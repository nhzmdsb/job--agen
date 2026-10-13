# Issue #8 Worker 契约

本批让 Agent 复用已保存的岗位、JD、翻译和判断。程序负责身份、证据格式、版本与编辑边界；Agent 负责语义判断。没有新增调度器或渠道采集器。

## 一轮执行

1. 读取 `worker_context`，取得已确认条件、有效条件标识和分页待办。跟随 `next_offset`；处理过程中待办会缩短，下一轮重新从 offset 0 获取，不能边修改边递增偏移而漏项。
2. 枚举本轮入口的全部信息。公司目录、汇总表行保存在 sources/leads；取得具体岗位身份后才创建 job。旧岗位出现不能作为停止扫描的依据。
3. 每批最多 200 个身份调用 `lookup_jobs`。优先 `source + source_job_id`，URL/别名补充。结果包含机器排除、回收站和合并记录；合并记录应继续查其 `merged_into`。不得自动恢复回收站，不得按相似标题合并。
4. 对照待办 `tasks` 只补缺少的步骤：`detail`、`translation`、`assessment`、`source_verification`。完整缓存 JD 可直接重核。英文翻译使用上下文的 `translation_hash`；判断使用 `job_hash` 和当前档案版本。
5. 先保存事实，再保存判断。判断失败时保留事实并记录未完成步骤，下轮只补判断。所有已有岗位修改使用选择性更新。
6. 保存 runs：列表发现、详情读取、跳过、重新判断、失败数量及 `remaining`。每个成功步骤后保存进度；报告“明确建议 → 依据 → 下一步动作”。没有有价值的新结果时不要重复报告旧机会。

## 先粗筛、按公司并行、困难入口停止投入

先比较列表已明确给出的资格与已确认条件，优先深读更可能匹配的一小批 JD。只有可靠证据表明硬性不符时才排除；未知资格继续保留，不能仅凭“2027 校招”等活动标签排除。列表只够粗筛时保存 lead 和依据，不用目录或汇总表行冒充完整岗位判断。本轮选定范围仍须完整枚举，分别记录未读、略过和失败。

使用现有工具按公司并行，各公司保留独立页面、来源上下文和进度；不新增并发调度器。切换前程无忧等页面的 `#` 路由后，核对岗位 ID、标题与实际正文，出现旧岗位内容时刷新核对，不能保存串页 JD。具体岗位入口用 `detail_url` 保存，页面优先展示它；公司目录另存 sources/leads。查重保留有岗位身份意义的 `#` 路由、`JobID` 和未知参数，仅去掉已确认的跟踪参数。

困难入口尝试合理替代后及时停止投入，继续其他公司。主动略过与读取失败不同：失败沿用退避/重试；略过使用已有 lead 选择性更新接口记录：

```json
{"expected_version":3,"patch":{"status":"skipped","skip_reason":"虚构示例：登录阻止读取，替代入口也不可用","skipped_at":"2026-10-13T00:00:00Z","reopen_when":"本人要求、出现具体新线索或访问能力修复"},"reason":"停止本轮投入并保留入口"}
```

通过 `PATCH /api/worker/records/leads/ID`（`X-Workspace: 1`）、MCP `patch_record` 或 CLI `patch leads --id ID --expected-version N --reason "停止本轮投入"` 提交；CLI 输入仅为上述 patch 对象。自由字段已能表达，不新增接口。

读取 sources/leads 时排除 `status=skipped` 的详情任务，runs.remaining 也不再携带它；新一轮枚举、`--retry` 或 `--refresh` 都不能自动重开。只有本人要求、具体新线索或能力修复后，重新读取该 lead，说明重开依据，并用预期版本明确改 `status=pending`，再补未完成步骤。略过入口不删除已保存岗位，也不取消该岗位独立的翻译、判断或重核任务。不要将所有旧 pending 批量改成略过。

## 三个入口共用存储规则

| 操作 | CLI | MCP | HTTP |
| --- | --- | --- | --- |
| 上下文 | `worker-context --limit 50 --offset 0` | `worker_context` | `GET /api/worker/context?limit=50&offset=0` |
| 批量查重 | `lookup-jobs`，输入身份数组 | `lookup_jobs(identities)` | `POST /api/worker/lookup`，`{"identities":[...]}` |
| 创建 | `put jobs --expected-version 0`，输入 data | `write_record` | `POST /api/worker/records/jobs` |
| 选择性更新 | `patch jobs --id ID --expected-version N`，输入 patch | `patch_record` | `PATCH /api/worker/records/jobs/ID` |
| 合并 | `merge-jobs`，输入双方版本和确认 | `merge_jobs` | `POST /api/worker/merge` |

HTTP 写入需要 `X-Workspace: 1`，沿用本地工作台 Origin 检查。CLI 全局 `--db PATH` 放在命令之前；MCP 使用 `JOB_AGENT_DB`。运行真实服务沿用既有启动方式。

