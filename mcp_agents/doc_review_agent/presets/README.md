# 文档审查提示词目录说明

- 文件命名：`<id>.json`
- 语法（JSON）：
  - `id`: 预设ID（可选，若省略则使用文件名）
  - `name`: 预设名称
  - `description`: 预设描述
  - `keywords`: 用于匹配需求的关键词数组
  - `target_types`: 偏好文件类型（如 `pdf`, `docx`, `md`）
  - `language`: 输出语言（如 `zh`, `en`）
  - `template`: 审查提示词模板（字符串）

示例：
```json
{
  "id": "quality_review",
  "name": "质量规范审查",
  "description": "按质量规范审查技术文档并给出改进建议",
  "keywords": ["质量", "规范", "技术文档", "审查"],
  "target_types": ["pdf", "docx", "md"],
  "language": "zh",
  "template": "你是一位严谨的文档审查专家。请根据质量规范，审查以下文档：\n- 指出结构性问题、语言表达问题、术语一致性问题\n- 给出具体、可操作的改进建议\n- 在结尾给出总体评价"
}
```

提示：可以在此目录中添加多个 `.json` 文件以覆盖不同审查场景；智能体会根据需求文本自动推荐最合适的预设。