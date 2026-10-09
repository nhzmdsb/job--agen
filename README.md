# Job Agent Workspace

供 AI 管理的求职搜索池框架。AI 使用自己的搜索、浏览器或 CLI 能力读取信息源，通过本项目保存线索、岗位、判断和搜索进度。

当前提供本地 SQLite 存储、JSON CLI、可选 stdio MCP、修改历史与并发版本检查。飞书来源已由 AI 实际读取并保存到私有交付数据中；仓库本身不包含网页采集器、真实数据或模型调用，也不会自动投递。小红书与 BOSS 项目已做云端试跑，尚未完成真实搜索接入。

## 当前目标与接替入口

当前重心是**扩展信息源池**，优先验证小红书与 BOSS 的搜索、详情读取，而不是继续深挖飞书内少数公司。先根据需求设计工作流，再选择技术；先完成最小存储框架，再逐个接通来源，用真实样本验证。

- [交接说明](docs/handoff.md)：已确定的计划、工作顺序、数据恢复、来源角色和持续交接约定。
- [当前执行任务 #1](https://github.com/nhzmdsb/job--agen/issues/1)：候选项目、云端试跑结果、阻塞、待办和验收条件。读取正文和后续评论，确认最新进度。
- [来源筛选](docs/source-selection.md)、[飞书读取](docs/feishu-workflow.md)、[公司与岗位扫描](docs/job-selection.md)：已有工作流约定。

新接替的 Agent 不应假定有全局聊天记忆。先读 README → AGENTS.md → handoff → 当前 Issue → 相关工作流，再读取用户本地私有数据。个人求职条件以私有 profiles 或用户补充为准，不能从通用样例推断。

程序负责访问、存储、读取、版本与历史；AI 负责搜索规划、去重、补全、匹配和策略。不要为这些判断增加确定性评分、自动合并或复杂状态机。

## 安装与启动

需要 Python 3.10+。在仓库目录中：

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell 改用 .venv\Scripts\Activate.ps1
python -m pip install -e .
job-agent init
job-agent list sources
```

可不安装，直接在仓库目录运行 `python -m job_agent.cli`。默认数据库为当前目录的 `data/workspace.sqlite3`。CLI 可用 `--db` 指定绝对路径；MCP 请通过 `JOB_AGENT_DB` 设置绝对路径，避免不同运行目录产生不同搜索池。

## AI 的读写方式

`sources` 保存信息源与读取方法；`leads` 保存原始线索；`jobs` 保存岗位及 AI 判断；`runs` 保存搜索任务、进度与失败原因；`artifacts` 保存材料内容或文件引用；`profiles` 保存用户自己提供的求职条件。

数据是自由 JSON 对象。建议字段见 [记录约定](docs/records.md)，它们不是强制业务规则。新建数据库初始为空；接替时优先使用已交付的私有数据库，不能把一个新空库误认为原搜索池。

创建时从 stdin 提供 JSON（避免在 shell 参数中直接插入网页文本）：

```bash
job-agent put sources --id example-source < examples/source.json
job-agent get sources example-source
job-agent list leads --limit 50 --offset 0
```

修改时先读取，保留原字段并提供完整的新 JSON 对象，以及刚读取的版本：

```bash
job-agent put sources --id example-source --expected-version 1 --reason "用户确认读取范围" < examples/source.json
job-agent history sources example-source
job-agent export > private-backup.json
```

`put` 是完整替换，不是字段 patch。版本冲突时重新读取后再修改。恢复旧版本：读取当前版本，取历史中的旧 `data`，用当前版本再次写入；历史不会被删除。分页读取要持续跟随 `next_offset`，直到为 null。导出包含全部记录和历史，备份文件需放在仓库之外或 `private/` 中。

## MCP

```bash
python -m pip install -e '.[mcp]'
job-agent-mcp
```

复用官方 Python MCP SDK 的 1.26.0 版本；只提供 stdio，不开放网络服务。支持工具：`list_records`、`read_record`、`write_record`、`read_history`。由宿主 Agent 负责搜索、补全、去重和判断。

支持 stdio MCP 的客户端可参考以下配置；替换成实际绝对路径，具体配置位置依客户端而定：

```json
{
  "mcpServers": {
    "job-agent": {
      "command": "/absolute/path/to/.venv/bin/job-agent-mcp",
      "env": {"JOB_AGENT_DB": "/absolute/path/to/private/workspace.sqlite3"}
    }
  }
}
```

Windows 使用 `.venv\\Scripts\\job-agent-mcp.exe` 的绝对路径。云端不会自动访问本地数据库，后续共享存储方案由信息源和使用方式决定。

## 验证

```bash
python -m unittest discover -s tests -v
```

开发约定见 [AGENTS.md](AGENTS.md)，工作流见 [workflow.md](docs/workflow.md)。代码仓库不存真实简历、账号凭证或求职记录。
