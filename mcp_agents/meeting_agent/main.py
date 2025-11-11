import json
from typing import Optional, List, Dict, Any

from services.llm_service import LLMService
from mcp_agents.email_agent.main import MCPAgent as EmailAgent
from mcp_agents.summary_agent.main import MCPAgent as SummaryAgent
from mcp_agents.calendar_agent.main import MCPAgent as CalendarAgent


class MCPAgent:
    def __init__(self):
        self.name = "会议处理智能体"
        self.llm_service = LLMService()
        self.email = EmailAgent()
        self.summary = SummaryAgent()
        self.calendar = CalendarAgent()

    def _parse_meeting_with_llm(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """调用本地LLM，将邮件元数据+正文解析为会议JSON。失败返回空字典。"""
        best_model = self.llm_service.get_best_model()
        if not best_model:
            return {}
        sys_msg = (
            "你是一个会议解析器。根据提供的邮件元信息与正文，识别是否是会议通知，"
            "并输出一个严格的 JSON: {is_meeting:boolean, title:string|null, start:string|null, end:string|null, location:string|null, attendees:string[]|null, includes_me:boolean}."
            "时间请尽量用本地时区并输出 ISO 格式: YYYY-MM-DDTHH:MM:SS。若无法确定则填 null。"
            "includes_me 为 true 的条件是: 参会人员或收件人/抄送中包含本机账户地址之一（将提供）。"
        )
        try:
            raw = self.llm_service.query(str(best_model), f"系统: {sys_msg}\n用户: {json.dumps(meta, ensure_ascii=False)}")
        except Exception:
            return {}
        text = "" if raw is None else str(raw)
        try:
            import re
            m = re.search(r"\{[\s\S]*\}", text)
            payload = m.group(0) if m else text
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def process_meetings(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        unread_only: bool = True,
        limit: int = 20,
        mailbox_name: Optional[str] = None,
    ) -> str:
        """
        收取邮件、生成摘要、识别会议信息；如参会人包含本机账户则自动加入日程。
        返回用户可读的中文汇总。
        """
        try:
            items = self.email.list_emails(
                start=start,
                end=end,
                mailbox_name=mailbox_name,
                unread_only=unread_only,
                limit=limit,
            )
        except Exception as e:
            return f"抱歉，读取邮件时出现错误：{e}"
        if not items:
            return "没有找到符合条件的邮件。"

        ids: List[str] = []
        for it in items:
            mid = it.get("id")
            if isinstance(mid, str) and mid.strip():
                ids.append(mid)
        try:
            details = self.email.get_messages_by_ids(ids)
        except Exception as e:
            return f"抱歉，读取邮件正文时出现错误：{e}"

        try:
            my_addrs = self.email.get_account_addresses()
        except Exception:
            my_addrs = []

        lines: List[str] = []
        auto_added = 0
        for i, d in enumerate(details, 1):
            subj = d.get("subject") or "(无主题)"
            sender = d.get("sender") or ""
            date_s = d.get("date") or ""
            mailbox = d.get("mailbox") or ""
            read = d.get("read")
            tag = "未读" if read is False else "已读"
            to_addrs = d.get("to") or []
            cc_addrs = d.get("cc") or []
            body = d.get("body") or ""

            # 要点式摘要
            try:
                summary = self.summary.summarize(
                    input_text=body,
                    file_path=None,
                    style="信息型",
                    length="简短",
                    language="zh",
                    output_format="bullets",
                )
            except Exception:
                summary = "(摘要生成失败)"

            meta = {
                "subject": subj,
                "sender": sender,
                "to": to_addrs,
                "cc": cc_addrs,
                "my_addresses": my_addrs,
                "body": body,
            }
            meeting_json = self._parse_meeting_with_llm(meta)

            is_meeting = bool(meeting_json.get("is_meeting")) if isinstance(meeting_json, dict) else False
            includes_me = bool(meeting_json.get("includes_me")) if isinstance(meeting_json, dict) else False
            title = meeting_json.get("title") if isinstance(meeting_json.get("title"), str) else None
            start_i = meeting_json.get("start") if isinstance(meeting_json.get("start"), str) else None
            end_i = meeting_json.get("end") if isinstance(meeting_json.get("end"), str) else None
            location = meeting_json.get("location") if isinstance(meeting_json.get("location"), str) else None
            attendees = meeting_json.get("attendees") if isinstance(meeting_json.get("attendees"), list) else []

            header = f"{i}. {subj} | {sender} | {date_s} | {mailbox} | {tag}"
            lines.append(header)
            lines.append(f"摘要(要点)：\n{summary}")

            if is_meeting:
                lines.append("会议信息：")
                lines.append(f"- 标题: {title or '(未知)'}")
                lines.append(f"- 开始: {start_i or '(未知)'}")
                lines.append(f"- 结束: {end_i or '(未知)'}")
                lines.append(f"- 地点: {location or '(未知)'}")
                lines.append(f"- 参会: {', '.join(attendees) if attendees else '(未知)'}")

                # 自动入日历：缺失 end 时，默认 +1 小时
                if includes_me and isinstance(start_i, str) and start_i.strip():
                    end_for_event = end_i if isinstance(end_i, str) and end_i.strip() else start_i
                    try:
                        ok = self.calendar.create_event(
                            title=title or "会议",
                            start=start_i,
                            end=end_for_event,
                            location=location or None,
                        )
                    except Exception:
                        ok = False
                    if ok:
                        auto_added += 1
                        lines.append("- 已自动加入到日程。")
                    else:
                        lines.append("- 自动加入失败，请确认后手动加入。")
                else:
                    lines.append("- 是否加入日程？请回复“加入第" + str(i) + "封”。")
            else:
                lines.append("不是会议通知。")
            lines.append("")

        suffix = "\n提示：如需加入，请回复“加入第N封”。"
        if auto_added:
            suffix += f"\n已自动加入 {auto_added} 个会议到日历。"
        return "以下为邮件摘要与会议识别：\n" + "\n".join(lines) + suffix

    def join_by_index(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        unread_only: bool = True,
        index: int = 1,
        limit: int = 20,
        mailbox_name: Optional[str] = None,
    ) -> str:
        """根据索引抓取会议邮件并加入到日历。"""
        if index <= 0:
            return "请以“加入第N封”格式指定要加入的邮件序号。"
        try:
            items = self.email.list_emails(
                start=start,
                end=end,
                mailbox_name=mailbox_name,
                unread_only=unread_only,
                limit=limit,
            )
        except Exception as e:
            return f"抱歉，读取邮件时出现错误：{e}"
        if not items or index > len(items):
            return "序号不在范围内或没有找到邮件。"
        target = items[index - 1]
        msg_id_obj = target.get("id")
        if not isinstance(msg_id_obj, str) or not msg_id_obj.strip():
            return "无法定位该邮件的ID。"
        try:
            detail_list = self.email.get_messages_by_ids([msg_id_obj])
        except Exception as e:
            return f"抱歉，读取该邮件正文时出现错误：{e}"
        if not detail_list:
            return "无法读取该邮件详情。"
        d = detail_list[0]
        subj = d.get("subject") or "会议"
        body = d.get("body") or ""
        to_addrs = d.get("to") or []
        cc_addrs = d.get("cc") or []
        try:
            my_addrs = self.email.get_account_addresses()
        except Exception:
            my_addrs = []
        meta = {
            "subject": subj,
            "sender": d.get("sender") or "",
            "to": to_addrs,
            "cc": cc_addrs,
            "my_addresses": my_addrs,
            "body": body,
        }
        meeting_json = self._parse_meeting_with_llm(meta)
        is_meeting = bool(meeting_json.get("is_meeting")) if isinstance(meeting_json, dict) else False
        title = meeting_json.get("title") if isinstance(meeting_json.get("title"), str) else None
        start_i = meeting_json.get("start") if isinstance(meeting_json.get("start"), str) else None
        end_i = meeting_json.get("end") if isinstance(meeting_json.get("end"), str) else None
        location = meeting_json.get("location") if isinstance(meeting_json.get("location"), str) else None
        if not is_meeting or not (isinstance(start_i, str) and start_i.strip()):
            return "该邮件未识别为会议或缺少时间信息，无法加入。"
        end_for_event = end_i if isinstance(end_i, str) and end_i.strip() else start_i
        try:
            ok = self.calendar.create_event(
                title=title or subj,
                start=start_i,
                end=end_for_event,
                location=location or None,
            )
        except Exception as e:
            return f"抱歉，创建日历事件时出现错误：{e}"
        return "已加入该会议到日程。" if ok else "加入失败。"