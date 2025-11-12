# D2 智能助理

D2 是一个在本地运行的多智能体助手，聚焦日历、邮件与文本摘要等高频工作流，通过路由解析自动选择合适的智能体完成任务。项目在 macOS 上工作良好，并尽量依赖系统内置能力（如 Apple Mail 与 AppleScript）。

## 项目概览
- 主智能体：`main_agent/d2_main.py`，负责语义解析与路由分发。
- 邮件智能体：`mcp_agents/email_agent/main.py`，读取/发送/标记/移动邮件、获取正文与账户地址。
- 日历智能体：`mcp_agents/calendar_agent/`，读取与创建日历事件。
- 摘要智能体：`mcp_agents/summary_agent/`，对文本/文档生成摘要，支持多种输出格式。
- 会议处理智能体：`mcp_agents/meeting_agent/`，统一封装会议邮件处理流程（摘要+解析+自动入日程），主路由将优先委派。
- 文档审查智能体：`mcp_agents/doc_review_agent/`，根据预设提示词审查附件内容，并将审查结果写入邮件草稿。
- LLM 服务：`services/llm_service.py`，提供本地模型查询能力（`query(model, prompt)`）。
- 语音转文字（STT）：`services/stt_service.py`，支持 Whisper 与 Fun-ASR，支持模型预下载与离线运行。
- 图形界面（GUI）：`gui/server.py` 与 `gui/templates/index.html`，提供网页交互界面。

## 主要特性
- 语义路由：支持 `calendar`、`email`、`summary`、`stt` 四大路由与动作解析。
- 会议处理：`email` 路由新增 `process_meetings` 动作，可自动识别会议邀请并在满足条件时自动加入日程。
- 文本摘要：支持可选输出格式——要点 (`bullets`)、结构化大纲 (`outline`)、结论+建议 (`conclusion_recommendations`)。
- 文档审查：当邮件包含“审查/审核/评审”等需求且涉及附件时，主路由可执行 `review_attachments` 动作，自动推荐审查模板，提取附件文本，生成审查报告，并写入草稿回复。
- 关键词兜底：当用户输入包含“会议通知/会议邀请/摘要/要点”等关键词时，自动触发相应处理流程。
- 语音转文字：支持 Whisper/Fun-ASR，本地与离线模型均可，适配常见格式（如 `wav/mp3/m4a/mp4`）。

## 运行环境
- 操作系统：macOS（需启用系统“邮件”应用）。
- 权限要求：允许终端/IDE 执行 `osascript` 控制“邮件”（系统设置中的“隐私与安全性”→“自动化/辅助功能”可能需要授权）。
- Python：建议 3.9+。
- 本地 LLM：建议本机可用的模型服务，`LLMService.query(model, prompt)` 为两参签名；若无可用模型，部分解析会退化为关键词与规则，精度会下降。
- STT 依赖：
  - Whisper：`pip install whisper`，并且系统需安装 `ffmpeg`（macOS 可用 `brew install ffmpeg`）。
  - Fun-ASR：`pip install funasr modelscope`，首次加载自动缓存到本地；也可通过预下载离线运行。

## 启动方式
- 直接运行主智能体：
  ```bash
  python D2/main_agent/d2_main.py
  ```
- 主智能体会加载 MCP 子智能体并进入交互循环。

### 启动图形界面（GUI）
- 安装依赖（推荐）：
  ```bash
  pip install -r requirements.txt
  ```
- 或手动安装：
  ```bash
  pip install fastapi uvicorn jinja2 python-multipart
  ```
- 启动：在 `D2/` 目录下执行
  ```bash
  uvicorn gui.server:app --host 127.0.0.1 --port 8186 --reload
  ```
- 访问：打开浏览器访问 `http://127.0.0.1:8186/`
- 特性：聊天式布局，使用 Tailwind CDN，输出整洁美观；复用核心路由与兜底逻辑（`process_prompt`）。

## 版本变更
- 当前版本：`v0.2.0`
- 详细更新日志：参见 [`CHANGELOG.md`](./CHANGELOG.md)

## 使用示例（自然语言）
- 日历：
  - “本周还有哪些日程”
  - “帮我添加明天上午10点的会议到日历”
- 邮件：
  - “列出今天未读邮件”
  - “发一封邮件给张三，主题是会议，内容是安排如下”
- 会议处理：
  - “处理今天的会议邀请邮件并识别是否加入日程”
  - 当输出中出现“是否加入日程？请回复‘加入第N封’”，可直接输入：“加入第3封”。
- 摘要：
  - “请用要点总结这段文本：……”
  - “把 /Users/me/report.docx 概括为结构化大纲，语言用中文”
- 语音转文字（STT）：
  - “把 /Users/me/audio.mp3 转成中文文字”
  - “用 fun-asr 识别 /Users/me/meeting.wav”

## 路由与动作参考（语义解析）
- 路由：`route<'calendar'|'email'|'summary'|'stt'|'none'>`
- 常见动作：
  - `calendar`: `list`, `create`
  - `email`: `list`, `send`, `mark`, `move`, `verify`, `process_meetings`, `review_attachments`
  - `summary`: `summarize`
  - `stt`: `transcribe`
- 常用字段（节选）：
  - 日历：`title`, `start`, `end`, `calendar_name`
  - 邮件：`email_to`, `email_subject`, `email_body`, `email_attachments`, `email_ids`, `email_move_to`, `unread_only`, `review_requirements`, `review_preset_id`
  - 摘要：`summary_text`, `summary_file_path`, `summary_style`, `summary_length`, `summary_language<'zh'|'en'>`, `summary_format<'bullets'|'outline'|'conclusion_recommendations'>`
  - 语音转文字：`audio_file_path`, `stt_backend<'whisper'|'funasr'>`, `stt_model`, `stt_language<'zh'|'en'>`

