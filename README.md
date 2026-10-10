# 个人求职工作台 V0

2026-10-11：在原项目上新增可操作工作台，保留 Python、SQLite、记录 envelope、版本检查、完整历史、CLI 和 MCP。此版本取代旧计划中的「禁止 GUI」「采集优先」「生命周期以后实现」。后台自动化整理信息，用户决定策略、简历和最终申请；程序不自动投递。

## 打开和使用

Windows 双击 `start-workbench.cmd`，浏览器打开 http://127.0.0.1:18765 。首次启动安装 Flask 和 Waitress；之后直接启动。关闭启动窗口会停止本地服务。仅监听本机。

主页面看岗位卡片，右侧切换机会池、准备投递、已投递、面试中、已结束和垃圾桶。点卡片编辑详情后保存；备注自动保存并显示状态。四色由资格证据和兴趣决定，颜色与进度独立，未知资格或缺 JD 为灰色；人工调整必须填写原因。没有匹配分数。招聘年份、面向届别和允许申请年份分别记录，不能把「2026 招聘」推断为「2026 届可申请」。

岗位详情可以上传真实 PDF/DOCX、下载原文件、选择四种准备任务并生成 Prompt。复制后打开 ChatGPT 粘贴；附件需要在新会话另行上传，Prompt 不表示 ChatGPT 已读取附件。超出公司同批次名额时必须明确确认并填写原因。垃圾桶保留去重标识，已结束保留历史。

## 数据与协作

个人工作副本、附件、设置、备份位于 `%LOCALAPPDATA%\JobAgent`；可用 `JOB_AGENT_HOME` 调整。原 `data/workspace.sqlite3` 被保留，默认数据库首次迁移前用 SQLite 备份。公开仓库只存代码、文档和虚构测试数据。

私有 GitHub 仓库优先作为共享权威源。本次已建立并验证 `nhzmdsb/job--agen-data` 私有属性与真实读写。`settings.json` 只记录 `{"repository":"nhzmdsb/job--agen-data"}`；凭据由现有 Git Credential Manager 提供，不写入配置或浏览器。连接任何仓库前检查私有属性。云端不可用时本地照常保存并显示待同步。

每次修改同时保存版本、历史和待同步事项。后台在写入后、启动时同步；页面刷新发起检查，每六小时补充维护。不同记录可合并，同一记录并发修改保留双方，在页面选择合并，双方完整历史归档到私有仓库。同步 JSON 与按 SHA-256 保存的原文件，不同步 SQLite 文件。运行依赖电脑开机且本地服务运行；定时 Work 任务另见交接记录。

读取后保留未知字段，版本化写入，不能强制覆盖。[共享协作协议](docs/COLLABORATION.md) 包含 HTTP、CLI、MCP 和私有仓库写入方式。

## 开发与验证

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[mcp]"
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m job_agent.web --no-browser
```

CLI：`python -m job_agent.cli init/list/get/put/history/export/import/sync`，JSON 从标准输入读取。`put` 更新必须提供 `--expected-version`，并提交保留未知字段的完整 data。MCP：`python -m job_agent.mcp_server`，仍为 stdio，复用同一存储。历史导入先验证完整快照再原子恢复，保留原 ID、版本和历史。

官方来源补充：`python -m job_agent.collect` 读取已验证的 Canonical 官方岗位，保存完整 JD、原始入口、访问时间和证据；未变化时不重复写入。新增来源须先验证可访问性，不能把招聘活动年份推断成申请资格。已创建「求职工作台六小时维护」Work 定时任务（本机需开机且应用运行）；任务只报告新增机会、失败或需要处理的事项，自动投递不在范围内。

## 当前真实状态

- 已读取 Canonical 官方 Graduate Software Engineer 完整 JD，原文明确提及 2025/2026 毕业生；个人专业、学历等资格尚未核对，因此保持灰色。
- 已验证私有 GitHub 独立读回、远端修改回流、真实来源 HTML 二进制往返、同岗位冲突双方归档。
- 旧数据库为空；未找到 `search-pool-20261008.zip`。交接中的 64 个历史岗位没有恢复，不能视为当前数据。没有找到真实简历，真实简历验收未完成；测试文件不是个人简历。
- BOSS、小红书、微信公众号仍是独立待办，不阻塞工作台。不会自动公司背调、删掉证据不足的机会或提交申请。

[本次验收](docs/v0-acceptance.md) · [当前交接与旧计划](docs/handoff.md) · [记录约定](docs/records.md) · [历史验证](docs/verification.md)
