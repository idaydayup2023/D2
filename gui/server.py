import os
import uuid
from typing import Optional, cast
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from main_agent.d2_main import D2Agent
from mcp_agents.meeting_minutes_agent.main import MCPAgent as MeetingMinutesAgent


BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="D2 助理 GUI")
agent = D2Agent()

# 可选静态资源目录（目前主要使用 CDN 样式）
static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

upload_dir = os.path.join(BASE_DIR, "uploads")
os.makedirs(upload_dir, exist_ok=True)

# 简洁 SVG 图标与 /favicon 兼容处理
FAVICON_SVG = (
    """
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
  <defs>
    <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0' stop-color='#6366F1'/>
      <stop offset='1' stop-color='#EC4899'/>
    </linearGradient>
  </defs>
  <rect x='4' y='4' width='56' height='56' rx='12' fill='url(#g)'/>
  <text x='50%' y='55%' text-anchor='middle' font-family='system-ui, -apple-system, Segoe UI, Roboto, Arial' font-size='28' font-weight='700' fill='white'>D2</text>
</svg>
    """
).strip()


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    # 若存在静态目录中的 ico 文件则优先返回
    try:
        path = os.path.join(static_dir, "favicon.ico")
        if os.path.isfile(path):
            return FileResponse(path, media_type="image/x-icon")
    except Exception:
        pass
    # 否则返回 204，避免 404 噪音；浏览器会使用 /favicon.svg
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title": "D2 助理"})


# 兼容 IDE/Vite 开发客户端在 WebView 中自动注入的心跳脚本，避免 404 噪音
@app.get("/@vite/client")
async def vite_client_stub():
    return Response(content="/* Vite client disabled in this server */", media_type="application/javascript")


@app.get("/api/health")
async def health():
    return {"ok": True, "name": agent.name}


@app.post("/api/ask")
async def ask(payload: dict):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return JSONResponse({"ok": False, "error": "请输入内容"}, status_code=400)
    result = agent.process_prompt(prompt)
    return {"ok": True, "response": result}


@app.post("/api/dialog")
async def dialog(
    prompt: str = Form(None),
    attachments: list[UploadFile] = File(None),
    backend: str = Form(None),
    language: str = Form(None),
    model: str = Form(None),
):
    """
    统一对话入口：
    - 接收用户指令 `prompt`
    - 可选接收多个附件 `attachments`（.docx / 音频 / 文本）
    - 自动解析附件为上下文并并入对话，由主代理进行语义路由
    """
    user_prompt = (prompt or "").strip()
    if not user_prompt and not attachments:
        return JSONResponse({"ok": False, "error": "请输入内容或添加附件"}, status_code=400)

    context_parts: list[str] = []

    def _safe_join(parts: list[str]) -> str:
        return "\n\n".join([p for p in parts if isinstance(p, str) and p.strip()])

    # 处理附件
    if attachments:
        for f in attachments:
            try:
                ext = os.path.splitext(f.filename or "")[1].lower()
                fname = f"{uuid.uuid4().hex}{ext or ''}"
                path = os.path.join(upload_dir, fname)
                content = await f.read()
                with open(path, "wb") as out:
                    out.write(content)

                if ext in {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".webm"}:
                    # 音频转写
                    try:
                        stt = agent.stt_service.transcribe(
                            path,
                            backend=backend,
                            model=model,
                            language=language,
                            prompt=None,
                        )
                        if bool(stt.get("ok")):
                            text = stt.get("text") or ""
                            context_parts.append(f"【附件：音频转写】\n文件：{f.filename}\n\n{text}")
                        else:
                            err = stt.get("error") or "转写失败"
                            context_parts.append(f"【附件：音频转写失败】\n文件：{f.filename}\n错误：{err}")
                    except Exception as e:
                        context_parts.append(f"【附件：音频处理异常】\n文件：{f.filename}\n错误：{e}")

                elif ext == ".docx":
                    # 提取 docx 文本
                    doc_text = ""
                    try:
                        try:
                            from docx import Document  # type: ignore
                            doc = Document(path)
                            doc_text = "\n".join([p.text for p in doc.paragraphs if p.text])
                        except Exception:
                            # 兜底：读取 document.xml
                            import zipfile
                            import re
                            with zipfile.ZipFile(path, "r") as zf:
                                with zf.open("word/document.xml") as xf:
                                    raw = xf.read().decode("utf-8", errors="ignore")
                                    doc_text = re.sub(r"<[^>]+>", "\n", raw)
                                    doc_text = re.sub(r"\n+", "\n", doc_text).strip()
                    except Exception as e:
                        context_parts.append(f"【附件：Docx解析失败】\n文件：{f.filename}\n错误：{e}")
                    else:
                        context_parts.append(f"【附件：Docx内容】\n文件：{f.filename}\n\n{doc_text}")

                elif ext in {".txt", ".md"}:
                    try:
                        text = content.decode("utf-8", errors="ignore")
                        context_parts.append(f"【附件：文本内容】\n文件：{f.filename}\n\n{text}")
                    except Exception:
                        context_parts.append(f"【附件：文本解析失败】\n文件：{f.filename}")
                else:
                    context_parts.append(f"【附件：已接收但暂不解析】\n文件：{f.filename}")
            except Exception as e:
                context_parts.append(f"【附件处理异常】文件：{getattr(f, 'filename', '未知')}\n错误：{e}")

    combined_prompt = _safe_join([
        (f"用户指令：\n{user_prompt}" if user_prompt else "用户未提供明确指令。"),
        (f"附件上下文：\n{_safe_join(context_parts)}" if context_parts else ""),
        "请基于以上信息，自动选择合适的工具或代理完成任务。",
    ])

    try:
        result = agent.process_prompt(combined_prompt)
        # 对回答进行口语化润色，改善对话窗口呈现效果
        try:
            result = agent.polish_text(result, style='spoken_zh')
        except Exception:
            pass
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"处理对话失败：{e}"}, status_code=500)

    return {"ok": True, "response": result}


