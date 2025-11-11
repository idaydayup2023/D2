import subprocess
from datetime import datetime, date, time
from typing import List, Dict, Optional, Any
import imaplib
import smtplib
import ssl
import socket


def _run_osascript(script: str) -> str:
    """Run AppleScript via osascript and return stdout (raise on error)."""
    result = subprocess.run(["osascript"], input=script, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "AppleScript 执行失败")
    return result.stdout.strip()


def _make_applescript_date_handler() -> str:
    """AppleScript handler that builds a date object from components reliably."""
    return (
        "on makeDate(yyyy, mm, dd, hh, mi)\n"
        "    set d to current date\n"
        "    set year of d to yyyy\n"
        "    set monthNames to {January, February, March, April, May, June, July, August, September, October, November, December}\n"
        "    set month of d to item mm of monthNames\n"
        "    set day of d to dd\n"
        "    set time of d to (hh * hours + mi * minutes)\n"
        "    return d\n"
        "end makeDate\n"
    )


def _dt_parts(dt: datetime):
    return dt.year, dt.month, dt.day, dt.hour, dt.minute


class MCPAgent:
    def __init__(self):
        self.name = "邮件智能体"

    def list_emails(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        mailbox_name: Optional[str] = None,
        unread_only: bool = False,
        limit: int = 20,
    ) -> List[Dict]:
        """
        列出指定范围内的邮件。
        - start/end: ISO 字符串（本地时区），未提供则默认今天全天
        - mailbox_name: 指定邮箱中的某个邮箱（文件夹）名称；未提供则遍历所有账户的收件箱
        - unread_only: 是否仅返回未读邮件
        - limit: 最大返回条数
        返回: [{subject, sender, date, mailbox, id, read}]
        """
        # 解析时间范围
        if start is None or end is None:
            today = date.today()
            start_dt = datetime.combine(today, time(hour=0, minute=0))
            end_dt = datetime.combine(today, time(hour=23, minute=59))
        else:
            start_s = start.replace("Z", "+00:00")
            end_s = end.replace("Z", "+00:00")
            try:
                start_dt = datetime.fromisoformat(start_s)
                end_dt = datetime.fromisoformat(end_s)
            except Exception as e:
                raise ValueError(f"时间解析失败: {e}")

        sy, sm, sd, sh, si = _dt_parts(start_dt)
        ey, em, ed, eh, ei = _dt_parts(end_dt)

        mb_decl = (
            f'set mailboxName to "{mailbox_name}"\nset hasMailboxName to true\n'
            if mailbox_name
            else 'set mailboxName to ""\nset hasMailboxName to false\n'
        )

        filter_unread = " and read status is false" if unread_only else ""

        script = (
            _make_applescript_date_handler()
            + f"set startDate to makeDate({sy}, {sm}, {sd}, {sh}, {si})\n"
            + f"set endDate to makeDate({ey}, {em}, {ed}, {eh}, {ei})\n"
            + mb_decl
            + "tell application \"Mail\" to launch\n"
            + "tell application \"Mail\" to activate\n"
            + "delay 0.5\n"
            + "tell application \"Mail\"\n"
            + "set out to \"\"\n"
            + "set added to 0\n"
            + "set boxes to {}\n"
            + "if hasMailboxName then\n"
            + "    repeat with acc in accounts\n"
            + "        set ms to (mailboxes of acc whose name is mailboxName)\n"
            + "        set boxes to boxes & ms\n"
            + "    end repeat\n"
            + "else\n"
            + "    repeat with acc in accounts\n"
            + "        set boxes to boxes & {(inbox of acc)}\n"
            + "    end repeat\n"
            + "end if\n"
            + "repeat with mb in boxes\n"
            + "    set msgs to (messages of mb whose date received is greater than or equal to startDate and date received is less than or equal to endDate"
            + filter_unread
            + ")\n"
            + "    repeat with m in msgs\n"
            + "        set theSubject to subject of m\n"
            + "        set theSender to sender of m\n"
            + "        set theDate to (date received of m as string)\n"
            + "        set theId to message id of m\n"
            + "        set isRead to read status of m\n"
            + "        set boxName to name of mb\n"
            + "        set out to out & theSubject & \"|\" & theSender & \"|\" & theDate & \"|\" & theId & \"|\" & isRead & \"|\" & boxName & \"\n\"\n"
            + "        set added to added + 1\n"
            + "        if added ≥ "
            + str(max(1, limit))
            + " then exit repeat\n"
            + "    end repeat\n"
            + "    if added ≥ "
            + str(max(1, limit))
            + " then exit repeat\n"
            + "end repeat\n"
            + "end tell\n"
            + "return out\n"
        )

        raw = _run_osascript(script)
        emails: List[Dict] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            subject = parts[0] if len(parts) > 0 else ""
            sender = parts[1] if len(parts) > 1 else ""
            date_str = parts[2] if len(parts) > 2 else ""
            msg_id = parts[3] if len(parts) > 3 else ""
            read_val = parts[4] if len(parts) > 4 else "false"
            mailbox = parts[5] if len(parts) > 5 else ""
            emails.append({
                "subject": subject,
                "sender": sender,
                "date": date_str,
                "id": msg_id,
                "read": True if str(read_val).lower() in ["true", "yes"] else False,
                "mailbox": mailbox,
            })
        return emails

    def get_messages_by_ids(self, message_ids: List[str]) -> List[Dict[str, Any]]:
        """
        根据消息ID列表返回详细信息（跨所有账户的所有邮箱中查找）：
        返回: [{id, subject, sender, date, mailbox, read, to, cc, body}]
        - body 为纯文本正文（HTML邮件将尽力返回文本内容），特殊字符做了简易转义。
        """
        ids = [i for i in (message_ids or []) if isinstance(i, str) and i.strip()]
        if not ids:
            return []

        def _ids_to_list(ids: List[str]) -> str:
            inner = ", ".join([f'"{x}"' for x in ids])
            return f"{{{inner}}}"

        id_list = _ids_to_list(ids)

        # AppleScript：提供文本替换工具，清洗正文中的竖线与换行，避免解析冲突
        script = (
            "on replaceText(s, find, rep)\n"
            "    set AppleScript's text item delimiters to find\n"
            "    set parts to text items of s\n"
            "    set AppleScript's text item delimiters to rep\n"
            "    set outStr to parts as string\n"
            "    set AppleScript's text item delimiters to \"\"\n"
            "    return outStr\n"
            "end replaceText\n"
            "tell application \"Mail\"\n"
            + "launch\nactivate\ndelay 0.2\n"
            + f"set idList to {id_list}\n"
            + "set out to \"\"\n"
            + "set allBoxes to {}\n"
            + "repeat with acc in accounts\n"
            + "    set allBoxes to allBoxes & (mailboxes of acc)\n"
            + "end repeat\n"
            + "repeat with mb in allBoxes\n"
            + "    repeat with idStr in idList\n"
            + "        try\n"
            + "            set msgs to (messages of mb whose message id is (idStr as string))\n"
            + "            repeat with m in msgs\n"
            + "                set theSubject to subject of m\n"
            + "                set theSender to sender of m\n"
            + "                set theDate to (date received of m as string)\n"
            + "                set theId to message id of m\n"
            + "                set isRead to read status of m\n"
            + "                set boxName to name of mb\n"
            + "                set bodyRaw to content of m\n"
            + "                set bodyRaw to my replaceText(bodyRaw, return, \"␤\")\n"
            + "                set bodyRaw to my replaceText(bodyRaw, linefeed, \"␤\")\n"
            + "                set bodySafe to my replaceText(bodyRaw, \"|\", \"¦\")\n"
            + "                set toStr to \"\"\n"
            + "                repeat with r in (to recipients of m)\n"
            + "                    set addr to address of r\n"
            + "                    if (toStr = \"\") then set toStr to addr else set toStr to toStr & \", \" & addr\n"
            + "                end repeat\n"
            + "                set ccStr to \"\"\n"
            + "                repeat with r in (cc recipients of m)\n"
            + "                    set addr to address of r\n"
            + "                    if (ccStr = \"\") then set ccStr to addr else set ccStr to ccStr & \", \" & addr\n"
            + "                end repeat\n"
            + "                set out to out & theSubject & \"|\" & theSender & \"|\" & theDate & \"|\" & theId & \"|\" & isRead & \"|\" & boxName & \"|\" & bodySafe & \"|\" & toStr & \"|\" & ccStr & \"\n\"\n"
            + "            end repeat\n"
            + "        end try\n"
            + "    end repeat\n"
            + "end repeat\n"
            + "end tell\n"
            + "return out\n"
        )

        raw = _run_osascript(script)
        details: List[Dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            subject = parts[0] if len(parts) > 0 else ""
            sender = parts[1] if len(parts) > 1 else ""
            date_str = parts[2] if len(parts) > 2 else ""
            msg_id = parts[3] if len(parts) > 3 else ""
            read_val = parts[4] if len(parts) > 4 else "false"
            mailbox = parts[5] if len(parts) > 5 else ""
            body_safe = parts[6] if len(parts) > 6 else ""
            to_addrs = parts[7] if len(parts) > 7 else ""
            cc_addrs = parts[8] if len(parts) > 8 else ""
            details.append({
                "id": msg_id,
                "subject": subject,
                "sender": sender,
                "date": date_str,
                "mailbox": mailbox,
                "read": True if str(read_val).lower() in ["true", "yes"] else False,
                "to": [x.strip() for x in to_addrs.split(",") if x.strip()],
                "cc": [x.strip() for x in cc_addrs.split(",") if x.strip()],
                "body": body_safe.replace("␤", "\n"),
            })
        return details

    def get_account_addresses(self) -> List[str]:
        """返回本机系统邮件账户的地址列表（尽力而为）。"""
        script = (
            "tell application \"Mail\"\n"
            + "launch\nactivate\ndelay 0.2\n"
            + "set out to \"\"\n"
            + "repeat with acc in accounts\n"
            + "    try\n"
            + "        set addrs to email addresses of acc\n"
            + "        repeat with a in addrs\n"
            + "            if (out = \"\") then set out to a as string else set out to out & \", \" & (a as string)\n"
            + "        end repeat\n"
            + "    on error\n"
            + "        try\n"
            + "            set uname to user name of acc\n"
            + "            if (out = \"\") then set out to uname as string else set out to out & \", \" & (uname as string)\n"
            + "        end try\n"
            + "    end try\n"
            + "end repeat\n"
            + "end tell\n"
            + "return out\n"
        )
        try:
            raw = _run_osascript(script)
        except Exception:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

    def send_email(
        self,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
    ) -> bool:
        """发送邮件（使用系统邮件应用）。"""
        if not to or not subject:
            raise ValueError("收件人与主题为必填")

        # 构造收件人列表字符串
        def _list_to_applescript_list(addrs: List[str]) -> str:
            safe = [a for a in addrs if isinstance(a, str) and a.strip()]
            if not safe:
                return "{}"
            inner = ", ".join([f'"{x}"' for x in safe])
            return f"{{{inner}}}"

        to_list = _list_to_applescript_list(to)
        cc_list = _list_to_applescript_list(cc or [])
        bcc_list = _list_to_applescript_list(bcc or [])
        att_list = _list_to_applescript_list(attachments or [])

        script = (
            "tell application \"Mail\" to launch\n"
            + "tell application \"Mail\" to activate\n"
            + "delay 0.5\n"
            + "tell application \"Mail\"\n"
            + f"set newMsg to make new outgoing message with properties {{subject:\"{subject}\", content:\"{body}\", visible:false}}\n"
            + f"repeat with addr in {to_list}\n"
            + "    make new to recipient at newMsg with properties {address:addr}\n"
            + "end repeat\n"
            + f"repeat with addr in {cc_list}\n"
            + "    make new cc recipient at newMsg with properties {address:addr}\n"
            + "end repeat\n"
            + f"repeat with addr in {bcc_list}\n"
            + "    make new bcc recipient at newMsg with properties {address:addr}\n"
            + "end repeat\n"
            + f"set attPaths to {att_list}\n"
            + "repeat with p in attPaths\n"
            + "    try\n"
            + "        set f to POSIX file p\n"
            + "        make new attachment at newMsg with properties {file name:f}\n"
            + "    end try\n"
            + "end repeat\n"
            + "send newMsg\n"
            + "end tell\n"
            + "return \"OK\"\n"
        )

        out = _run_osascript(script)
        return out.strip() == "OK"

    def mark_messages_by_ids(self, message_ids: List[str], read: bool) -> Dict[str, Any]:
        """
        根据消息ID列表标记为已读/未读。
        返回 {changed: int}
        """
        ids = [i for i in (message_ids or []) if isinstance(i, str) and i.strip()]
        if not ids:
            return {"changed": 0}

        # 构造 AppleScript 列表
        def _ids_to_list(ids: List[str]) -> str:
            inner = ", ".join([f'"{x}"' for x in ids])
            return f"{{{inner}}}"

        id_list = _ids_to_list(ids)
        read_flag = "true" if read else "false"

        script = (
            "tell application \"Mail\"\n"
            + "launch\nactivate\ndelay 0.2\n"
            + f"set idList to {id_list}\n"
            + "set changedCount to 0\n"
            + "set allBoxes to {}\n"
            + "repeat with acc in accounts\n"
            + "    set allBoxes to allBoxes & (mailboxes of acc)\n"
            + "end repeat\n"
            + "repeat with mb in allBoxes\n"
            + "    repeat with idStr in idList\n"
            + "        try\n"
            + "            set msgs to (messages of mb whose message id is (idStr as string))\n"
            + "            repeat with m in msgs\n"
            + f"                set read status of m to {read_flag}\n"
            + "                set changedCount to changedCount + 1\n"
            + "            end repeat\n"
            + "        end try\n"
            + "    end repeat\n"
            + "end repeat\n"
            + "return changedCount\n"
            + "end tell\n"
        )

        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if res.returncode != 0:
                return {"changed": 0, "error": res.stderr.strip()}
            s = res.stdout.strip()
            return {"changed": int(s) if s.isdigit() else 0}
        except Exception as e:
            return {"changed": 0, "error": str(e)}

    def move_messages_by_ids(self, message_ids: List[str], target_mailbox_name: str) -> Dict[str, Any]:
        """
        根据消息ID列表移动到目标邮箱（文件夹）。
        返回 {moved: int}
        """
        ids = [i for i in (message_ids or []) if isinstance(i, str) and i.strip()]
        target = (target_mailbox_name or "").strip()
        if not ids or not target:
            return {"moved": 0}

        def _ids_to_list(ids: List[str]) -> str:
            inner = ", ".join([f'"{x}"' for x in ids])
            return f"{{{inner}}}"

        id_list = _ids_to_list(ids)
        target_esc = target.replace("\"", "\\\"")

        script = (
            "tell application \"Mail\"\n"
            + "launch\nactivate\ndelay 0.2\n"
            + f"set idList to {id_list}\n"
            + "set movedCount to 0\n"
            + "set targetBox to missing value\n"
            + "repeat with acc in accounts\n"
            + f"    set boxes to (mailboxes of acc whose name is \"{target_esc}\")\n"
            + "    if (count of boxes) > 0 then\n"
            + "        set targetBox to item 1 of boxes\n"
            + "        exit repeat\n"
            + "    end if\n"
            + "end repeat\n"
            + "if targetBox is missing value then return movedCount\n"
            + "set allBoxes to {}\n"
            + "repeat with acc in accounts\n"
            + "    set allBoxes to allBoxes & (mailboxes of acc)\n"
            + "end repeat\n"
            + "repeat with mb in allBoxes\n"
            + "    repeat with idStr in idList\n"
            + "        try\n"
            + "            set msgs to (messages of mb whose message id is (idStr as string))\n"
            + "            repeat with m in msgs\n"
            + "                move m to targetBox\n"
            + "                set movedCount to movedCount + 1\n"
            + "            end repeat\n"
            + "        end try\n"
            + "    end repeat\n"
            + "end repeat\n"
            + "return movedCount\n"
            + "end tell\n"
        )

        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if res.returncode != 0:
                return {"moved": 0, "error": res.stderr.strip()}
            s = res.stdout.strip()
            return {"moved": int(s) if s.isdigit() else 0}
        except Exception as e:
            return {"moved": 0, "error": str(e)}

    def verify_credentials(
        self,
        email_address: str,
        password: str,
        imap_server: Optional[str] = None,
        imap_port: int = 993,
        smtp_server: Optional[str] = None,
        smtp_port: int = 465,
        timeout: int = 10,
    ) -> Dict[str, bool]:
        """
        验证邮箱凭证有效性：尝试 IMAP 与 SMTP 登录。
        - 默认根据域名推断服务器：如 139.com -> imap.139.com / smtp.139.com
        - 返回: {imap: bool, smtp: bool}
        """
        if not isinstance(email_address, str) or "@" not in email_address:
            raise ValueError("请输入有效的邮箱地址")
        domain = email_address.split("@", 1)[1].strip().lower()
        def _default_imap(d: str) -> str:
            return "imap.139.com" if d == "139.com" else f"imap.{d}"
        def _default_smtp(d: str) -> str:
            return "smtp.139.com" if d == "139.com" else f"smtp.{d}"
        imap_host = imap_server or _default_imap(domain)
        smtp_host = smtp_server or _default_smtp(domain)

        results = {"imap": False, "smtp": False}

        # Pre-check connectivity to reduce slow failures
        try:
            socket.create_connection((imap_host, imap_port), timeout=timeout).close()
        except Exception:
            pass  # allow imaplib to attempt anyway
        try:
            socket.create_connection((smtp_host, smtp_port), timeout=timeout).close()
        except Exception:
            pass

        # IMAP login
        try:
            ctx = ssl.create_default_context()
            with imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ctx) as M:
                M.login(email_address, password)
                M.logout()
                results["imap"] = True
        except Exception:
            results["imap"] = False

        # SMTP login
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=timeout) as S:
                S.login(email_address, password)
                S.quit()
                results["smtp"] = True
        except Exception:
            results["smtp"] = False

        return results

    def save_attachments_by_ids(self, message_ids: List[str], target_dir: str) -> List[str]:
        """
        保存指定消息ID的所有附件到目标目录，返回保存后的文件路径列表。
        - 该方法会在所有账户的所有邮箱中查找匹配的消息。
        - 如果目标目录不存在，将自动创建。
        """
        ids = [i for i in (message_ids or []) if isinstance(i, str) and i.strip()]
        if not ids:
            return []
        os_path = target_dir or "/tmp"
        try:
            import os
            os.makedirs(os_path, exist_ok=True)
        except Exception:
            pass

        def _ids_to_list(ids: List[str]) -> str:
            inner = ", ".join([f'"{x}"' for x in ids])
            return f"{{{inner}}}"

        id_list = _ids_to_list(ids)
        dir_esc = os_path.replace("\"", "\\\"")

        script = (
            "tell application \"Mail\"\n"
            + "launch\nactivate\ndelay 0.2\n"
            + f"set idList to {id_list}\n"
            + f"set baseDir to \"{dir_esc}\"\n"
            + "set out to \"\"\n"
            + "set allBoxes to {}\n"
            + "repeat with acc in accounts\n"
            + "    set allBoxes to allBoxes & (mailboxes of acc)\n"
            + "end repeat\n"
            + "repeat with mb in allBoxes\n"
            + "    repeat with idStr in idList\n"
            + "        try\n"
            + "            set msgs to (messages of mb whose message id is (idStr as string))\n"
            + "            repeat with m in msgs\n"
            + "                set atts to mail attachments of m\n"
            + "                repeat with a in atts\n"
            + "                    set nm to name of a\n"
            + "                    set p to POSIX file (baseDir & \"/\" & nm)\n"
            + "                    try\n"
            + "                        save a in p\n"
            + "                        set out to out & (nm as string) & \"|\" & (idStr as string) & \"|\" & (baseDir & \"/\" & nm) & \"\\n\"\n"
            + "                    end try\n"
            + "                end repeat\n"
            + "            end repeat\n"
            + "        end try\n"
            + "    end repeat\n"
            + "end repeat\n"
            + "end tell\n"
            + "return out\n"
        )

        try:
            raw = _run_osascript(script)
        except Exception:
            return []
        paths: List[str] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            p = parts[2] if len(parts) > 2 else ""
            if p:
                paths.append(p)
        return paths

    def create_draft_reply_by_message_id(self, message_id: str, subject_prefix: Optional[str], body: str) -> bool:
        """
        为指定消息创建一封草稿回复，并写入提供的正文内容。
        - 可选地为主题增加前缀（如“审查结果 - ”）。
        返回 True/False 表示成功与否。
        """
        target = (message_id or "").strip()
        if not target:
            return False
        subj_pref = (subject_prefix or "").replace("\"", "\\\"")
        body_safe = (body or "").replace("\"", "\\\"")

        script = (
            "tell application \"Mail\"\n"
            + "launch\nactivate\ndelay 0.2\n"
            + f"set targetId to \"{target}\"\n"
            + "set theMsg to missing value\n"
            + "set allBoxes to {}\n"
            + "repeat with acc in accounts\n"
            + "    set allBoxes to allBoxes & (mailboxes of acc)\n"
            + "end repeat\n"
            + "repeat with mb in allBoxes\n"
            + "    try\n"
            + "        set msgs to (messages of mb whose message id is targetId)\n"
            + "        if (count of msgs) > 0 then\n"
            + "            set theMsg to item 1 of msgs\n"
            + "            exit repeat\n"
            + "        end if\n"
            + "    end try\n"
            + "end repeat\n"
            + "if theMsg is missing value then return \"NOT_FOUND\"\n"
            + "set theReply to reply theMsg\n"
            + "try\n"
            + f"    if (length of \"{subj_pref}\") > 0 then set subject of theReply to (\"{subj_pref}\" & (subject of theMsg))\n"
            + "end try\n"
            + f"set content of theReply to \"{body_safe}\"\n"
            + "save theReply\n"
            + "end tell\n"
            + "return \"OK\"\n"
        )

        out = _run_osascript(script)
        return out.strip() == "OK"