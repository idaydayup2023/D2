# Changelog

所有值得注意的更改都会记录在此文件中。遵循语义化版本（SemVer）。

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