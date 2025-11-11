import os
import json
import re
from typing import List, Dict, Optional, Any, Tuple

from services.llm_service import LLMService
from mcp_agents.email_agent.main import MCPAgent as EmailAgent
from mcp_agents.summary_agent.main import MCPAgent as SummaryAgent


class MCPAgent:
    def __init__(self):
        self.name = "文档审查智能体"
        self.llm_service = LLMService()
        self.email = EmailAgent()
        self.summary = SummaryAgent()
        self._preset_cache: Optional[List[Dict[str, Any]]] = None

    def _presets_dir(self) -> str:
        return os.path.join(os.path.dirname(__file__), "presets")

    def _load_presets(self) -> List[Dict[str, Any]]:
        if isinstance(self._preset_cache, list):
            return self._preset_cache
        presets: List[Dict[str, Any]] = []
        pdir = self._presets_dir()
        if not os.path.isdir(pdir):
            self._preset_cache = []
            return []
        for fn in os.listdir(pdir):
            if not fn.lower().endswith(".json"):
                continue
            path = os.path.join(pdir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    obj["id"] = obj.get("id") or os.path.splitext(fn)[0]
                    presets.append(obj)
            except Exception:
                continue
        self._preset_cache = presets
        return presets

    def list_presets(self) -> List[Dict[str, Any]]:
        return self._load_presets()

    def recommend_preset(self, requirements: Optional[str]) -> Optional[Dict[str, Any]]:
        req = (requirements or "").lower()
        presets = self._load_presets()
        if not presets:
            return None
        # 简单打分：keywords 命中数量 + target_types 提示词命中
        def score(p: Dict[str, Any]) -> int:
            s = 0
            kws = p.get("keywords") or []
            if isinstance(kws, list):
                for k in kws:
                    kstr = str(k).lower()
                    if kstr and kstr in req:
                        s += 2
            # 文件类型偏好提示词
            types = p.get("target_types") or []
            if isinstance(types, list):
                for t in types:
                    tstr = str(t).lower()
                    if tstr and tstr in req:
                        s += 1
            # 语言偏好
            lang = str(p.get("language") or "").lower()
            if lang and lang in req:
                s += 1
            return s

        best = None
        best_s = -1
        for p in presets:
            sc = score(p)
            if sc > best_s:
                best = p
                best_s = sc
        return best or presets[0]

    def _extract_texts(self, file_paths: List[str]) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for fp in file_paths:
            if not isinstance(fp, str) or not fp.strip():
                continue
            try:
                text = self.summary._extract_text(fp)  # 复用摘要智能体的提取逻辑
            except Exception:
                text = ""
            if text.strip():
                out.append((fp, text))
        return out

    def _build_review_prompt(self, template: str, requirements: Optional[str], file_name: str, content: str, idx: int) -> str:
        req_note = requirements or ""
        base = template or "请对以下文档进行审查，指出问题并给出改进建议。"
        return (
            f"{base}\n审查要求：{req_note}\n【第{idx}个附件：{file_name}】\n{content}"
        )

    def _run_review(self, prompts: List[str], language_hint: Optional[str]) -> List[str]:
        best_model = self.llm_service.get_best_model()
        if not best_model:
            return ["没有可用的大模型服务，无法执行审查。"]
        model_name = str(best_model)
        outs: List[str] = []
        for p in prompts:
            try:
                out = self.llm_service.query(model_name, p)
            except Exception:
                out = ""
            outs.append(str(out or "").strip())
        # 汇总为单一文本（分段标识）
        lang = (language_hint or "zh").lower()
        header = "【审查结果（中文）】" if lang.startswith("zh") else "【Review Results (English)】"
        return [header] + [o for o in outs if o]

    def review_attachments_for_message_ids(self, message_ids: List[str], requirements: Optional[str] = None, preset_id: Optional[str] = None) -> Dict[str, Any]:
        ids = [i for i in (message_ids or []) if isinstance(i, str) and i.strip()]
        if not ids:
            return {"ok": False, "error": "未提供有效的消息ID。"}
        # 读取预设提示词
        presets = self._load_presets()
        preset = None
        if preset_id:
            preset = next((p for p in presets if str(p.get("id")) == str(preset_id)), None)
        if not preset:
            preset = self.recommend_preset(requirements)
        template = str((preset or {}).get("template") or "")
        language_hint = str((preset or {}).get("language") or "zh")

        # 保存附件到临时目录
        tmp_root = os.path.join("/tmp", "d2_doc_review")
        os.makedirs(tmp_root, exist_ok=True)
        try:
            saved_paths = self.email.save_attachments_by_ids(ids, tmp_root)
        except Exception as e:
            return {"ok": False, "error": f"保存附件失败：{e}"}
        if not saved_paths:
            return {"ok": False, "error": "未找到可供审查的附件。"}

        # 提取文本并构造审查提示
        texts = self._extract_texts(saved_paths)
        if not texts:
            return {"ok": False, "error": "无法从附件中提取文本。"}

        prompts: List[str] = []
        for idx, (fp, t) in enumerate(texts, 1):
            prompts.append(self._build_review_prompt(template, requirements, os.path.basename(fp), t, idx))
        pieces = self._run_review(prompts, language_hint)
        review_text = "\n\n".join(pieces)
        return {"ok": True, "review_text": review_text, "files": saved_paths}

    def write_review_to_draft_reply(self, message_id: str, review_text: str, subject_prefix: Optional[str] = None) -> bool:
        if not isinstance(message_id, str) or not message_id.strip():
            return False
        try:
            return self.email.create_draft_reply_by_message_id(message_id, subject_prefix or "审查结果", review_text)
        except Exception:
            return False

    def review_by_index(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        unread_only: bool = True,
        index: int = 1,
        limit: int = 20,
        mailbox_name: Optional[str] = None,
        requirements: Optional[str] = None,
        preset_id: Optional[str] = None,
    ) -> str:
        if index <= 0:
            return "请以“审查第N封”格式指定要审查的邮件序号。"
        try:
            items = self.email.list_emails(start=start, end=end, mailbox_name=mailbox_name, unread_only=unread_only, limit=limit)
        except Exception as e:
            return f"抱歉，读取邮件时出现错误：{e}"
        if not items or index > len(items):
            return "序号不在范围内或没有找到邮件。"
        target = items[index - 1]
        msg_id_obj = target.get("id")
        if not isinstance(msg_id_obj, str) or not msg_id_obj.strip():
            return "无法定位该邮件的ID。"
        res = self.review_attachments_for_message_ids([msg_id_obj], requirements, preset_id)
        if not res.get("ok"):
            return f"审查失败：{res.get('error') or '未知错误'}"
        ok = self.write_review_to_draft_reply(msg_id_obj, str(res.get("review_text") or ""))
        return "已将审查结果写入该邮件的草稿。" if ok else "审查结果写入草稿失败。"