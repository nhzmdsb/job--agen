# 当前验证结果

2026-10-07，云端 Linux / Python 3.12 环境。

- 创建虚拟环境并成功安装本项目及 MCP 可选依赖（官方 SDK 1.26.0）。
- CLI 保存和读取包含中文的来源样例成功。
- 4 项测试通过：修改历史与冲突保护及恢复；分页且不自动去重；非法写入不改动数据；真实 stdio MCP 客户端初始化、工具发现、创建、读取、修改、冲突错误与历史读取。
- MCP 安装所解析到的 Pydantic Settings 版本出现一个上游 forward-reference 警告，未影响本轮协议调用。

尚未验证：用户的 Windows 环境、实际 Work 客户端的 MCP 配置、任何真实招聘信息源、Notion 访问或云端与本地数据同步。仓库中的来源样例为虚构且默认禁用。

复现：安装 `.[mcp]` 后执行 `python -m unittest discover -s tests -v`。不安装 MCP 可选依赖时，stdio 测试会跳过，其余存储测试仍运行。
