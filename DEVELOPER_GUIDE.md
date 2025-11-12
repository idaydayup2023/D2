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