@app.post("/api/upload_audio")
async def upload_audio(
    file: UploadFile = File(...),
    backend: str = Form(None),
    language: str = Form(None),
    model: str = Form(None),
    prompt: str = Form(None),
):
    # 校验扩展名
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".webm"}
    if ext not in allowed:
        return JSONResponse({"ok": False, "error": f"不支持的音频格式：{ext or '未知'}"}, status_code=400)

    # 保存到本地 uploads 目录
    fname = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(upload_dir, fname)
    try:
        content = await file.read()
        with open(path, "wb") as f:
            f.write(content)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"保存文件失败：{e}"}, status_code=500)

    # 调用 STT 转写
    try:
        res = agent.stt_service.transcribe(
            path,
            backend=backend,
            model=model,
            language=language,
            prompt=prompt,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"调用转写失败：{e}"}, status_code=500)

    return {
        "ok": bool(res.get("ok")),
        "backend": res.get("backend"),
        "response": res.get("text") or "",
        "error": res.get("error"),
        "filename": file.filename,
    }


@app.post("/api/meeting_minutes")
async def meeting_minutes(
    text: str = Form(None),
    docx_file: UploadFile = File(None),
    audio_file: UploadFile = File(None),
    backend: str = Form(None),
    language: str = Form(None),
    model: str = Form(None),
    prompt: str = Form(None),
):
    # 收集文本与文件
    input_text = (text or "").strip()
    docx_path = None
    audio_text = ""

    # 处理 Docx 文件
    if docx_file is not None:
        ext = os.path.splitext(docx_file.filename or "")[1].lower()
        if ext != ".docx":
            return JSONResponse({"ok": False, "error": f"不支持的文档格式：{ext or '未知'}（仅支持 .docx）"}, status_code=400)
        fname = f"{uuid.uuid4().hex}{ext}"
        docx_path = os.path.join(upload_dir, fname)
        try:
            content = await docx_file.read()
            with open(docx_path, "wb") as f:
                f.write(content)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"保存文档失败：{e}"}, status_code=500)

    # 处理音频文件（转写）
    if audio_file is not None:
        ext = os.path.splitext(audio_file.filename or "")[1].lower()
        allowed = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".webm"}
        if ext not in allowed:
            return JSONResponse({"ok": False, "error": f"不支持的音频格式：{ext or '未知'}"}, status_code=400)
        afname = f"{uuid.uuid4().hex}{ext}"
        apath = os.path.join(upload_dir, afname)
        try:
            acontent = await audio_file.read()
            with open(apath, "wb") as f:
                f.write(acontent)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"保存音频失败：{e}"}, status_code=500)

        try:
            stt = agent.stt_service.transcribe(
                apath,
                backend=backend,
                model=model,
                language=language,
                prompt=prompt,
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"调用转写失败：{e}"}, status_code=500)

        if bool(stt.get("ok")):
            audio_text = stt.get("text") or ""
        else:
            return JSONResponse({"ok": False, "error": stt.get("error") or "转写失败"}, status_code=500)

    # 组合文本
    combined = "\n\n".join([s for s in [input_text, audio_text] if isinstance(s, str) and s.strip()])
    minutes_agent_fn = getattr(agent, "_find_meeting_minutes_agent", None)
    mm: Optional[MeetingMinutesAgent] = None
    if callable(minutes_agent_fn):
        try:
            mm = cast(MeetingMinutesAgent, minutes_agent_fn())
        except Exception:
            mm = None

    if mm is None:
        # Fallback: 尝试直接实例化以避免加载失败
        try:
            mm = MeetingMinutesAgent()
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"未加载到会议纪要智能体：{e}"}, status_code=500)

    try:
        result = cast(MeetingMinutesAgent, mm).generate_minutes(
            input_text=combined,
            docx_path=docx_path,
            language=language or "zh",
            model=model,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"生成会议纪要失败：{e}"}, status_code=500)

    return {"ok": True, "response": result}