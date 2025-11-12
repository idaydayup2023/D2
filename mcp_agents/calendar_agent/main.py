import subprocess
from datetime import datetime, date, time, timedelta
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
        self.name = "日历智能体"
        # 简易内存缓存，加速重复查询（默认TTL 60秒）
        self._cache = {}
        self._cache_ttl_sec = 60
        # 默认未来窗口（天），用于未指定时间范围的查询
        self._default_horizon_days = 14
        # 单次查询的最大事件数，超过则提前退出（加速大窗口场景）
        self._max_events = 200

    def get_events(self, start: Optional[str] = None, end: Optional[str] = None, calendar_name: Optional[str] = None) -> List[Dict]:
        """
        获取指定时间范围内的事件；若未提供，则获取今天的事件。
        - start/end: ISO 格式字符串，如 "2025-11-14T00:00:00"（本地时区）
        - calendar_name: 指定日历名称；不提供则使用第一个日历
        返回: [{title, start, end, location}]
        """
        # 解析时间范围
        if start is None or end is None:
            # 默认从本周一开始，到未来窗口结束，避免一次性读取全部历史
            today = date.today()
            monday = today - timedelta(days=today.weekday())
            start_dt = datetime.combine(monday, time(hour=0, minute=0))
            end_dt = datetime.combine(monday + timedelta(days=self._default_horizon_days), time(hour=23, minute=59))
        else:
            # 粗略 ISO 解析（支持尾部 'Z'）
            start_s = start.replace("Z", "+00:00")
            end_s = end.replace("Z", "+00:00")
            try:
                start_dt = datetime.fromisoformat(start_s)
                end_dt = datetime.fromisoformat(end_s)
            except Exception as e:
                raise ValueError(f"时间解析失败: {e}")

        sy, sm, sd, sh, si = _dt_parts(start_dt)
        ey, em, ed, eh, ei = _dt_parts(end_dt)

        cal_list_decl = (
            f"set calList to {{calendar named \"{calendar_name}\"}}\n"
            if calendar_name else "set calList to calendars\n"
        )

        # 缓存键：日历名 + 起止时间
        cache_key = f"{calendar_name or '*'}|{start_dt.isoformat()}|{end_dt.isoformat()}"
        ent = self._cache.get(cache_key)
        if isinstance(ent, dict):
            ts = ent.get('ts')
            evs_cached = ent.get('events')
            if isinstance(ts, datetime) and isinstance(evs_cached, list):
                if (datetime.now() - ts).total_seconds() <= self._cache_ttl_sec:
                    return evs_cached

        script = (
            _make_applescript_date_handler()
            + f"set startDate to makeDate({sy}, {sm}, {sd}, {sh}, {si})\n"
            + f"set endDate to makeDate({ey}, {em}, {ed}, {eh}, {ei})\n"
            + "tell application \"Calendar\" to launch\n"
            + f"set maxEvents to {self._max_events}\n"
            + "set counter to 0\n"
            + "tell application \"Calendar\"\n"
            + cal_list_decl
            + "set out to \"\"\n"
            + "repeat with theCal in calList\n"
            + "    set evs to (every event of theCal whose start date is greater than or equal to startDate and start date is less than or equal to endDate)\n"
            + "    repeat with e in evs\n"
            + "        set itemText to (summary of e) & \"|\" & (start date of e as string) & \"|\" & (end date of e as string) & \"|\" & (location of e)\n"
            + "        set out to out & itemText & \"\\n\"\n"
            + "        set counter to counter + 1\n"
            + "        if counter is greater than or equal to maxEvents then exit repeat\n"
            + "    end repeat\n"
            + "    if counter is greater than or equal to maxEvents then exit repeat\n"
            + "end repeat\n"
            + "end tell\n"
            + "return out\n"
        )

        raw = _run_osascript(script)
        events = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            # 期望: [title, start_string, end_string, location]
            title = parts[0] if len(parts) > 0 else ""
            start_str = parts[1] if len(parts) > 1 else ""
            end_str = parts[2] if len(parts) > 2 else ""
            location = parts[3] if len(parts) > 3 else ""
            events.append({
                "title": title,
                "start": start_str,
                "end": end_str,
                "location": location,
            })
        # 写入缓存
        self._cache[cache_key] = {"ts": datetime.now(), "events": events}
        return events

    def create_event(self, title: str, start: str, end: str, location: Optional[str] = None, calendar_name: Optional[str] = None) -> bool:
        """
        创建事件。
        - title: 事件标题
        - start/end: ISO 字符串，如 "2025-11-14T10:00:00"（本地时区）
        - location: 可选
        - calendar_name: 指定日历名称，可选
        返回: True/False
        """
        if not title:
            raise ValueError("title 不能为空")
        start_s = start.replace("Z", "+00:00")
        end_s = end.replace("Z", "+00:00")
        try:
            start_dt = datetime.fromisoformat(start_s)
            end_dt = datetime.fromisoformat(end_s)
        except Exception as e:
            raise ValueError(f"时间解析失败: {e}")

        sy, sm, sd, sh, si = _dt_parts(start_dt)
        ey, em, ed, eh, ei = _dt_parts(end_dt)

        cal_selector = (
            f"set theCal to calendar \"{calendar_name}\"\n"
            "if (theCal is missing value) then set theCal to first calendar\n"
            if calendar_name else "set theCal to first calendar\n"
        )

        props = f"{{summary:\"{title}\", start date:startDate, end date:endDate"
        if location:
            props += f", location:\"{location}\""
        props += "}"

        script = (
            _make_applescript_date_handler()
            + f"set startDate to makeDate({sy}, {sm}, {sd}, {sh}, {si})\n"
            + f"set endDate to makeDate({ey}, {em}, {ed}, {eh}, {ei})\n"
            + "tell application \"Calendar\" to launch\n"
            + "tell application \"Calendar\"\n"
            + cal_selector
            + f"make new event at theCal with properties {props}\n"
            + "end tell\n"
            + "return \"OK\"\n"
        )

        out = _run_osascript(script)
        return out.strip() == "OK"

    # 保留旧方法名以兼容占位调用
    def set_event(self, event: Dict[str, object]) -> bool:
        """
        兼容旧接口：从字典创建事件
        需要字段: title(str), start(str), end(str)
        可选字段: location(str), calendar_name(str)
        """
        title_obj = event.get("title")
        start_obj = event.get("start")
        end_obj = event.get("end")

        missing = [k for k, v in {"title": title_obj, "start": start_obj, "end": end_obj}.items() if not isinstance(v, str) or not v]
        if missing:
            raise ValueError(f"缺少或类型不正确的必填字段: {', '.join(missing)}")

        title: str = title_obj  # type: ignore[assignment]
        start: str = start_obj  # type: ignore[assignment]
        end: str = end_obj      # type: ignore[assignment]

        location_obj = event.get("location")
        location: Optional[str] = location_obj if isinstance(location_obj, str) and location_obj else None

        cal_obj = event.get("calendar_name")
        calendar_name: Optional[str] = cal_obj if isinstance(cal_obj, str) and cal_obj else None

        return self.create_event(title, start, end, location, calendar_name)