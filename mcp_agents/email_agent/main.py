import subprocess
from datetime import datetime, date, time
from typing import List, Dict, Optional


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

    def send_email(
        self,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
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
            + "send newMsg\n"
            + "end tell\n"
            + "return \"OK\"\n"
        )

        out = _run_osascript(script)
        return out.strip() == "OK"