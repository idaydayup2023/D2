import os
import re
import zipfile
from typing import Optional, List
from xml.etree import ElementTree as ET

from services.llm_service import LLMService


class MCPAgent:
    def __init__(self):
        self.name = "会议纪要智能体"
        self.llm_service = LLMService()

    def _read_docx(self, path: str) -> str:
        # 优先使用 python-docx，如不可用则回退为解析 document.xml
        try:
            import docx  # type: ignore
            try:
                doc = docx.Document(path)
                paras: List[str] = []
                for p in getattr(doc, "paragraphs", []):
                    t = getattr(p, "text", "")
                    if isinstance(t, str) and t.strip():
                        paras.append(t.strip())
                return "\n".join(paras)
            except Exception:
                pass
        except Exception:
            pass

        try:
            with zipfile.ZipFile(path) as z:
                xml_bytes = z.read("word/document.xml")
                xml = xml_bytes.decode("utf-8", errors="ignore")
                root = ET.fromstring(xml)
                paras2: List[str] = []
                for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    texts = [t.text for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if t.text]
                    para = "".join(texts)
                    if para.strip():
                        paras2.append(para)
                return "\n".join(paras2)
        except Exception:
            return ""

    def _normalize_text(self, text: str) -> str:
        s = re.sub(r"\s+", " ", text or "").strip()
        s = re.sub(r"\n\s*\n+", "\n\n", s)
        return s

    def _build_system_prompt(self, language: str = "zh") -> str:
        lang = (language or "zh").lower()
        output_lang = "中文" if lang.startswith("zh") else "英文"
        return (
            f"你是一名资深会议秘书。请将提供的会议文本整理成{output_lang}的规范会议纪要。"
            "必须输出完整的纪要正文（非 JSON）。结构要求如下：\n"
            "- 会议主题\n- 会议时间\n- 参会人员\n- 会议目的\n- 议程\n"
            "- 讨论要点（3-10条）\n- 结论与决策\n- 待办事项（负责人+截止时间）\n- 风险与依赖\n- 后续安排\n\n"
            "若原始文本缺失部分信息，请合理标注“未提供”或“待确认”。保持条理清晰、语言简洁、格式整齐。"
        )

    def generate_minutes(
        self,
        input_text: Optional[str] = None,
        docx_path: Optional[str] = None,
        language: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        text_parts: List[str] = []
        if isinstance(docx_path, str) and docx_path.strip():
            docx_text = self._read_docx(docx_path)
            if docx_text.strip():
                text_parts.append(docx_text)
        if isinstance(input_text, str) and input_text.strip():
            text_parts.append(input_text)
        text = "\n\n".join(text_parts)
        if not text or not text.strip():
            return "未获取到可整理的会议文本，请提供文本内容或有效的 .docx 文件。"

        normalized = self._normalize_text(text)
        best_model = model or self.llm_service.get_best_model()
        if not best_model:
            return (
                "会议主题：待确认\n"
                "会议时间：待确认\n"
                "参会人员：待确认\n"
                "会议目的：待确认\n"
                "议程：待确认\n"
                "讨论要点：\n- （原文内容如下）\n\n"
                f"原文摘录：\n{normalized}\n\n"
                "结论与决策：待确认\n"
                "待办事项：待确认（请补充负责人与截止时间）\n"
                "风险与依赖：待确认\n"
                "后续安排：待确认"
            )

        model_name = str(best_model)
        sys_msg = self._build_system_prompt(language or "zh")
        prompt = (
            f"系统: {sys_msg}\n"
            f"用户: 请基于以下会议原始记录，输出整洁规范的会议纪要正文：\n\n{normalized}"
        )
        try:
            resp = self.llm_service.query(model_name, prompt)
        except Exception:
            resp = ""
        out = resp if isinstance(resp, str) else ""
        return out.strip() or normalized