import os
import uuid
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from main_agent.d2_main import D2Agent


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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title": "D2 助理"})


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