# 当前工作台数据约定（2026-10-11）

保留外层 collection/id/version/created_at/updated_at 和自由 data；未知字段必须保留。jobs 工作台字段：company/title、jd/url/boss_url、salary/city/education/experience/tags、recruitment_year/target_cohort/eligible_years、qualification/qualification_evidence、interest/color/color_reason、cycle/notes、artifacts、application_history/outcome、dedupe_keys。年份字段不互相推断；未知资格不排除。

stage 为 pool/preparing/applied/interview/ended/trash。资格 unknown/eligible/disadvantage 与兴趣分别记录。灰色代表未知资格或缺 JD，黄色代表已知不利条件，符合且有兴趣为绿色，符合但兴趣未定为米色。人工颜色需原因，不能掩盖缺失资格/JD。

profiles 中 `company-quotas` 的 items 为 company/cycle/limit。企业同批次已申请记录和历史计入名额；超额必须确认并保存 quota_override_reason。垃圾桶保留原始去重标识，不能自动恢复。

`profiles/candidate` 保存 confirmed（已确认事实）、pending（未确认细节）、preferences 和 Notion 来源引用，仅存私有层。Notion 是本轮读取来源，没有双向同步；偏好不是事实，未确认经历不能当成简历内容。jobs.screening 保存 profile_id/version、逐项 checks、questions、职责摘录及人工讨论提示，不给匹配分数。recruitment_year 是发布日期年份；允许毕业年份只能依据 JD 或招聘活动的明确窗口。

目录发现写入 `leads/discovered-*`：保留原链接、稳定来源标识、源响应引用、job_id、detail_checked_at、detail_error、retry_after。已有完整 JD 跳过；失败保留入口，24 小时后可重试。Notion 历史线索以独立 ID 保留原行，未重核验不冒充已取 JD 岗位。jobs.dedupe_keys 合并目录与详情别名，包含垃圾桶记录，不自动恢复。

发现运行 runs 保存真实开始/结束时间、每来源 listed/directory_pages/details_attempted/waiting、new_job_ids、incomplete_jd_ids、errors、trash_skipped。waiting 是本批限额下未尝试的详情，不能记为失败。sources 的 scope 与原始响应记录真实覆盖范围；每次重新枚举目录检查新发布。来源原文件为 source_evidence，不能充当简历。

artifacts 的 data 保存 name/kind/sha256/size 等，二进制位于私有 attachments/{sha256}，jobs.artifacts 关联记录。真实 PDF/DOCX 可上传下载且核验哈希；仅名称/引用的旧附件标记「原文件未恢复」。来源资料与简历明确区分。

SQLite 是私有本地工作副本；私有 GitHub JSON 保存当前记录与完整历史，二进制另存。每次写入事务同时追加历史和待同步；导入验证完整历史后保留原 ID/版本。下面旧约定仍可帮助理解自由字段；其中「只保存附件引用」「共享存储以后再做」已被上述实现取代。

---
## 旧自由记录建议（历史保留）

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
