import os
import importlib
import sys
from datetime import datetime, timedelta, date, time
import json
import re
from typing import Optional, Dict, Any

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from services.llm_service import LLMService
from services.stt_service import STTService

class D2Agent:
    def __init__(self):
        self.name = "D2"
        self.mcp_agents = []
        self.llm_service = LLMService()
        self.stt_service = STTService()
        self.preferences: Dict[str, Any] = {}
        self.load_mcp_agents()

    def update_preferences(self, default_mailbox_name: Optional[str] = None, default_calendar_name: Optional[str] = None, meeting_only_with_attachments: Optional[bool] = None, unread_only: Optional[bool] = None):
        if isinstance(default_mailbox_name, str) and default_mailbox_name.strip():
            self.preferences['default_mailbox_name'] = default_mailbox_name.strip()
        if isinstance(default_calendar_name, str) and default_calendar_name.strip():
            self.preferences['default_calendar_name'] = default_calendar_name.strip()
        if isinstance(meeting_only_with_attachments, bool):
            self.preferences['meeting_only_with_attachments'] = meeting_only_with_attachments
        if isinstance(unread_only, bool):
            self.preferences['unread_only'] = unread_only

    def _find_calendar_agent(self):
        for agent in self.mcp_agents:
            if getattr(agent, 'name', '').startswith('日历'):
                return agent
        return None

    def _find_email_agent(self):
        for agent in self.mcp_agents:
            if getattr(agent, 'name', '').startswith('邮件'):
                return agent
        return None

    def _find_summary_agent(self):
        for agent in self.mcp_agents:
            name = getattr(agent, 'name', '')
            if name.startswith('文本摘要') or name.startswith('摘要'):
                return agent
        return None

    def _find_meeting_agent(self):
        for agent in self.mcp_agents:
            if getattr(agent, 'name', '').startswith('会议'):
                return agent
        return None

    def _find_meeting_minutes_agent(self):
        for agent in self.mcp_agents:
            if getattr(agent, 'name', '').startswith('会议纪要'):
                return agent
        return None

    def _find_doc_review_agent(self):
        for agent in self.mcp_agents:
            name = getattr(agent, 'name', '')
            if name.startswith('文档审查') or name.startswith('审查'):
                return agent
        return None

    def _resolve_range_from_prompt(self, prompt: str):
        p = prompt.strip().lower()
        today = date.today()
        now_dt = datetime.now()
        mentions_remaining = any(k in p for k in ['还有', '剩余', '剩下'])

        if '本周' in p:
            # 本周：周一到周日；若含“还有/剩余/剩下”，则从现在到周末
            monday = today - timedelta(days=today.weekday())
            sunday = monday + timedelta(days=6)
            start_dt = now_dt if mentions_remaining else datetime.combine(monday, time(0, 0, 0))
            end_dt = datetime.combine(sunday, time(23, 59, 0))
        elif '今天' in p:
            # 今天：若含“还有/剩余/剩下”，从现在到今日结束
            start_dt = now_dt if mentions_remaining else datetime.combine(today, time(0, 0, 0))
            end_dt = datetime.combine(today, time(23, 59, 0))
        elif '明天' in p:
            # 明天：整天
            tmr = today + timedelta(days=1)
            start_dt = datetime.combine(tmr, time(0, 0, 0))
            end_dt = datetime.combine(tmr, time(23, 59, 0))
        else:
            # 未明确则默认今天（非剩余），可根据需要扩展更多自然语言
            start_dt = datetime.combine(today, time(0, 0, 0))
            end_dt = datetime.combine(today, time(23, 59, 0))
        return start_dt.strftime('%Y-%m-%dT%H:%M:%S'), end_dt.strftime('%Y-%m-%dT%H:%M:%S')

    def _maybe_route_to_calendar(self, prompt: str):
        """若命中日历意图则调用日历智能体并返回文本；否则返回 None。"""
        # 快路径：对常见的日历查询先用关键词路由，避免不必要的 LLM 调用带来的延迟
        try:
            cal_fast_keywords = ['日程', '日历', '安排', '事件']  # 排除 '会议' 以减少语义冲突
            email_mentions = ['邮件', '邮箱', '收件箱']
            if any(k in prompt for k in cal_fast_keywords) and not any(k in prompt for k in email_mentions):
                cal = self._find_calendar_agent()
                if cal:
                    start_iso, end_iso = self._resolve_range_from_prompt(prompt)
                    # 若未明确时间关键词，则默认使用“本周起至未来窗口”的范围
                    p = prompt.strip().lower()
                    if not any(k in p for k in ['今天', '明天', '本周']):
                        today = date.today()
                        monday = today - timedelta(days=today.weekday())
                        horizon_days = getattr(cal, '_default_horizon_days', 14)
                        start_iso = datetime.combine(monday, time(0, 0, 0)).strftime('%Y-%m-%dT%H:%M:%S')
                        end_iso = datetime.combine(monday + timedelta(days=horizon_days), time(23, 59, 0)).strftime('%Y-%m-%dT%H:%M:%S')
                    try:
                        events = cal.get_events(start=start_iso, end=end_iso, calendar_name=self.preferences.get('default_calendar_name'))
                    except Exception as e:
                        return f"抱歉，读取日历时出现错误：{e}"
                    if not events:
                        return "该时间范围内没有找到日程。"
                    lines = []
                    for i, ev in enumerate(events, 1):
                        title = ev.get('title') or '未命名事件'
                        st = ev.get('start') or ''
                        ed = ev.get('end') or ''
                        loc = ev.get('location') or ''
                        line = f"{i}. {title} | {st} → {ed}" + (f" | {loc}" if loc else '')
                        lines.append(line)
                    return "以下是您的日程：\n" + "\n".join(lines)
        except Exception:
            # 快路径异常不影响后续 LLM 路由
            pass

        # 优先使用大模型语义解析来路由
        parsed = self._llm_semantic_parse(prompt)
        if parsed and parsed.get('route') in ('calendar', 'email', 'summary', 'stt'):
            if parsed.get('route') == 'calendar':
                cal = self._find_calendar_agent()
                if not cal:
                    return "抱歉，未加载到日历智能体。"
                action = parsed.get('action')
                if action == 'list':
                    start_iso_raw = parsed.get('start')
                    end_iso_raw = parsed.get('end')
                    cal_name_raw = parsed.get('calendar_name')
                    # 将可能的 Any/None 收敛为 Optional[str]
                    start_iso = start_iso_raw if isinstance(start_iso_raw, str) else None
                    end_iso = end_iso_raw if isinstance(end_iso_raw, str) else None
                    cal_name = cal_name_raw if isinstance(cal_name_raw, str) and cal_name_raw.strip() else None
                    try:
                        cal_name_eff = cal_name or self.preferences.get('default_calendar_name')
                        events = cal.get_events(start=start_iso, end=end_iso, calendar_name=cal_name_eff)
                    except Exception as e:
                        return f"抱歉，读取日历时出现错误：{e}"
                    if not events:
                        return "该时间范围内没有找到日程。"
                    lines = []
                    for i, ev in enumerate(events, 1):
                        title = ev.get('title') or '未命名事件'
                        st = ev.get('start') or ''
                        ed = ev.get('end') or ''
                        loc = ev.get('location') or ''
                        line = f"{i}. {title} | {st} → {ed}" + (f" | {loc}" if loc else '')
                        lines.append(line)
                    return "以下是您的日程：\n" + "\n".join(lines)
                elif action == 'create':
                    title_raw = parsed.get('title')
                    start_iso_raw = parsed.get('start')
                    end_iso_raw = parsed.get('end')
                    location_raw = parsed.get('location')
                    cal_name_raw = parsed.get('calendar_name')
                    title = title_raw if isinstance(title_raw, str) and title_raw.strip() else None
                    start_iso = start_iso_raw if isinstance(start_iso_raw, str) else None
                    end_iso = end_iso_raw if isinstance(end_iso_raw, str) else None
                    location = location_raw if isinstance(location_raw, str) and location_raw.strip() else None
                    cal_name = cal_name_raw if isinstance(cal_name_raw, str) and cal_name_raw.strip() else None
                    try:
                        ok = cal.create_event(title=title, start=start_iso, end=end_iso, location=location, calendar_name=cal_name)
                    except Exception as e:
                        return f"抱歉，创建日历事件时出现错误：{e}"
                    return "已创建事件。" if ok else "事件创建失败。"
                else:
                    # 未指定动作，回退到模型回答
                    return None
            elif parsed.get('route') == 'summary':
                sa = self._find_summary_agent()
                if not sa:
                    return "抱歉，未加载到摘要智能体。"
                action = parsed.get('action')
                if action in ('summarize', 'summary'):
                    text_raw = parsed.get('summary_text')
                    file_raw = parsed.get('summary_file_path')
                    style_raw = parsed.get('summary_style')
                    length_raw = parsed.get('summary_length')
                    lang_raw = parsed.get('summary_language')
                    fmt_raw = parsed.get('summary_format')
                    text = text_raw if isinstance(text_raw, str) and text_raw.strip() else None
                    file_path = file_raw if isinstance(file_raw, str) and file_raw.strip() else None
                    style = style_raw if isinstance(style_raw, str) and style_raw.strip() else None
                    length = length_raw if isinstance(length_raw, str) and length_raw.strip() else None
                    language = lang_raw if isinstance(lang_raw, str) and lang_raw.strip() else None
                    fmt = fmt_raw if isinstance(fmt_raw, str) and fmt_raw.strip() else None
                    try:
                        out = sa.summarize(input_text=text, file_path=file_path, style=style, length=length, language=language, output_format=fmt)
                    except Exception as e:
                        return f"抱歉，生成摘要时出现错误：{e}"
                    return out
                else:
                    return None
            elif parsed.get('route') == 'stt':
                action = parsed.get('action')
                if action in ('transcribe', 'stt'):
                    audio_raw = parsed.get('audio_file_path')
                    backend_raw = parsed.get('stt_backend')
                    model_raw = parsed.get('stt_model')
                    lang_raw = parsed.get('stt_language')
                    audio_path = audio_raw if isinstance(audio_raw, str) and audio_raw.strip() else None
                    backend = backend_raw if isinstance(backend_raw, str) and backend_raw.strip() else None
                    model = model_raw if isinstance(model_raw, str) and model_raw.strip() else None
                    language = lang_raw if isinstance(lang_raw, str) and lang_raw.strip() else None
                    if not audio_path:
                        return "请提供本地音频文件路径用于转写。"
                    res = self.stt_service.transcribe(audio_path=audio_path, backend=backend, model=model, language=language)
                    if not res.get('ok'):
                        return f"语音转文字失败：{res.get('error') or '未知错误'}"
                    text = str(res.get('text') or '').strip()
                    used = res.get('backend') or 'unknown'
                    return f"[STT:{used}] 转写结果：\n{text}"
                else:
                    return None
            else:
                # Email 路由
                em = self._find_email_agent()
                if not em:
                    return "抱歉，未加载到邮件智能体。"
                action = parsed.get('action')
                # 文档审查：审查附件并将结果写入草稿
                if action == 'review_attachments':
                    dra = self._find_doc_review_agent()
                    if not dra:
                        return "抱歉，未加载到文档审查智能体。"
                    ids_raw = parsed.get('email_ids')
                    req_raw = parsed.get('review_requirements')
                    preset_raw = parsed.get('review_preset_id')
                    def _to_list_str(x):
                        if isinstance(x, list):
                            return [str(v) for v in x if isinstance(v, (str, int)) and str(v).strip()]
                        if isinstance(x, (str, int)):
                            s = str(x)
                            if "," in s:
                                return [t.strip() for t in s.split(",") if t.strip()]
                            return [s] if s.strip() else []
                        return []
                    ids = _to_list_str(ids_raw)
                    req_text = req_raw if isinstance(req_raw, str) and req_raw.strip() else None
                    preset_id = preset_raw if isinstance(preset_raw, str) and preset_raw.strip() else None
                    if not ids:
                        # 如未给定ID，则先列出邮件供用户按序号选择
                        start_iso_raw = parsed.get('start')
                        end_iso_raw = parsed.get('end')
                        unread_only_raw = parsed.get('unread_only')
                        limit_raw = parsed.get('limit')
                        start_iso = start_iso_raw if isinstance(start_iso_raw, str) else None
                        end_iso = end_iso_raw if isinstance(end_iso_raw, str) else None
                        unread_only = bool(unread_only_raw) if isinstance(unread_only_raw, (bool, int)) else True
                        try:
                            limit = int(limit_raw) if isinstance(limit_raw, (int, str)) and str(limit_raw).isdigit() else 20
                        except Exception:
                            limit = 20
                        try:
                            items = em.list_emails(start=start_iso, end=end_iso, unread_only=unread_only, limit=limit)
                        except Exception as e:
                            return f"抱歉，读取邮件时出现错误：{e}"
                        if not items:
                            return "没有找到符合条件的邮件。"
                        lines = []
                        for i, it in enumerate(items, 1):
                            subj = it.get('subject') or '(无主题)'
                            sender = it.get('sender') or ''
                            date_s = it.get('date') or ''
                            mailbox = it.get('mailbox') or ''
                            read = it.get('read')
                            tag = '未读' if read is False else '已读'
                            lines.append(f"{i}. {subj} | {sender} | {date_s} | {mailbox} | {tag}")
                        return "以下是匹配的邮件（回复“审查第N封”开始审查其附件）：\n" + "\n".join(lines)
                    # 执行审查并写入草稿
                    try:
                        res = dra.review_attachments_for_message_ids(ids, requirements=req_text, preset_id=preset_id)
                    except Exception as e:
                        return f"抱歉，执行审查失败：{e}"
                    if not res.get('ok'):
                        return f"审查失败：{res.get('error') or '未知错误'}"
                    review_text = str(res.get('review_text') or '')
                    ok_count = 0
                    for mid in ids:
                        try:
                            if dra.write_review_to_draft_reply(mid, review_text, subject_prefix='审查结果 - '):
                                ok_count += 1
                        except Exception:
                            pass
                    return f"已将审查结果写入 {ok_count}/{len(ids)} 封邮件的草稿。"
                if action == 'list':
                    start_iso_raw = parsed.get('start')
                    end_iso_raw = parsed.get('end')
                    mailbox_raw = parsed.get('mailbox_name')
                    unread_only_raw = parsed.get('unread_only')
                    limit_raw = parsed.get('limit')
                    start_iso = start_iso_raw if isinstance(start_iso_raw, str) else None
                    end_iso = end_iso_raw if isinstance(end_iso_raw, str) else None
                    mailbox_name = mailbox_raw if isinstance(mailbox_raw, str) and mailbox_raw.strip() else None
                    unread_only = bool(unread_only_raw) if isinstance(unread_only_raw, (bool, int)) else False
                    try:
                        limit = int(limit_raw) if isinstance(limit_raw, (int, str)) and str(limit_raw).isdigit() else 20
                    except Exception:
                        limit = 20
                    try:
                        mailbox_eff = mailbox_name or self.preferences.get('default_mailbox_name')
                        unread_eff = unread_only if isinstance(unread_only, bool) else bool(self.preferences.get('unread_only'))
                        items = em.list_emails(start=start_iso, end=end_iso, mailbox_name=mailbox_eff, unread_only=unread_eff, limit=limit)
                    except Exception as e:
                        return f"抱歉，读取邮件时出现错误：{e}"
                    if not items:
                        return "没有找到符合条件的邮件。"
                    lines = []
                    for i, it in enumerate(items, 1):
                        subj = it.get('subject') or '(无主题)'
                        sender = it.get('sender') or ''
                        date_s = it.get('date') or ''
                        mailbox = it.get('mailbox') or ''
                        read = it.get('read')
                        tag = '未读' if read is False else '已读'
                        lines.append(f"{i}. {subj} | {sender} | {date_s} | {mailbox} | {tag}")
                    return "以下是匹配的邮件：\n" + "\n".join(lines)
                elif action in ('create', 'send'):
                    to_raw = parsed.get('email_to')
                    cc_raw = parsed.get('email_cc')
                    bcc_raw = parsed.get('email_bcc')
                    attachments_raw = parsed.get('email_attachments')
                    subj_raw = parsed.get('email_subject')
                    body_raw = parsed.get('email_body')
                    # 归一化为列表/字符串
                    def _to_list(x):
                        if isinstance(x, list):
                            return [str(v) for v in x if isinstance(v, (str, int)) and str(v).strip()]
                        if isinstance(x, (str, int)):
                            s = str(x)
                            if "," in s:
                                return [t.strip() for t in s.split(",") if t.strip()]
                            return [s] if s.strip() else []
                        return []
                    to_list = _to_list(to_raw)
                    cc_list = _to_list(cc_raw)
                    bcc_list = _to_list(bcc_raw)
                    attach_list = _to_list(attachments_raw)
                    subject = subj_raw if isinstance(subj_raw, str) else None
                    body = body_raw if isinstance(body_raw, str) else ""
                    try:
                        ok = em.send_email(to=to_list, subject=subject or "(无主题)", body=body, cc=cc_list or None, bcc=bcc_list or None, attachments=attach_list or None)
                    except Exception as e:
                        return f"抱歉，发送邮件时出现错误：{e}"
                    return "已发送邮件。" if ok else "邮件发送失败。"
                elif action == 'mark':
                    ids_raw = parsed.get('email_ids')
                    mark_read_raw = parsed.get('mark_read')
                    def _to_list_str(x):
                        if isinstance(x, list):
                            return [str(v) for v in x if isinstance(v, (str, int)) and str(v).strip()]
                        if isinstance(x, (str, int)):
                            s = str(x)
                            if "," in s:
                                return [t.strip() for t in s.split(",") if t.strip()]
                            return [s] if s.strip() else []
                        return []
                    ids = _to_list_str(ids_raw)
                    mark_read = bool(mark_read_raw) if isinstance(mark_read_raw, (bool, int)) else True
                    if not ids:
                        return "请提供要标记的邮件ID列表。可先用列表功能获取ID。"
                    try:
                        res = em.mark_messages_by_ids(message_ids=ids, read=mark_read)
                    except Exception as e:
                        return f"抱歉，标记邮件时出现错误：{e}"
                    changed = int(res.get('changed') or 0)
                    tag = '已读' if mark_read else '未读'
                    return f"已将 {changed} 封邮件标记为{tag}。"
                elif action == 'move':
                    ids_raw = parsed.get('email_ids')
                    move_to_raw = parsed.get('email_move_to')
                    def _to_list_str(x):
                        if isinstance(x, list):
                            return [str(v) for v in x if isinstance(v, (str, int)) and str(v).strip()]
                        if isinstance(x, (str, int)):
                            s = str(x)
                            if "," in s:
                                return [t.strip() for t in s.split(",") if t.strip()]
                            return [s] if s.strip() else []
                        return []
                    ids = _to_list_str(ids_raw)
                    move_to = move_to_raw if isinstance(move_to_raw, str) and move_to_raw.strip() else None
                    if not ids or not move_to:
                        return "请提供要移动的邮件ID列表与目标邮箱（文件夹）名称。"
                    try:
                        res = em.move_messages_by_ids(message_ids=ids, target_mailbox_name=move_to)
                    except Exception as e:
                        return f"抱歉，移动邮件时出现错误：{e}"
                    moved = int(res.get('moved') or 0)
                    return f"已将 {moved} 封邮件移动到 {move_to}。"
                elif action == 'verify':
                    addr_raw = parsed.get('email_address')
                    pwd_raw = parsed.get('email_password')
                    imap_host_raw = parsed.get('imap_server')
                    smtp_host_raw = parsed.get('smtp_server')
                    imap_port_raw = parsed.get('imap_port')
                    smtp_port_raw = parsed.get('smtp_port')
                    addr = addr_raw if isinstance(addr_raw, str) and addr_raw.strip() else None
                    pwd = pwd_raw if isinstance(pwd_raw, str) and pwd_raw.strip() else None
                    imap_host = imap_host_raw if isinstance(imap_host_raw, str) and imap_host_raw.strip() else None
                    smtp_host = smtp_host_raw if isinstance(smtp_host_raw, str) and smtp_host_raw.strip() else None
                    try:
                        imap_port = int(imap_port_raw) if isinstance(imap_port_raw, (int, str)) and str(imap_port_raw).isdigit() else 993
                    except Exception:
                        imap_port = 993
                    try:
                        smtp_port = int(smtp_port_raw) if isinstance(smtp_port_raw, (int, str)) and str(smtp_port_raw).isdigit() else 465
                    except Exception:
                        smtp_port = 465
                    if not addr or not pwd:
                        return "请提供邮箱地址与密码以进行验证。"
                    try:
                        res = em.verify_credentials(email_address=addr, password=pwd, imap_server=imap_host, imap_port=imap_port, smtp_server=smtp_host, smtp_port=smtp_port)
                    except Exception as e:
                        return f"抱歉，验证邮箱时出现错误：{e}"
                    tag_imap = "成功" if res.get('imap') else "失败"
                    tag_smtp = "成功" if res.get('smtp') else "失败"
                    return f"邮箱验证结果：IMAP登录{tag_imap}，SMTP登录{tag_smtp}。"
                elif action == 'process_meetings':
                    # 优先委派给会议处理智能体；不可用则回退到现有逻辑
                    start_iso_raw = parsed.get('start')
                    end_iso_raw = parsed.get('end')
                    mailbox_raw = parsed.get('mailbox_name')
                    unread_only_raw = parsed.get('unread_only')
                    limit_raw = parsed.get('limit')
                    start_iso = start_iso_raw if isinstance(start_iso_raw, str) else None
                    end_iso = end_iso_raw if isinstance(end_iso_raw, str) else None
                    mailbox_name = mailbox_raw if isinstance(mailbox_raw, str) and mailbox_raw.strip() else None
                    unread_only = bool(unread_only_raw) if isinstance(unread_only_raw, (bool, int)) else True
                    try:
                        limit = int(limit_raw) if isinstance(limit_raw, (int, str)) and str(limit_raw).isdigit() else 20
                    except Exception:
                        limit = 20
                    mt = self._find_meeting_agent()
                    if mt:
                        try:
                            return mt.process_meetings(start=start_iso, end=end_iso, unread_only=unread_only, limit=limit, mailbox_name=mailbox_name)
                        except Exception:
                            pass
                    # 回退逻辑：收取邮件，生成摘要，并识别会议信息；如参会人包含本机账户则自动入日历
                    sa = self._find_summary_agent()
                    cal = self._find_calendar_agent()
                    if not sa:
                        return "抱歉，未加载到摘要智能体。"
                    if not cal:
                        return "抱歉，未加载到日历智能体。"
                    try:
                        mailbox_eff = mailbox_name or self.preferences.get('default_mailbox_name')
                        unread_eff = unread_only if isinstance(unread_only, bool) else bool(self.preferences.get('unread_only'))
                        items = em.list_emails(start=start_iso, end=end_iso, mailbox_name=mailbox_eff, unread_only=unread_eff, limit=limit)
                    except Exception as e:
                        return f"抱歉，读取邮件时出现错误：{e}"
                    if not items:
                        return "没有找到符合条件的邮件。"
                    ids = [it.get('id') for it in items if it.get('id')]
                    only_with_atts = bool(self.preferences.get('meeting_only_with_attachments'))
                    if only_with_atts:
                        try:
                            import os
                            tmp_root = os.path.join('/tmp', 'd2_meeting_filter')
                            os.makedirs(tmp_root, exist_ok=True)
                            kept = []
                            for mid in ids:
                                try:
                                    saved = em.save_attachments_by_ids([mid], tmp_root)
                                except Exception:
                                    saved = []
                                if saved:
                                    kept.append(mid)
                            ids = kept
                        except Exception:
                            pass
                    try:
                        details = em.get_messages_by_ids(ids)
                    except Exception as e:
                        return f"抱歉，读取邮件正文时出现错误：{e}"
                    try:
                        my_addrs = em.get_account_addresses()
                    except Exception:
                        my_addrs = []

                    best_model = self.llm_service.get_best_model()
                    best_model_str = str(best_model) if best_model else ""
                    lines = []
                    auto_added = 0
                    for i, d in enumerate(details, 1):
                        subj = d.get('subject') or '(无主题)'
                        sender = d.get('sender') or ''
                        date_s = d.get('date') or ''
                        mailbox = d.get('mailbox') or ''
                        read = d.get('read')
                        tag = '未读' if read is False else '已读'
                        to_addrs = d.get('to') or []
                        cc_addrs = d.get('cc') or []
                        body = d.get('body') or ''
                        # 先生成要点式摘要
                        try:
                            summary = sa.summarize(input_text=body, file_path=None, style='信息型', length='简短', language='zh', output_format='bullets')
                        except Exception:
                            summary = '(摘要生成失败)'
                        # 使用 LLM 抽取结构化会议信息
                        meeting_json = {}
                        if best_model:
                            sys_msg = (
                                "你是一个会议解析器。根据提供的邮件元信息与正文，识别是否是会议通知，"
                                "并输出一个严格的 JSON: {is_meeting:boolean, title:string|null, start:string|null, end:string|null, location:string|null, attendees:string[]|null, includes_me:boolean}."
                                "时间请尽量用本地时区并输出 ISO 格式: YYYY-MM-DDTHH:MM:SS。若无法确定则填 null。"
                                "includes_me 为 true 的条件是: 参会人员或收件人/抄送中包含本机账户地址之一（将提供）。"
                            )
                            prompt_obj = {
                                "subject": subj,
                                "sender": sender,
                                "to": to_addrs,
                                "cc": cc_addrs,
                                "my_addresses": my_addrs,
                                "body": body,
                            }
                            try:
                                resp = self.llm_service.query(
                                    best_model_str,
                                    f"系统: {sys_msg}\n用户: {json.dumps(prompt_obj, ensure_ascii=False)}"
                                )
                                text = str(resp)
                                m = re.search(r"\{[\s\S]*\}", text)
                                if m:
                                    meeting_json = json.loads(m.group(0))
                            except Exception:
                                meeting_json = {}
                        is_meeting = bool(meeting_json.get('is_meeting')) if isinstance(meeting_json, dict) else False
                        includes_me = bool(meeting_json.get('includes_me')) if isinstance(meeting_json, dict) else False
                        title = meeting_json.get('title') if isinstance(meeting_json.get('title'), str) else None
                        start_i = meeting_json.get('start') if isinstance(meeting_json.get('start'), str) else None
                        end_i = meeting_json.get('end') if isinstance(meeting_json.get('end'), str) else None
                        location = meeting_json.get('location') if isinstance(meeting_json.get('location'), str) else None
                        attendees = meeting_json.get('attendees') if isinstance(meeting_json.get('attendees'), list) else []
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
                            if includes_me and isinstance(start_i, str) and start_i.strip():
                                try:
                                    ok = cal.create_event(title=title or '会议', start=start_i, end=end_i if isinstance(end_i, str) and end_i.strip() else None, location=location or None)
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
                    suffix = f"\n提示：如需加入，请回复“加入第N封”。"
                    if auto_added:
                        suffix = suffix + f"\n已自动加入 {auto_added} 个会议到日历。"
                    return "以下为邮件摘要与会议识别：\n" + "\n".join(lines) + suffix
                else:
                    return None
            cal = self._find_calendar_agent()
            if not cal:
                return "抱歉，未加载到日历智能体。"
            action = parsed.get('action')
            if action == 'list':
                start_iso_raw = parsed.get('start')
                end_iso_raw = parsed.get('end')
                cal_name_raw = parsed.get('calendar_name')
                # 将可能的 Any/None 收敛为 Optional[str]
                start_iso = start_iso_raw if isinstance(start_iso_raw, str) else None
                end_iso = end_iso_raw if isinstance(end_iso_raw, str) else None
                cal_name = cal_name_raw if isinstance(cal_name_raw, str) and cal_name_raw.strip() else None
                try:
                    events = cal.get_events(start=start_iso, end=end_iso, calendar_name=cal_name)
                except Exception as e:
                    return f"抱歉，读取日历时出现错误：{e}"
                if not events:
                    return "该时间范围内没有找到日程。"
                lines = []
                for i, ev in enumerate(events, 1):
                    title = ev.get('title') or '未命名事件'
                    st = ev.get('start') or ''
                    ed = ev.get('end') or ''
                    loc = ev.get('location') or ''
                    line = f"{i}. {title} | {st} → {ed}" + (f" | {loc}" if loc else '')
                    lines.append(line)
                return "以下是您的日程：\n" + "\n".join(lines)
            elif action == 'create':
                title_raw = parsed.get('title')
                start_iso_raw = parsed.get('start')
                end_iso_raw = parsed.get('end')
                location_raw = parsed.get('location')
                cal_name_raw = parsed.get('calendar_name')
                title = title_raw if isinstance(title_raw, str) and title_raw.strip() else None
                start_iso = start_iso_raw if isinstance(start_iso_raw, str) else None
                end_iso = end_iso_raw if isinstance(end_iso_raw, str) else None
                location = location_raw if isinstance(location_raw, str) and location_raw.strip() else None
                cal_name = cal_name_raw if isinstance(cal_name_raw, str) and cal_name_raw.strip() else None
                try:
                    ok = cal.create_event(title=title, start=start_iso, end=end_iso, location=location, calendar_name=cal_name)
                except Exception as e:
                    return f"抱歉，创建日历事件时出现错误：{e}"
                return "已创建事件。" if ok else "事件创建失败。"
            else:
                # 未指定动作，回退到模型回答
                return None

        # 若 LLM 路由未命中，回退到关键词判断
        cal_keywords = ['日程', '日历', '安排', '会议', '事件']
        email_keywords = ['邮件', '邮箱', '收件箱', '未读', '发邮件', '邮件列表', '附件', '移动', '标记']
        doc_review_keywords = ['审查', '审核', '评审']
        meeting_keywords = ['会议通知', '会议邀请', '邀请函', 'meeting invite', 'meeting']
        email_verify_keywords = ['验证', '登录', '密码']
        summary_keywords = ['摘要', '总结', '概述', '提炼', '要点', '梳理', '概括', '结构化大纲', '大纲', '结论+建议', '三段式', 'docx', 'pdf', 'word', '文档', '报告', 'outline', 'bullets', 'conclusion']
        stt_keywords = ['语音转文字', '转文字', '音频识别', '语音识别', '转写', '转录', '听写']
        if any(k in prompt for k in cal_keywords):
            cal = self._find_calendar_agent()
            if not cal:
                return None
            start_iso, end_iso = self._resolve_range_from_prompt(prompt)
            try:
                events = cal.get_events(start=start_iso, end=end_iso, calendar_name=self.preferences.get('default_calendar_name'))
            except Exception as e:
                return f"抱歉，读取日历时出现错误：{e}"
            if not events:
                return "该时间范围内没有找到日程。"
            lines = []
            for i, ev in enumerate(events, 1):
                title = ev.get('title') or '未命名事件'
                st = ev.get('start') or ''
                ed = ev.get('end') or ''
                loc = ev.get('location') or ''
                line = f"{i}. {title} | {st} → {ed}" + (f" | {loc}" if loc else '')
                lines.append(line)
            return "以下是您的日程：\n" + "\n".join(lines)
        elif any(k in prompt.lower() for k in summary_keywords):
            sa = self._find_summary_agent()
            if not sa:
                return None
            # 尝试从文本中抽取文件路径
            file_path = None
            try:
                m_file = re.search(r"(/[^\s,;]+\.(?:txt|md|docx|pdf))", prompt)
                if m_file:
                    file_path = m_file.group(1)
            except Exception:
                pass
            # 简单识别风格/长度/语言
            p_lower = prompt.lower()
            style = None
            if '要点' in prompt:
                style = '要点式'
            elif '学术' in prompt or 'academic' in p_lower:
                style = '学术风'
            elif '商业' in prompt or 'business' in p_lower:
                style = '商业风'
            length = None
            if '简短' in prompt or '精简' in prompt or '简洁' in prompt:
                length = '简短'
            elif '详细' in prompt or '完整' in prompt or '尽量长' in prompt:
                length = '详细'
            language = None
            if '英文' in prompt or 'english' in p_lower:
                language = 'en'
            elif '中文' in prompt or '汉字' in prompt:
                language = 'zh'
            fmt = None
            if ('结构化大纲' in prompt) or ('大纲' in prompt) or ('outline' in p_lower):
                fmt = 'outline'
            elif ('结论+建议' in prompt) or ('三段式' in prompt) or ('conclusion' in p_lower):
                fmt = 'conclusion_recommendations'
            elif ('要点' in prompt) or ('bullets' in p_lower):
                fmt = 'bullets'
            try:
                out = sa.summarize(input_text=None if file_path else prompt, file_path=file_path, style=style, length=length, language=language, output_format=fmt)
            except Exception as e:
                return f"抱歉，生成摘要时出现错误：{e}"
            return out
        elif any(k in prompt for k in stt_keywords):
            # 语音转文字兜底：提取音频文件路径、后端与语言
            # 支持常见音频/视频容器（whisper 可识别）
            audio_path = None
            try:
                m_audio = re.search(r"(/[^\s,;]+\.(?:wav|mp3|m4a|ogg|flac|mp4))", prompt, re.IGNORECASE)
                if m_audio:
                    audio_path = m_audio.group(1)
            except Exception:
                pass
            p_lower = prompt.lower()
            backend = None
            if 'fun-asr' in p_lower or 'funasr' in p_lower:
                backend = 'funasr'
            elif 'whisper' in p_lower:
                backend = 'whisper'
            language = None
            if '英文' in prompt or 'english' in p_lower:
                language = 'en'
            elif '中文' in prompt or '汉字' in prompt or 'chinese' in p_lower:
                language = 'zh'
            if not audio_path:
                return "请提供音频文件路径，例如：语音转文字 /Users/xx/audio.mp3"
            res = self.stt_service.transcribe(audio_path=audio_path, backend=backend, language=language)
            if not res.get('ok'):
                return f"语音转文字失败：{res.get('error') or '未知错误'}"
            text = str(res.get('text') or '').strip()
            used = res.get('backend') or 'unknown'
            return f"[STT:{used}] 转写结果：\n{text}"
        elif any(k in prompt for k in email_verify_keywords):
            em = self._find_email_agent()
            if not em:
                return None
            # 兜底解析邮箱与密码
            m_addr = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", prompt)
            addr = m_addr.group(0) if m_addr else None
            # 简单提取“密码”后的内容
            pwd = None
            for sep in [":", "：", "=", "＝", "->", "→"]:
                if "密码" in prompt and sep in prompt:
                    idx = prompt.find("密码")
                    if idx >= 0:
                        rest = prompt[idx + len("密码"):]
                        if sep in rest:
                            pwd = rest.split(sep, 1)[1].strip()
                            break
            if not addr or not pwd:
                return "请提供有效的邮箱地址与密码，例如：验证邮箱 user@example.com, 密码: yourpass"
            try:
                res = em.verify_credentials(email_address=addr, password=pwd)
            except Exception as e:
                return f"抱歉，验证邮箱时出现错误：{e}"
            tag_imap = "成功" if res.get('imap') else "失败"
            tag_smtp = "成功" if res.get('smtp') else "失败"
            return f"邮箱验证结果：IMAP登录{tag_imap}，SMTP登录{tag_smtp}。"
        elif any(k in prompt for k in meeting_keywords):
            # 关键词兜底：优先委派给会议处理智能体
            mt = self._find_meeting_agent()
            if mt:
                start_iso, end_iso = self._resolve_range_from_prompt(prompt)
                unread_only = True if '未读' in prompt else True
                try:
                    return mt.process_meetings(start=start_iso, end=end_iso, unread_only=unread_only, limit=20)
                except Exception:
                    pass
            # 回退：直接在此处理会议类邮件
            em = self._find_email_agent()
            sa = self._find_summary_agent()
            cal = self._find_calendar_agent()
            if not em or not sa or not cal:
                return None
            unread_only = True if '未读' in prompt else True
            start_iso, end_iso = self._resolve_range_from_prompt(prompt)
            try:
                mailbox_eff = self.preferences.get('default_mailbox_name')
                items = em.list_emails(start=start_iso, end=end_iso, mailbox_name=mailbox_eff, unread_only=unread_only, limit=20)
            except Exception as e:
                return f"抱歉，读取邮件时出现错误：{e}"
            if not items:
                return "没有找到符合条件的会议邮件。"
            ids = [it.get('id') for it in items if it.get('id')]
            try:
                details = em.get_messages_by_ids(ids)
            except Exception as e:
                return f"抱歉，读取邮件正文时出现错误：{e}"
            try:
                my_addrs = em.get_account_addresses()
            except Exception:
                my_addrs = []
            best_model = self.llm_service.get_best_model()
            best_model_str = str(best_model) if best_model else ""
            lines = []
            auto_added = 0
            for i, d in enumerate(details, 1):
                subj = d.get('subject') or '(无主题)'
                sender = d.get('sender') or ''
                date_s = d.get('date') or ''
                mailbox = d.get('mailbox') or ''
                read = d.get('read')
                tag = '未读' if read is False else '已读'
                to_addrs = d.get('to') or []
                cc_addrs = d.get('cc') or []
                body = d.get('body') or ''
                # 摘要
                try:
                    summary = sa.summarize(input_text=body, file_path=None, style='信息型', length='简短', language='zh', output_format='bullets')
                except Exception:
                    summary = '(摘要生成失败)'
                # 会议解析
                meeting_json = {}
                if best_model:
                    sys_msg = (
                        "你是一个会议解析器。根据提供的邮件元信息与正文，识别是否是会议通知，"
                        "并输出一个严格的 JSON: {is_meeting:boolean, title:string|null, start:string|null, end:string|null, location:string|null, attendees:string[]|null, includes_me:boolean}."
                        "时间请尽量用本地时区并输出 ISO 格式: YYYY-MM-DDTHH:MM:SS。若无法确定则填 null。"
                        "includes_me 为 true 的条件是: 参会人员或收件人/抄送中包含本机账户地址之一（将提供）。"
                    )
                    prompt_obj = {
                        "subject": subj,
                        "sender": sender,
                        "to": to_addrs,
                        "cc": cc_addrs,
                        "my_addresses": my_addrs,
                        "body": body,
                    }
                    try:
                        resp = self.llm_service.query(
                            best_model_str,
                            f"系统: {sys_msg}\n用户: {json.dumps(prompt_obj, ensure_ascii=False)}"
                        )
                        text = str(resp)
                        m = re.search(r"\{[\s\S]*\}", text)
                        if m:
                            meeting_json = json.loads(m.group(0))
                    except Exception:
                        meeting_json = {}
                is_meeting = bool(meeting_json.get('is_meeting')) if isinstance(meeting_json, dict) else False
                includes_me = bool(meeting_json.get('includes_me')) if isinstance(meeting_json, dict) else False
                title = meeting_json.get('title') if isinstance(meeting_json.get('title'), str) else None
                start_i = meeting_json.get('start') if isinstance(meeting_json.get('start'), str) else None
                end_i = meeting_json.get('end') if isinstance(meeting_json.get('end'), str) else None
                location = meeting_json.get('location') if isinstance(meeting_json.get('location'), str) else None
                attendees = meeting_json.get('attendees') if isinstance(meeting_json.get('attendees'), list) else []
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
                    if includes_me and isinstance(start_i, str) and start_i.strip():
                        try:
                            ok = cal.create_event(title=title or '会议', start=start_i, end=end_i if isinstance(end_i, str) and end_i.strip() else None, location=location or None)
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
            suffix = f"\n提示：如需加入，请回复“加入第N封”。"
            if auto_added:
                suffix = suffix + f"\n已自动加入 {auto_added} 个会议到日历。"
            return "以下为邮件摘要与会议识别：\n" + "\n".join(lines) + suffix
        elif re.search(r"加入第(\d+)(?:封|条)?", prompt):
            # 交互确认：优先委派会议智能体执行加入操作
            mt = self._find_meeting_agent()
            if mt:
                m_idx = re.search(r"加入第(\d+)(?:封|条)?", prompt)
                idx = int(m_idx.group(1)) if m_idx else 0
                start_iso, end_iso = self._resolve_range_from_prompt(prompt)
                if idx <= 0:
                    return "请以“加入第N封”格式指定要加入的邮件序号。"
                try:
                    return mt.join_by_index(start=start_iso, end=end_iso, unread_only=True, index=idx, limit=20)
                except Exception:
                    pass
            # 回退：在此直接读取并加入
            em = self._find_email_agent()
            sa = self._find_summary_agent()
            cal = self._find_calendar_agent()
            if not em or not cal:
                return None
            m_idx = re.search(r"加入第(\d+)(?:封|条)?", prompt)
            idx = int(m_idx.group(1)) if m_idx else 0
            if idx <= 0:
                return "请以“加入第N封”格式指定要加入的邮件序号。"
            # 重新抓取默认范围的未读会议邮件
            unread_only = True
            start_iso, end_iso = self._resolve_range_from_prompt(prompt)
            try:
                mailbox_eff = self.preferences.get('default_mailbox_name')
                items = em.list_emails(start=start_iso, end=end_iso, mailbox_name=mailbox_eff, unread_only=unread_only, limit=20)
            except Exception as e:
                return f"抱歉，读取邮件时出现错误：{e}"
            if not items or idx > len(items):
                return "序号不在范围内或没有找到邮件。"
            target = items[idx - 1]
            msg_id = target.get('id')
            if not msg_id:
                return "无法定位该邮件的ID。"
            try:
                detail_list = em.get_messages_by_ids([msg_id])
            except Exception as e:
                return f"抱歉，读取该邮件正文时出现错误：{e}"
            if not detail_list:
                return "无法读取该邮件详情。"
            d = detail_list[0]
            subj = d.get('subject') or '会议'
            body = d.get('body') or ''
            to_addrs = d.get('to') or []
            cc_addrs = d.get('cc') or []
            try:
                my_addrs = em.get_account_addresses()
            except Exception:
                my_addrs = []
            best_model = self.llm_service.get_best_model()
            best_model_str = str(best_model) if best_model else ""
            meeting_json = {}
            if best_model:
                sys_msg = (
                    "你是一个会议解析器。根据提供的邮件元信息与正文，识别是否是会议通知，"
                    "并输出一个严格的 JSON: {is_meeting:boolean, title:string|null, start:string|null, end:string|null, location:string|null, attendees:string[]|null, includes_me:boolean}."
                    "时间请尽量用本地时区并输出 ISO 格式: YYYY-MM-DDTHH:MM:SS。若无法确定则填 null。"
                )
                prompt_obj = {
                    "subject": subj,
                    "sender": d.get('sender') or '',
                    "to": to_addrs,
                    "cc": cc_addrs,
                    "my_addresses": my_addrs,
                    "body": body,
                }
                try:
                    resp = self.llm_service.query(
                        best_model_str,
                        f"系统: {sys_msg}\n用户: {json.dumps(prompt_obj, ensure_ascii=False)}"
                    )
                    text = str(resp)
                    m = re.search(r"\{[\s\S]*\}", text)
                    if m:
                        meeting_json = json.loads(m.group(0))
                except Exception:
                    meeting_json = {}
            is_meeting = bool(meeting_json.get('is_meeting')) if isinstance(meeting_json, dict) else False
            title = meeting_json.get('title') if isinstance(meeting_json.get('title'), str) else None
            start_i = meeting_json.get('start') if isinstance(meeting_json.get('start'), str) else None
            end_i = meeting_json.get('end') if isinstance(meeting_json.get('end'), str) else None
            location = meeting_json.get('location') if isinstance(meeting_json.get('location'), str) else None
            if not is_meeting or not (isinstance(start_i, str) and start_i.strip()):
                return "该邮件未识别为会议或缺少时间信息，无法加入。"
            try:
                ok = cal.create_event(title=title or subj, start=start_i, end=end_i if isinstance(end_i, str) and end_i.strip() else None, location=location or None)
            except Exception as e:
                return f"抱歉，创建日历事件时出现错误：{e}"
            return "已加入该会议到日程。" if ok else "加入失败。"
        elif re.search(r"审查第(\d+)(?:封|条)?", prompt):
            # 交互：按序号审查附件并写入草稿
            dra = self._find_doc_review_agent()
            if not dra:
                return None
            m_idx = re.search(r"审查第(\d+)(?:封|条)?", prompt)
            idx = int(m_idx.group(1)) if m_idx else 0
            if idx <= 0:
                return "请以“审查第N封”格式指定要审查的邮件序号。"
            start_iso, end_iso = self._resolve_range_from_prompt(prompt)
            try:
                return dra.review_by_index(start=start_iso, end=end_iso, unread_only=True, index=idx, limit=20)
            except Exception as e:
                return f"抱歉，执行审查失败：{e}"
        elif any(k in prompt for k in email_keywords) or (('附件' in prompt) and any(k in prompt for k in doc_review_keywords)):
            em = self._find_email_agent()
            if not em:
                return None
            # 简单兜底：默认今天、如果包含“未读”则仅未读
            unread_only = True if '未读' in prompt else False
            start_iso, end_iso = self._resolve_range_from_prompt(prompt)
            try:
                items = em.list_emails(start=start_iso, end=end_iso, unread_only=unread_only, limit=20)
            except Exception as e:
                return f"抱歉，读取邮件时出现错误：{e}"
            if not items:
                return "没有找到符合条件的邮件。"
            lines = []
            for i, it in enumerate(items, 1):
                subj = it.get('subject') or '(无主题)'
                sender = it.get('sender') or ''
                date_s = it.get('date') or ''
                mailbox = it.get('mailbox') or ''
                read = it.get('read')
                tag = '未读' if read is False else '已读'
                lines.append(f"{i}. {subj} | {sender} | {date_s} | {mailbox} | {tag}")
            suffix = "（如需审查附件，请回复“审查第N封”。）" if '附件' in prompt else ''
            return "以下是匹配的邮件：\n" + "\n".join(lines) + ("\n" + suffix if suffix else '')
        else:
            return None

    def _llm_semantic_parse(self, prompt: str) -> Optional[Dict[str, Any]]:
        """使用大模型进行语义解析，返回结构化路由JSON。失败则返回 None。"""
        best_model = self.llm_service.get_best_model()
        if not best_model:
            return None
        # 收敛为字符串，避免传递 Any/None 给下游 query
        best_model_str: str = str(best_model)
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        sys_msg = (
            "你是一个路由器，负责把用户的中文指令解析为一个 JSON。"
            "仅输出JSON，不输出任何额外文字、代码块或解释。"
            "字段包括: route<'calendar'|'email'|'summary'|'stt'|'none'>, action<'list'|'create'|'send'|'verify'|'mark'|'move'|'summarize'|'process_meetings'|'review_attachments'|'transcribe'|null>,"
            " start<YYYY-MM-DDTHH:MM:SS或null>, end<同上或null>, calendar_name<或null>,"
            " title<创建事件标题或null>, location<或null>, remaining<boolean，用于'还有/剩余/剩下'>,"
            " mailbox_name<邮件列表查询的邮箱名或null>, unread_only<boolean或null>, limit<number或null>,"
            " email_to<string[]或string>, email_cc<string[]或string或null>, email_bcc<string[]或string或null>,"
            " email_subject<string或null>, email_body<string或null>, email_attachments<string[]或string或null>,"
            " email_ids<string[]或string或null>, mark_read<boolean或null>, email_move_to<string或null>,"
            " email_address<string或null>, email_password<string或null>, imap_server<string或null>, smtp_server<string或null>, imap_port<number或null>, smtp_port<number或null>,"
            " summary_text<string或null>, summary_file_path<string或null>, summary_style<string或null>, summary_length<string或null>, summary_language<'zh'|'en'或null>, summary_format<'bullets'|'outline'|'conclusion_recommendations'或null>,"
            " review_requirements<string或null>, review_preset_id<string或null>,"
            " audio_file_path<string或null>, stt_backend<'whisper'|'funasr'或null>, stt_model<string或null>, stt_language<'zh'|'en'或null>。"
            " 语义规则示例：'本周还有哪些日程' -> route:'calendar', action:'list', start:现在, end:本周日23:59, remaining:true;"
            " '帮我添加明天上午10点的会议到日历' -> route:'calendar', action:'create', title:'会议', start:明天10:00, end:明天11:00;"
            " '列出今天未读邮件' -> route:'email', action:'list', start:今天00:00, end:今天23:59, unread_only:true;"
            " '发一封邮件给张三，主题是会议，内容是安排如下，并附上/report.pdf' -> route:'email', action:'send', email_to:['zhangsan@example.com'], email_subject:'会议', email_body:'安排如下', email_attachments:['/Users/xx/report.pdf'];"
            " '把ID为<msg-id-1>,<msg-id-2>的邮件标记为已读' -> route:'email', action:'mark', email_ids:['<msg-id-1>','<msg-id-2>'], mark_read:true;"
            " '把这两封邮件移动到归档(Archive)' -> route:'email', action:'move', email_ids:['<msg-id-1>','<msg-id-2>'], email_move_to:'Archive';"
            " '处理今天的会议邀请邮件并识别是否加入日程' -> route:'email', action:'process_meetings', start:今天00:00, end:今天23:59, unread_only:true;"
            " '审查附件并把结果写入草稿' -> route:'email', action:'review_attachments', email_ids:['<msg-id-1>'], review_requirements:'质量规范', review_preset_id:null;"
            " '请对/Users/xx/report.pdf生成要点摘要，简短一些，输出英文' -> route:'summary', action:'summarize', summary_file_path:'/Users/xx/report.pdf', summary_style:'要点式', summary_length:'简短', summary_language:'en', summary_format:'bullets';"
            " '请把下面这段文字概述成中文的结构化大纲' -> route:'summary', action:'summarize', summary_text:'<用户给出的文字>', summary_style:'要点式', summary_language:'zh', summary_format:'outline';"
            " '请生成结论+建议三段式摘要' -> route:'summary', action:'summarize', summary_text:'<用户给出的文字>', summary_language:'zh', summary_format:'conclusion_recommendations';"
            " '把/Users/xx/audio.mp3转成中文文字' -> route:'stt', action:'transcribe', audio_file_path:'/Users/xx/audio.mp3', stt_language:'zh';"
            " '用fun-asr识别/Users/xx/meeting.wav' -> route:'stt', action:'transcribe', audio_file_path:'/Users/xx/meeting.wav', stt_backend:'funasr'。"
            f" 当前时间: {now_iso}。若用户未给日历名，不必填 calendar_name。"
        )
        user_msg = f"用户指令：{prompt}。请严格返回JSON。"

        try:
            raw = self.llm_service.query(best_model_str, f"系统: {sys_msg}\n用户: {user_msg}")
        except Exception:
            return None

        # 保证字符串类型，避免 None 或非字符串导致的类型问题
        raw_str: str = "" if raw is None else str(raw)

        def _extract_json_block(s: str) -> Optional[str]:
            code_m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", s, re.IGNORECASE)
            if code_m:
                return code_m.group(1)
            start = s.find('{')
            if start < 0:
                return None
            depth = 0
            in_str = False
            esc = False
            end = None
            for i in range(start, len(s)):
                ch = s[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == '\\':
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                
            if end is not None:
                return s[start:end+1]
            return None

        text_block = _extract_json_block(raw_str) or raw_str
        text: str = text_block

        try:
            parsed_any = json.loads(text)
        except Exception:
            return None

        if not isinstance(parsed_any, dict):
            return None

        parsed: Dict[str, Any] = parsed_any
        # 兜底：补全最小字段
        if 'route' not in parsed:
            parsed['route'] = 'none'
        return parsed

    def process_prompt(self, prompt: str) -> str:
        """处理单次用户输入，返回整洁的字符串输出，供图形界面复用。"""
        try:
            routed = self._maybe_route_to_calendar(prompt)
            if routed is not None:
                return str(routed)
            # 若关键词兜底未命中，则尝试调用 LLM 简答
            best_model = self.llm_service.get_best_model()
            best_model_str = str(best_model) if best_model else None
            if best_model_str is not None:
                try:
                    resp = self.llm_service.query(best_model_str, prompt)
                except Exception as e:
                    return f"抱歉，模型调用失败：{e}"
                return str(resp)
            else:
                return "抱歉，当前未连接大模型服务，可使用日历/邮件/摘要/语音转文字关键词功能。"
        except Exception as e:
            return f"抱歉，处理输入时出现错误：{e}"

    def polish_text(self, text: str, style: str = 'spoken_zh') -> str:
        """使用大模型对输出进行口语化润色，默认中文口语风格。"""
        try:
            best_model = self.llm_service.get_best_model()
            if not best_model:
                return text
            original = str(text or '')
            if not original.strip():
                return original
            if style == 'spoken_zh':
                prompt = (
                    "请将下面的文本改写为更口语化、自然、简洁的中文回答，适合直接朗读并呈现在聊天窗口。"
                    "保留所有关键信息与步骤，避免冗长、过多条目或复杂格式。\n\n"
                    "文本：\n" + original + "\n\n"
                    "仅输出改写后的文本。"
                )
            else:
                prompt = (
                    "Please rewrite the following text to be more conversational, natural, and concise."
                    " Keep all key information. Return only the revised text.\n\n"
                    + original
                )
            revised = self.llm_service.query(best_model, prompt)
            return str(revised).strip() or original
        except Exception:
            return text

    def load_mcp_agents(self):
        print("正在加载MCP智能体...")
        mcp_agents_dir = os.path.join(os.path.dirname(__file__), '..', 'mcp_agents')
        for agent_dir in os.listdir(mcp_agents_dir):
            agent_path = os.path.join(mcp_agents_dir, agent_dir)
            if os.path.isdir(agent_path):
                try:
                    module = importlib.import_module(f'mcp_agents.{agent_dir}.main')
                    agent_class = getattr(module, 'MCPAgent')
                    self.mcp_agents.append(agent_class())
                    print(f"成功加载MCP智能体: {agent_dir}")
                except Exception as e:
                    print(f"加载MCP智能体 {agent_dir} 失败: {e}")

    def start(self):
        print(f"你好，我是 {self.name}，你的个人助理。")
        print(f"已加载的MCP智能体: {[agent.name for agent in self.mcp_agents]}")
        
        # 将 best_model_str 明确声明为 Optional[str]，避免 None 与 str 的类型冲突
        best_model_str: Optional[str] = None
        best_model = self.llm_service.get_best_model()
        if not best_model:
            print("没有可用的大模型服务，将使用关键词与规则模式。")
        else:
            best_model_str = str(best_model)
            print(f"使用模型: {best_model_str}")

        while True:
            try:
                prompt = input("请输入您的问题 (输入 '退出' 结束): ")
                if prompt.lower() == '退出':
                    break
                routed = self._maybe_route_to_calendar(prompt)
                if routed is not None:
                    response = routed
                else:
                    if best_model_str is not None:
                        response = self.llm_service.query(best_model_str, prompt)
                    else:
                        response = "抱歉，当前未连接大模型服务，可使用日历/邮件/摘要/语音转文字关键词功能。"
                print(f"D2: {response}")
            except KeyboardInterrupt:
                break

if __name__ == "__main__":
    agent = D2Agent()
    agent.start()
