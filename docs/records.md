# 自由记录的建议约定

所有记录由外层统一管理 `collection/id/version/created_at/updated_at`，AI 自由管理内层 `data`。引用其他记录时使用其 collection 和 id。以下字段仅供协作参考，不是数据库约束。

| 集合 | 建议内容 |
|---|---|
| sources | name、url、access_method、scope、instructions、enabled、checkpoint、last_checked_at |
| leads | source_id、source_url、captured_at、raw_text 或 raw_reference、notes、job_ids |
| jobs | company、title、locations、jd、apply_urls、source_refs、eligibility、assessment、open_questions、verified_at、status |
| runs | source_ids、scope、started_at、finished_at、status、checkpoint、lead_ids、job_ids、errors、next_action |
| artifacts | job_id、kind、content 或 file_reference、notes |
| profiles | 求职背景与偏好，按用户提供的内容保存 |

例如 eligibility 可写成 `{ "decision": "unknown", "reason": "尚未找到毕业年份要求", "evidence_refs": [] }`。没有固定决策枚举或状态转移限制。

来源 checkpoint 可以是行号、最后处理的链接或自然语言说明，由 AI 根据来源决定。AI 需记录处理范围和未完成项，不能把单纯访问过页面当作已处理全部内容。

截图和 PDF 目前只保存引用；读取与识别使用 Agent 现有工具。文件引用本身不上传或复制附件。SQLite 是当前本地持久化实现，不代表提前确定最终共享存储为 SQLite，也没有 Notion 双向同步。
