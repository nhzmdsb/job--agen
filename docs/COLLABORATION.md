# 共享工作台协作协议 V0

公开代码仓库不接收个人资料。共享权威源为已确认 private 的 `nhzmdsb/job--agen-data`；SQLite、附件、设置、备份位于本地私有目录。凭据只由已授权 Git 凭据管理器使用，不能复制到文件、日志、Prompt 或浏览器。

## 本地接口

工作台、CLI、MCP 共用 Store。HTTP 默认 http://127.0.0.1:18765 ，只接受本机 Host；修改请求包含 `X-Workspace: 1`，浏览器 Origin 必须同源。

| 操作 | 接口 |
| --- | --- |
| 查询/新增岗位 | GET /api/jobs；POST /api/jobs，JSON {data} |
| 读取/版本更新 | GET /api/records/{collection}/{id}；PATCH 同一路径，JSON {expected_version,patch,reason} |
| 原文件 | POST /api/jobs/{id}/artifacts，multipart file 与 expected_version；GET /api/artifacts/{id}/download |
| 准备 Prompt | POST /api/jobs/{id}/prompt，JSON {task,description} |
| 企业名额 | PUT /api/quotas，JSON {expected_version,items:[{company,cycle,limit}]} |
| 同步 | GET /api/sync；POST /api/sync |
| 明确合并冲突 | POST /api/conflicts/{collection}/{id}/resolve，JSON {expected_version,data} |

task 为 research/strategy/resume/interview；可查页面选项。HTTP 409 表示需重新读取，禁止自动覆盖。PATCH 保留未知字段。超额进度写入需要 quota_confirm=true 和非空 quota_reason。上传只接受有效 PDF/DOCX，最大 8 MiB，保存真实字节并检查 SHA-256。

CLI `python -m job_agent.cli` 的 list/get/put/history/export/import/sync 使用同一数据。put 从标准输入接收完整 data，必须保留未知字段并传 --expected-version；import 从标准输入接收 export 快照，验证后原子恢复原 ID/版本/历史。MCP `python -m job_agent.mcp_server` 保留 stdio 工具和同样的版本化写入规则。没有内置 LLM，不自动投递。

## 获授权的云端会话直接写私有 GitHub

1. 再次读取仓库元信息确认 private；读取默认分支和目标文件及 blob SHA。
2. 记录路径为 `records/{collection}/{sha256(id 的 UTF-8 字节)}.json`。文件为 `{schema_version:1,record:envelope,history:[完整版本记录]}`。以现有文件为模板，不改 ID/created_at，不丢未知字段和完整历史。
3. 检查原 version，修改 data，version 加一，updated_at 使用 UTC ISO 时间；向 history 追加相同 envelope 和 actor/reason。历史版本从 1 连续到当前版本，最新历史与当前记录一致。
4. 用 GitHub Contents API PUT 并提供刚读到的文件 SHA。409/422 时重新读取，保留自己的建议内容供合并；不强制写、不覆盖其他会话。
5. 原文件先保存为 `attachments/{sha256}`，读回校验内容哈希，再写 artifacts 记录并把 ID 加到岗位 artifacts 数组。只有名称/摘要不得声明已保存原文件。

新增岗位保留原始 URL、完整 JD 或招聘入口、访问时间和资格证据。先查全部岗位，包括垃圾桶与 dedupe_keys；重复不恢复、不再推荐。招聘活动年份、目标届别、允许毕业年份分开；未知资格保持 unknown，不打分、不因兴趣不明排除。生命周期改变追加 application_history；企业同批次名额超额只能在明确确认后记录原因。用户控制简历、策略及最终申请。

本地同步合并不同记录；同一记录两端修改保留双方，用户合并后保存新版本，并将两条完整历史保存到私有 conflict-history。禁止直接删除远端记录来清理：使用 trash，保留去重和历史。远端删除被视作需处理的冲突。

本地写入后的同步、启动/刷新检查和六小时维护需要本机开机、应用运行及网络；离线仍可本地编辑，之后重试。外部招聘登录/反爬限制应记录来源状态并保留线索，不阻塞核心工作台。