## 文档审查流程（review_attachments）
该能力由独立智能体 `doc_review_agent` 提供，主路由在邮件场景下调用。
1. 根据用户的“审查/审核/评审”等需求，从预设提示词目录加载模板（可根据 `review_requirements` 推荐，或通过 `review_preset_id` 指定）。
2. 保存目标邮件的所有附件到临时目录（仅支持可提取文本的格式：`txt/md/docx/pdf`）。
3. 复用摘要智能体的文本提取能力，对每个附件构造审查提示并调用本地 LLM。
4. 汇总生成审查报告文本。
5. 为目标邮件创建草稿回复，并将审查报告写入正文（默认主题前缀：`审查结果 - `）。
6. 交互支持：“审查第N封”将对当前列表中的第 N 封邮件的附件执行审查并写入草稿。

## 会议自动处理流程（process_meetings）
该能力已抽象为独立智能体 `meeting_agent`，主路由会优先委派；不可用时回退到内置逻辑。
1. 列取目标时间范围内的邮件（默认未读）。
2. 通过 `get_messages_by_ids` 获取正文、收件人/抄送、发件人等详细信息。
3. 调用摘要智能体生成要点式摘要（`output_format='bullets'`）。
4. 调用本地 LLM 解析会议信息（严格 JSON）：
   - `is_meeting`、`title`、`start`、`end`、`location`、`attendees`、`includes_me`。
   - 时间尽量使用本地时区 ISO 格式（`YYYY-MM-DDTHH:MM:SS`）。
5. 如果 `includes_me == true` 且存在有效 `start`，自动创建日历事件；否则提示“加入第N封”。

## 文本摘要格式
- `summary_format`: 可选输出格式。
  - `bullets`：要点列表，适合快速浏览。
  - `outline`：结构化大纲，层次清晰。
  - `conclusion_recommendations`：结论与建议三段式，适用于决策场景。

## 邮件智能体能力
- 列出邮件：`list_emails(start, end, unread_only, limit, mailbox_name)`
- 读取正文：`get_messages_by_ids([msg_id])` 返回 `subject/sender/date/mailbox/read/to/cc/body`。
- 获取账户地址：`get_account_addresses()`，用于判断会议是否包含“我”。
- 发送：`send_email(to, subject, body, cc, bcc, attachments)`。
- 标记：`mark_messages_by_ids(message_ids, read)`。
- 移动：`move_messages_by_ids(message_ids, target_mailbox_name)`。
- 凭证验证：`verify_credentials(email_address, password, imap_server, smtp_server, imap_port, smtp_port)`。
- 保存附件：`save_attachments_by_ids(message_ids, target_dir)`。
- 创建草稿回复：`create_draft_reply_by_message_id(message_id, subject_prefix, body)`。

## 常见问题
- AppleScript 执行失败：
  - 确认“邮件”已配置至少一个账户并已登录；
  - 在“隐私与安全性”中允许终端/IDE 控制“邮件”；
  - 运行 `osascript -e 'tell application "Mail" to get name of accounts'` 验证权限。
- 会议未能自动加入：
  - 邮件正文可能未包含清晰的时间；
  - 本地 LLM 不可用或输出未能匹配严格 JSON；
  - 可用“加入第N封”手动确认后创建事件。
- 摘要格式不生效：
  - 确认传入了 `summary_format` 字段，或在自然语言中明确“要点/大纲/结论+建议”。

## 目录结构（节选）
```
D2/
├── main_agent/
│   └── d2_main.py
├── mcp_agents/
│   ├── calendar_agent/
│   ├── email_agent/
│   │   └── main.py
│   ├── meeting_agent/
│   │   └── main.py
│   ├── doc_review_agent/
│   │   ├── main.py
│   │   └── presets/
│   └── summary_agent/
└── services/
    ├── llm_service.py
    └── stt_service.py

## 语音转文字（STT）
- 后端选择：
  - `whisper`（OpenAI whisper，本地运行，需 `ffmpeg`）。
  - `funasr`（基于 ModelScope 的 Fun-ASR 管线）。
- 离线模型预下载：
  - Whisper：
    ```bash
    pip install whisper
    brew install ffmpeg  # macOS
    ```
    ```python
    from services.stt_service import STTService
    STTService().prepare_model(backend='whisper', model='base')
    ```
  - Fun-ASR：
    ```bash
    pip install funasr modelscope
    ```
    ```python
    from services.stt_service import STTService
    # 预下载常用中文模型（如需英文将 language 改为 'en' 或指定模型ID）
    STTService().prepare_model(backend='funasr', language='zh')
    ```
- 使用方式（自然语言路由）：
  - “把 /Users/xx/audio.mp3 转成中文文字” → `route:'stt', action:'transcribe'`。
  - “用 fun-asr 识别 /Users/xx/meeting.wav” → `route:'stt', action:'transcribe', stt_backend:'funasr'`。
- 直接调用服务（代码）：
  ```python
  from services.stt_service import STTService
  svc = STTService()
  res = svc.transcribe('/Users/me/audio.mp3', backend='whisper', language='zh')
  print(res['text'])
  ```
```

## 开发者指南
开发者相关提示与本地开发说明已迁移至独立文档：
- 请参见 [`DEVELOPER_GUIDE.md`](./DEVELOPER_GUIDE.md)

---
欢迎根据你的工作流继续扩展，例如：只处理指定邮箱/文件夹、仅处理含附件的会议邀请、自动选择工作日历等。