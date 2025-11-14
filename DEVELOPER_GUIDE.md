# 开发者指南

本指南汇总了在开发与扩展 D2 时的注意事项与建议。

## 开发者提示
- `LLMService.query(model, prompt)` 为两参数；不要使用命名参数（如 `sys_msg=`）。
- 在修改 AppleScript 文本时，注意 Python 字符串中的引号与逗号的转义；推荐将 `", "` 作为字面量拼接。
- 若需要扩展新的路由或动作，优先更新主智能体的语义解析 schema 与示例，同时保持关键词兜底一致。

## 本地开发环境（建议）
- 使用项目级虚拟环境以规避系统环境的 PEP 668 限制：
  - 创建：`python3 -m venv venv`
  - 激活（macOS）：`source venv/bin/activate`，退出：`deactivate`
  - 安装依赖：`pip install -r requirements.txt`
- VS Code 解释器：工作区已配置为 `${workspaceFolder}/venv/bin/python`，如需手动切换：
  - `Cmd+Shift+P` → `Python: Select Interpreter` → 选择 `venv`
  - 如遇解析异常，执行：`Python: Restart Language Server` 或重载窗口

## 运行 GUI（开发模式）
- 在项目根 `D2/` 下：
  - `uvicorn gui.server:app --host 127.0.0.1 --port 8186 --reload`
  - 或显式使用虚拟环境解释器：`venv/bin/python -m uvicorn gui.server:app --host 127.0.0.1 --port 8186 --reload`

---
欢迎根据你的工作流继续扩展，例如：只处理指定邮箱/文件夹、仅处理含附件的会议邀请、自动选择工作日历等。

## 工作流扩展与参数策略
- GUI 偏好设置：在页面“偏好设置”中可配置：
  - 默认邮箱（文件夹）：用于邮件列表与会议处理的默认来源
  - 默认日历名称：用于日历查询与事件创建的默认目标
  - 仅处理含附件的会议邀请：会议处理时仅保留包含附件的邮件
  - 优先未读：在邮件列表与会议处理时优先筛选未读
- 服务应用位置：
  - 偏好在服务器端生效，`/api/dialog` 会设置主代理偏好
  - 代码引用：
    - 偏好更新：`D2/gui/server.py:100-116` 使用 `agent.update_preferences(...)`
    - 主代理偏好字段：`D2/main_agent/d2_main.py:18-21` 定义 `self.preferences`
    - 日历查询偏好：
      - 快路径：`D2/main_agent/d2_main.py:106-109` 传入 `calendar_name`
      - 语义路由：`D2/main_agent/d2_main.py:141-147` 合并 `calendar_name`
      - 关键词兜底：`D2/main_agent/d2_main.py:611-614` 传入 `calendar_name`
    - 邮件列表偏好：
      - 语义路由：`D2/main_agent/d2_main.py:305-311` 合并 `mailbox_name` 与 `unread_only`
      - 会议处理回退：`D2/main_agent/d2_main.py:451-454` 合并 `mailbox_name` 与 `unread_only`
      - 关键词兜底与交互：`D2/main_agent/d2_main.py:743-746`, `867-869`, `953-955`
    - 会议附件过滤：`D2/main_agent/d2_main.py:748-756` 基于 `save_attachments_by_ids` 进行筛选
- 模板与表单：
  - 前端模板新增偏好控件：`D2/gui/templates/index.html:54-69, 75-82, 246-252`
  - 服务端表单字段：`D2/gui/server.py:94-101` 支持 `mailbox_name`, `calendar_name`, `only_with_attachments`, `unread_only`

## 常见扩展示例
- 只处理指定邮箱/文件夹：在 GUI 偏好中填写目标文件夹名称（如 `Archive`），或在指令中包含该名称，系统将优先使用该来源。
- 仅处理含附件的会议邀请：勾选偏好后，会议处理流程会先尝试保存附件并仅保留有附件的邮件进行识别与摘要。
- 自动选择工作日历：在偏好中填写日历名称（如 `Work`），创建事件与查询日程时将优先使用该日历。
