# Changelog

所有值得注意的更改都会记录在此文件中。遵循语义化版本（SemVer）。

## v0.3.0 — 2025-11-14

标签：`v0.3.0`  ·  提交：`94ae906`

### 性能
- 邮件列表/详情查询加入缓存（TTL 45s），重复查询快速返回；在标记与移动操作后自动失效相关缓存。
- 会议识别批量化：多封邮件元数据一次性提交 LLM，减少逐封推理带来的延迟。
- 会议摘要并行生成：对识别为会议的邮件并行生成要点摘要（最多 4 并发）。
- GUI 启动预热：后台预热“今日未读邮件（10条）”与日历事件，提升首请求速度。

### 功能
- GUI 偏好选项：支持默认邮箱（文件夹）、默认日历、仅处理含附件的会议邀请、优先未读。
- 路由解析：强化 JSON 提示与提取，支持代码块与括号匹配，降低非结构化输出的影响。

### 稳健性与兼容
- 动态导入 `ollama/openai`，避免在未安装依赖时发生 ImportError。
- AppleScript 兼容性：将“≥”修正为 ASCII `>=`，并缩短激活延时（0.5s → 0.2s）。
- 安全与交互：新增 CORS 中间件；上传大小限制（音频 50MB、文档 10MB）与过期清理（24h）。
- 文档：`DEVELOPER_GUIDE.md` 补充参数策略、偏好生效位置与代码引用。

### 变更范围
- `services/llm_service.py`
- `mcp_agents/email_agent/main.py`
- `mcp_agents/meeting_agent/main.py`
- `main_agent/d2_main.py`
- `gui/server.py`
- `gui/templates/index.html`
- `requirements.txt`
- `DEVELOPER_GUIDE.md`

## v0.2.0 — 2025-11-12

标签：`v0.2.0`  ·  提交：`a7068e8`

### 新增
- 前端：为语音指令触发的提问，自动使用浏览器 `speechSynthesis` 进行 TTS 播放。
- 路由/接口：`/api/dialog` 输出支持后处理润色；`D2Agent.polish_text` 将回复改写为更口语化、自然、简洁的中文。
- 代理：新增 `mcp_agents/meeting_minutes_agent/main.py` 初始实现，用于将会议原始记录整编为会议纪要。

### 改动
- GUI：`index.html` 中的 `ask` 支持 `fromVoice` 标记语音来源；在语音来源的回复中自动触发 TTS。
- UI 整理：移除多余调试输出，保持对话界面整洁。

### 修复/兼容
- 远程推送：在 SSH 端口受限时，支持通过 HTTPS 推送仓库与标签（无需修改 `origin` 配置）。

### 备注
- 版本标签已创建并推送：`v0.2.0`。
- 浏览器兼容性：TTS 在 Chrome/Edge 表现良好；Safari 需允许页面播放声音。

---

变更依据：提交信息与功能实现包括但不限于：
- `feat(dialog): polish responses; tts for voice; clean UI logs`
- `server.py` 的对话输出润色调用（`agent.polish_text`）。
- `index.html` 的 `speakText` 与 `ask({ fromVoice })` 支持。
- `meeting_minutes_agent/main.py` 的会议纪要生成入口。
