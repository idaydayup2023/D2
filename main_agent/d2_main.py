import os
import importlib
import sys
from datetime import datetime, timedelta, date, time
import json
import re
from typing import Optional, Dict, Any

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from services.llm_service import LLMService

class D2Agent:
    def __init__(self):
        self.name = "D2"
        self.mcp_agents = []
        self.llm_service = LLMService()
        self.load_mcp_agents()

    def _find_calendar_agent(self):
        for agent in self.mcp_agents:
            if getattr(agent, 'name', '').startswith('日历'):
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
        # 优先使用大模型语义解析来路由
        parsed = self._llm_semantic_parse(prompt)
        if parsed and parsed.get('route') == 'calendar':
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
        keywords = ['日程', '日历', '安排', '会议', '事件']
        if not any(k in prompt for k in keywords):
            return None
        cal = self._find_calendar_agent()
        if not cal:
            return None
        start_iso, end_iso = self._resolve_range_from_prompt(prompt)
        try:
            events = cal.get_events(start=start_iso, end=end_iso)
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
            "只输出 JSON，不要输出其他文字。"
            "字段包括: route<'calendar'|'email'|'none'>, action<'list'|'create'|null>,"
            " start<YYYY-MM-DDTHH:MM:SS或null>, end<同上或null>, calendar_name<或null>,"
            " title<创建事件标题或null>, location<或null>, remaining<boolean，用于'还有/剩余/剩下'>。"
            " 语义规则示例：'本周还有哪些日程' -> route:'calendar', action:'list', start:现在, end:本周日23:59, remaining:true;"
            " '帮我添加明天上午10点的会议到日历' -> route:'calendar', action:'create', title:'会议', start:明天10:00, end:明天11:00。"
            f" 当前时间: {now_iso}。若用户未给日历名，不必填 calendar_name。"
        )
        user_msg = f"用户指令：{prompt}。请严格返回JSON。"

        try:
            raw = self.llm_service.query(best_model_str, f"系统: {sys_msg}\n用户: {user_msg}")
        except Exception:
            return None

        # 保证字符串类型，避免 None 或非字符串导致的类型问题
        raw_str: str = "" if raw is None else str(raw)

        # 尝试提取第一个 JSON 文本块
        m = re.search(r"\{[\s\S]*\}", raw_str)
        text: str = m.group(0) if m else raw_str

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
        
        best_model = self.llm_service.get_best_model()
        if not best_model:
            print("没有可用的大模型服务。退出。")
            return
        best_model_str: str = str(best_model)
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
                    response = self.llm_service.query(best_model_str, prompt)
                print(f"D2: {response}")
            except KeyboardInterrupt:
                break

if __name__ == "__main__":
    agent = D2Agent()
    agent.start()