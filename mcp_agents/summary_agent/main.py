import os
import re
import zipfile
from typing import Optional, List
from xml.etree import ElementTree as ET

from services.llm_service import LLMService


class MCPAgent:
    def __init__(self):
        self.name = "文本摘要智能体"
        self.llm_service = LLMService()

    def _read_txt(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    def _read_docx(self, path: str) -> str:
        try:
            with zipfile.ZipFile(path) as z:
                xml_bytes = z.read("word/document.xml")
                xml = xml_bytes.decode("utf-8", errors="ignore")
                root = ET.fromstring(xml)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                paras: List[str] = []
                for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    texts = [t.text for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if t.text]
                    para = "".join(texts)
                    if para.strip():
                        paras.append(para)
                return "\n".join(paras)
        except Exception:
            return ""

    def _read_pdf(self, path: str) -> str:
        # 优先使用 PyPDF2，如不可用则尝试 Spotlight 文本属性（不保证一定有）
        try:
            import PyPDF2  # type: ignore
            try:
                reader = PyPDF2.PdfReader(path)
                parts: List[str] = []
                for page in getattr(reader, "pages", []):
                    t = page.extract_text() or ""
                    if t.strip():
                        parts.append(t)
                return "\n".join(parts)
            except Exception:
                pass
        except Exception:
            pass

        # Spotlight 元数据的文本内容（有些PDF会暴露；若无则返回空）
        try:
            import subprocess
            res = subprocess.run(["mdls", "-name", "kMDItemTextContent", path], capture_output=True, text=True)
            if res.returncode == 0:
                out = res.stdout.strip()
                # 形如: kMDItemTextContent = "...文本..."
                m = re.search(r"=\s*\"([\s\S]*)\"$", out)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return ""

    def _extract_text(self, file_path: str) -> str:
        p = (file_path or "").strip()
        if not p:
            return ""
        ext = os.path.splitext(p)[1].lower()
        if ext in (".txt", ".md", ".text"):
            return self._read_txt(p)
        if ext == ".docx":
            return self._read_docx(p)
        if ext == ".pdf":
            return self._read_pdf(p)
        # 其他扩展名尝试按文本读取
        return self._read_txt(p)

    def summarize(
        self,
        input_text: Optional[str] = None,
        file_path: Optional[str] = None,
        style: Optional[str] = None,
        length: Optional[str] = None,
        language: Optional[str] = None,
        output_format: Optional[str] = None,
    ) -> str:
        """
        对文本或文档内容进行摘要。
        - input_text: 直接传入的文本内容
        - file_path: 本地文件路径（支持 .txt/.md/.docx/.pdf）
        - style: 摘要风格，如 "要点式"、"学术风"、"商业风" 等
        - length: 长度偏好，如 "简短"、"详细"、"中等" 等
        - language: 输出语言，如 "zh" 或 "en"。默认 "zh"
        - output_format: 输出格式，可选 "bullets"（要点）、"outline"（结构化大纲）、
          "conclusion_recommendations"（结论+建议三段式）。
        """
        text = ""
        if isinstance(file_path, str) and file_path.strip():
            text = self._extract_text(file_path)
        if not text.strip() and isinstance(input_text, str):
            text = input_text
        if not text or not text.strip():
            return "未获取到可摘要的文本内容。请提供文本或有效的文件路径。"

        best_model = self.llm_service.get_best_model()
        if not best_model:
            return "没有可用的大模型服务，无法生成摘要。"
        model_name = str(best_model)

        # 适度分段，避免上下文过长（字符粗略估算）
        normalized = re.sub(r"\s+", " ", text).strip()
        chunk_size = 5000  # 可按需调整
        chunks: List[str] = [normalized[i:i + chunk_size] for i in range(0, len(normalized), chunk_size)]

        lang = (language or "zh").lower()
        style_note = f"风格偏好：{style}。" if style else ""
        length_note = f"长度偏好：{length}。" if length else ""
        output_lang = "中文" if lang.startswith("zh") else "英文"

        # 归一化输出格式
        fmt_raw = (output_format or "").strip().lower()
        fmt: Optional[str]
        if any(k in fmt_raw for k in ["bullet", "bullets", "list", "要点", "条目", "关键点"]):
            fmt = "bullets"
        elif any(k in fmt_raw for k in ["outline", "大纲", "结构化大纲", "纲要"]):
            fmt = "outline"
        elif any(k in fmt_raw for k in ["conclusion", "recommendation", "建议", "结论", "三段"]):
            fmt = "conclusion_recommendations"
        else:
            fmt = None

        # 逐段摘要
        chunk_summaries: List[str] = []
        for idx, ch in enumerate(chunks, 1):
            base = f"请以{output_lang}为以下文本生成摘要，{style_note}{length_note}"
            if fmt == "bullets":
                req = (
                    "要求：\n- 使用条目符号列出要点（每条不超过一句）"
                    "\n- 保留关键事实与数字\n- 结构清晰，避免冗长\n\n"
                )
            elif fmt == "outline":
                req = (
                    "要求：\n- 产出结构化大纲（至少两级：一级要点、二级要点）"
                    "\n- 每级要点简洁清晰\n- 保留关键事实与数字\n\n"
                )
            elif fmt == "conclusion_recommendations":
                req = (
                    "要求：\n- 先给出本段的结论（1-2句）"
                    "\n- 再给出建议/行动项（2-5条）\n- 如有关键数据，简要列出\n\n"
                )
            else:
                req = (
                    "要求：\n- 保留关键事实与数字\n- 提炼核心结论\n- 结构清晰，避免冗长\n\n"
                )
            prompt = base + req + f"【第 {idx} 段内容】\n{ch}"
            try:
                seg = self.llm_service.query(model_name, prompt)
            except Exception:
                seg = ""
            if isinstance(seg, str) and seg.strip():
                chunk_summaries.append(seg.strip())

        # 汇总摘要
        if len(chunk_summaries) == 1:
            return chunk_summaries[0]

        combined = "\n\n".join(chunk_summaries)
        if fmt == "bullets":
            final_prompt = (
                f"以下是多段摘要，请综合成一份{output_lang}最终摘要，{style_note}{length_note}"
                "要求：\n- 先给出总体结论（1-2句）\n- 列出3-10条要点，简洁明了\n- 如需补充过程与备注，简短说明\n\n"
                f"【分段摘要】\n{combined}"
            )
        elif fmt == "outline":
            final_prompt = (
                f"以下是多段摘要，请综合成一份{output_lang}结构化大纲，{style_note}{length_note}"
                "要求：\n- 先给出总体结论\n- 输出分层大纲（至少两级）\n- 要点简洁清晰，保留关键事实与数字\n\n"
                f"【分段摘要】\n{combined}"
            )
        elif fmt == "conclusion_recommendations":
            final_prompt = (
                f"以下是多段摘要，请综合成一份{output_lang}最终摘要，{style_note}{length_note}"
                "要求：\n- 第一部分：总体结论（2-4句）\n- 第二部分：建议/行动项（3-7条）\n- 第三部分：关键要点或数据（可选，2-6条）\n\n"
                f"【分段摘要】\n{combined}"
            )
        else:
            final_prompt = (
                f"以下是多段摘要，请综合成一份{output_lang}最终摘要，{style_note}{length_note}"
                "要求：\n- 先给出总体结论\n- 列出3-7条要点\n- 如有过程与建议，单独小节说明\n\n"
                f"【分段摘要】\n{combined}"
            )
        try:
            final = self.llm_service.query(model_name, final_prompt)
        except Exception:
            final = combined
        return final if isinstance(final, str) else combined