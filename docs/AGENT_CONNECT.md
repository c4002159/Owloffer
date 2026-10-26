# 智能体连接指南

让本机的 Claude 或 Codex 通过 MCP 查看 OwlOffer（BossHunter）的进度，并在你允许的范围内操作它。程序只在本机运行，智能体在这台电脑上调用，不需要把端口开放到外网。

## 它能做什么

| 工具 | 作用 |
|---|---|
| `bosshunter_status` | 每日循环进度、未处理异常、运行中的任务、被暂停的平台、今天的投递数、等你确认的请求 |
| `bosshunter_events` | 读取新消息（异常、平台暂停、HR 回复、循环结束）；传上次返回的 `latest` 作为 `since` |
| `bosshunter_daily_loop_start` / `_stop` | 启停每日循环（开始、结束时间，每平台投递上限） |
| `bosshunter_resolve_incident` | 把异常标为已处理 |
| `bosshunter_deliver` | 批准并投递岗位（按岗位 ID，或按平台） |
| `bosshunter_request_status` | 查看一条待你确认的请求是通过还是拒绝 |

## 权限

权限由程序里的设置决定，MCP 服务器只是转发，不能自己放宽。

- **自动同意（默认）**：智能体的操作直接执行，并记入消息中心。
- **先问我**：操作会变成请求，等你在消息中心确认。
- **只读**：只能看，不能改。

如果你频繁关闭它的请求，权限会按“自动同意 → 先问我 → 只读”逐级收紧。

## 连接步骤

1. 启动工作台（`bosshunter web`），打开 **配置 → 智能体**。
2. 生成令牌。令牌只显示一次，程序里只存哈希。**不要发到聊天、Issue 或提交里。**
3. 注册 MCP：

   Claude：

   ```bash
   claude mcp add bosshunter -e BOSSHUNTER_TOKEN=<令牌> -e BOSSHUNTER_URL=http://127.0.0.1:8686 -- "<python 路径>" -m bosshunter.main mcp
   ```

   Codex（`~/.codex/config.toml`）：

   ```toml
   [mcp_servers.bosshunter]
   command = "<python 路径>"
   args = ["-m", "bosshunter.main", "mcp"]
   env = { BOSSHUNTER_TOKEN = "<令牌>", BOSSHUNTER_URL = "http://127.0.0.1:8686" }
   ```

   `<python 路径>` 填运行 `pip install -e .` 的那个虚拟环境里的 Python。
4. 重启 Claude / Codex，看到 `bosshunter_*` 工具即成功。

## 智能体值班（内置，推荐）

不想自己建定时任务：打开 **配置 → 智能体**，在最上面的「智能体值班」里打开开关。程序在后台运行时，会每隔一段时间（默认一小时）自动叫起你电脑上的 Claude 命令行（`claude -p`），让它读状态和消息、循环停了就启动、处理能处理的异常，再把报告写进消息中心。

- 需要先装好 Claude Code，并**登录一次**：在终端运行 `claude`，输入 `/login`（或设置环境变量 `ANTHROPIC_API_KEY`）。点「检测命令行是否可用」可以确认。
- 每次值班只给它本程序的 MCP 工具，不能用终端、改文件或上网；它能做什么仍由上面的「智能体权限」决定。本程序为每次值班临时发一个只存在内存里的令牌，和你自己生成的令牌互不影响。
- 每次值班都会用掉你自己的 Claude 额度或 API 用量，所以默认关闭，间隔最短 10 分钟。
- 值班提示词可以在「高级」里改，也可以恢复默认。
- 程序关闭时不值班（它是程序自己调用 Claude，不是 Claude 常驻）。

## 定时值班（备用：自己建）

在 Claude 的定时任务或 Codex 的自动化里，使用配置页“值班提示词”，选间隔（如每小时）。值班员会读新消息，发现异常只记下并汇报，不会尝试绕过验证码、风控或登录。

## 排错

- 看不到工具：确认已重启客户端，且 `BOSSHUNTER_TOKEN` 没有多余空格。
- 提示权限被拒绝：令牌已撤销，或权限为只读。
- 连不上：确认工作台在运行，`BOSSHUNTER_URL` 端口一致。
- 请求没有执行：权限为“先问我”，到消息中心确认。