上下文返回 `profile`（已确认信息及实际记录版本）、`effective_profile_hash`（有效条件版本）、`items`、`count`、`next_offset`。每项含 `record`、`tasks` 和输入标识；不默认返回历史。来源列表另外分页读取 `sources`/`leads`。

创建的 HTTP 请求形如 `{"id":"demo","expected_version":0,"data":{...}}`；更新形如：

```json
{"expected_version":3,"patch":{"notes":"已核实职责","tags":["运营"]},"reason":"补充本轮核验结果"}
```

选择性更新返回 `record`、`version`、`action`（created/updated/unchanged）。兼容旧调用的 CLI put/MCP write_record 仍返回记录 envelope。对象递归合并，数组整体替换；未提交字段和附件关联保留。`null` 是写入空值，不是删除指令。即使内容无变化也先检查版本；无变化不增加岗位版本、历史或同步任务。

冲突时 HTTP 返回 409；CLI 非零退出；MCP 返回工具错误。重新读取记录，只重新提交本次确实要改的字段，不把旧快照整份重发。重核/翻译必须使用重新读取后的事实与条件。

## 判断、翻译和本人保护

`screening.work` 沿用六项证据：graduation、education、major、experience、responsibilities、preferences；包含 decision、reason、checks、questions、profile_version、job_hash、assessed_at。checks 的确定结论须引用已保存 JD/毕业窗口原文。结论由 Agent 作出，程序不会替 Agent 推断个人资格。保存时补有效条件标识及本批判断规则版本。

绿色 eligible 不依赖“感兴趣”；黄色 disadvantage 表示明确不利因素；灰色 unknown 表示读过 JD 后仍有重要歧义。无 JD、读取失败或只有事实没有判断均为内部待办。旧推荐重核时保留旧色、依据并提示“待重核”；旧米色不会自动升级。

翻译写入 `translation: {source_hash, title_zh, summary_zh, jd_zh}`，source_hash 使用 `translation_hash`。原文变化才失效；城市、标签变化不触发翻译。判断比较岗位事实及有效条件；普通备注、待确认资料不会触发。历史可验证的旧结果继续复用，其余进入待办，读取不会重写全库。

本人颜色及理由、`human_judgment` 受保护；历史来源不明的颜色也保护。普通 notes/tags/interest/stage 可以编辑。修改受保护判断前必须在对话中询问本人；获得明确同意后提交 `human_confirm: true`、`confirmation_reason`、本次修改 `reason` 和 `expected_version`。CLI 对应 `--human-confirm --confirmation-reason "本人同意的具体修改" --reason "修改依据"`。确认依据进入历史。操作者名称不授予权限，`judgment_authorship` 由存储层管理。

确认参数是 Agent 履行对话约定的声明，程序不能独立验证对话是否真的获得同意；它防止误覆盖与名称绕过，不是用户身份认证系统。

## 刷新、失败和合并

不按 7 天/两周强制重读。仅来源明确变化、发现具体更新线索或本人要求才刷新详情。刷新失败保留旧 JD、翻译与判断；lead 分开保存 `detail_attempted_at`、`detail_succeeded_at`、`detail_error`、`retry_after`。现有发现器保留 24 小时退避；修复能力后的明确重试使用 `--retry`，主动刷新使用 `--refresh`。这些开关须有本轮任务依据，不能作为每轮默认参数。

明确确认同一岗位后，合并输入 `target_id`、`other_id`、`target_version`、`other_version`、`same_job_confirm: true`、`reason`。同时检查双方版本，保留双方来源、别名、证据、附件及历史。合并不是替换目标的本人判断。

## 可执行虚构演示

在仓库根目录运行 `.venv\Scripts\python.exe examples\worker_demo.py`。只使用临时 SQLite 和 example.invalid 虚构岗位，不读取真实数据、不联网。展示首次补步骤、第二次复用零详情/零分析、条件变化只重核；自动断言岗位及历史在重复运行时不增长。

真实招聘汇总表入口试采和完整 V1 表格验收另见 [验收记录](issue8-acceptance.md)，此演示不代表真实采集能力验证。

## 报告示例与行动约定

以下为虚构表达示例，不代表已完成真实试采：

- 可直接判断：“建议考虑投递（绿色）。已保存 JD 的毕业窗口与已确认条件相符，职责符合求职倾向；已写入判断，下一步准备申请材料，由本人决定是否投递。”
- 需要小试验：“建议先用现有浏览器工具接入这个入口。表格链接通向企业目录，需要验证能否取得一个具体岗位及完整 JD；本轮先测试一个样本，再按结果继续或切换方法。”具备访问条件且已授权时，实际执行这个小试验。
- 当前受阻：“建议保留该来源，先处理其余可读取入口。当前登录阻止详情读取，已保存入口和未完成步骤；恢复访问后复测原样本，再决定是否补工具。”

依据现有信息作出合理暂定判断，只说明影响选择的关键假设。低成本、可撤销且已授权的动作直接推进；主动验证会改变选择的疑点。失败后尝试合理替代，记录具体障碍并继续其余任务，不无限研究、不添加审批层。建议与已完成动作分开表述，不编造成功结果。
