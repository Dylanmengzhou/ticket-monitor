# Flight Agent MCP

一个没有业务界面的本机 MCP App。Agent 通过对话收集航线和日期，查询携程、去哪儿、飞猪、同程、途牛，返回分平台的结构化结果，并在查价结束后通过 iOS Bark 通知。

v0.2 支持五平台国内/国际单程、批量航线与日期、直飞与不经停筛选、具体机场限制、价格/起飞时段筛选、排序、独立的平台登录态，以及 Bark 自动/手动推送。npm 启动器提供 macOS / Windows 64 位运行环境的自动安装；已在 macOS 实机验证，Windows 实机验收待完成，仓库已配置三平台 CI。

不需要启动原开源软件，不依赖 `webui.py`，不提供 HTTP 管理接口。登录时仅打开携程自己的官方窗口。

## 安装

推荐通过 npm 使用。只需先安装 **Node.js 22+（自带 npm/npx）**，无需自行安装 Python、uv 或克隆本项目。

**当前是本地待发布包，尚未上传 npm。** 已经拿到 `.tgz` 文件的朋友，在文件所在目录运行（macOS 终端 / Windows PowerShell 均可）：

```bash
npx -y --package ./flight-agent-mcp-0.2.0.tgz flight-mcp setup
```

若希望保留全局命令，也可 `npm install -g ./flight-agent-mcp-0.2.0.tgz` 后运行 `flight-mcp setup`。

发布到 npm 后，首次准备环境可以合并成一个命令：

```bash
npx -y flight-agent-mcp@0.2.0 setup
```

首次运行自动下载固定版本的 uv、独立 Python 3.12、查票依赖和匹配的 Chromium；后续使用本机缓存。uv 下载会校验内置 SHA-256。安装进度只写 stderr，完成后 stdout 输出这次安装对应的 MCP JSON 配置。首次下载可能需要几分钟，建议先执行 `setup` 再连接客户端。

也可全局安装已发布包后运行 `flight-mcp setup`。不设 npm `postinstall`，浏览器与 Python 在首次使用时准备，不会随 `npm install` 隐式运行安装脚本。

系统要求参照 [Playwright](https://playwright.dev/python/docs/intro#system-requirements)：macOS 14+、Windows 11+ / Server 2019+；64 位 Intel/AMD 或 ARM64。Linux 提供 glibc x64/ARM64 启动器，桌面与系统库需自行准备，不支持 Alpine/musl。

### 从源码开发

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m flight_mcp setup-browser
```

若已安装匹配版本的 Playwright Chromium，最后一步会直接复用缓存。浏览器下载地址可通过 Playwright 的 `PLAYWRIGHT_DOWNLOAD_HOST` 环境变量设置。

## 接入 MCP 客户端

`flight-mcp setup` 输出的配置使用当前 Node 和启动器的绝对路径，Mac / Windows 均无需配置 Python 路径。粘贴到支持**本地 stdio** MCP 的客户端即可。使用期间保留该全局安装或 npx 缓存；移动安装位置后重新运行 `mcp-config`。

已发布包也可以直接配置为按需启动：

```json
{
  "mcpServers": {
    "flight-agent": {
      "command": "npx",
      "args": ["-y", "flight-agent-mcp@0.2.0", "serve"]
    }
  }
}
```

在 Windows 中，若客户端无法执行 `npx.cmd`，优先使用 `setup` 输出的 Node 绝对路径配置。若客户端运行在远程服务器，本服务和登录窗口也会在那里执行；要使用本机携程窗口，应选择本机 MCP 执行模式。

常用命令：

```bash
flight-mcp doctor
flight-mcp mcp-config
flight-mcp setup-browser
flight-mcp --data-dir ./my-flights serve
```

以下是源码开发环境的配置方法：

生成带本机绝对路径的可用配置：

```bash
.venv/bin/python -m flight_mcp --data-dir ./data mcp-config
```

该命令输出标准 `mcpServers` JSON。客户端启动的是 `.venv/bin/python -m flight_mcp --data-dir <绝对路径> serve`，传输方式为 **stdio**；不需要端口、网页或 API Key。

也可接入 Codex：

```bash
codex mcp add flight-agent -- /绝对路径/.venv/bin/python -m flight_mcp --data-dir /绝对路径/data serve
```

服务的日志只写 stderr；stdout 留给 MCP JSON-RPC。客户端会按需启动服务，不需要另外常驻启动一份网页服务。

## 直接试用

```bash
# 检查浏览器、会话及运行目录
.venv/bin/python -m flight_mcp --data-dir ./data doctor

# 通过真正的 MCP stdio 客户端，查询首尔→成都、成都→北京
.venv/bin/python examples/try_flights.py --date 2026-11-27 --output local-results/search.json
```

示例中的日期需为今天至未来 365 天内。省略 `--date` 时使用 30 天后的日期。

可以直接对连接了本服务的 Agent 说：

> 查 2026 年 11 月 27 日首尔到成都、成都到北京的机票。只要直飞，不经停，每条给我最便宜的 5 班，写清具体机场、当地时间和税费。

Agent 应提交 `search_flights`，再调用 `get_search_result` 等待结果。

## MCP 工具

| 工具 | 作用 |
|---|---|
| `search_flights` | 开始批量查询，立即返回 `job_id` |
| `get_search_result` | 查询进度、每条航线状态与真实报价 |
| `cancel_search` | 取消查询，保留已完成部分结果 |
| `get_session_status` | 查看会话、验证证据和登录窗口状态 |
| `start_login` | 打开本机携程官方登录窗口 |
| `finish_login` | 用户确认完成后保存会话；也可取消窗口 |
| `list_providers` | 列出五个平台、各自会话状态和登录槽位 |
| `configure_bark` | 保存官方/自建 Bark 配置，不发送通知 |
| `get_bark_status` | 查看 Bark 状态，不返回设备 Key |
| `test_bark` | 用户明确请求测试后发送一条通知 |
| `push_search_result` | 手动推送已结束任务的摘要，防止重复发送 |

### 查询参数

```json
{
  "routes": [
    {"origin": "首尔", "destination": "成都", "dates": ["2026-11-27"]},
    {"origin": "成都天府", "destination": "北京首都", "dates": ["2026-11-27"]}
  ],
  "providers": ["ctrip", "qunar", "fliggy", "tongcheng", "tuniu"],
  "notify": true,
  "direct_only": true,
  "exclude_stopovers": true,
  "cabin": "economy",
  "max_price": 3000,
  "departure_after": "08:00",
  "departure_before": "23:00",
  "sort_by": "price",
  "limit": 5
}
```

- 中文城市名表示同城机场范围；机场名或机场三字码表示严格机场限制。`成都` 包含双流/天府，`TFU` 只认天府；`北京` 包含首都/大兴，`PEK` 只认首都。
- `dates` 必须含年份，按出发地当地日期理解；最多 8 条航线、每条最多 7 天、合计最多 16 个航线日期组合。
- `providers` 默认五个平台，也可只选一部分。每个平台独立采集，结果的 `legs` 为航线 × 日期 × 平台；每个平台各自应用 `limit`。`notify=false` 可禁用本次查价的自动通知。
- 默认经济舱、仅直飞、不经停。设置 `direct_only=false` 可查询中转；`exclude_stopovers` 单独控制经停。
- 价格币种为 CNY；起飞时段为出发机场当地时间，支持 `22:00`—`06:00` 跨零点窗口。
- `sort_by` 可选 `price`、`departure`、`duration`；每个航线日期返回最多 1—50 班。
- 机场限制、日期、直飞、不经停、价格与时段都在返回前再次过滤。不能确认具体机场的行不会通过严格机场筛选。

### 结果口径

每个航班包含实际起降机场、带时区的当地时间、飞行时长、舱位、人民币列表报价、税费是否包含及已采到的拆分、行李、采集时间和该平台查询链接。列表报价不表示已经锁定或校验到订单支付价。

携程国内 H5 使用其 `tprice`，国际 PC 使用成人票价加税费；同程及已确认的国际明细总价保留含税标记，飞猪国内 PC 标价为税前。其他尚无法确认的来源，`tax_included=null`。税费拆分缺失时保留 `null`，不补造数值。跨时区航班按实际时间偏移计算时长。

`comparisons` 按航线和日期汇总：`cheapest_reported` 是各平台显示价格的最低值，可能是税前价；`cheapest_tax_included` 只在已确认含税的具体航班中比较。国际渠道可能仅取得日历价，状态为 `calendar_only`，不会混入 `flights` 或最低航班价。

任务状态为 `queued` / `running` / `completed` / `partial` / `failed` / `cancelled`。每条航线独立报告：

- `ok`：取得满足条件的真实航班。
- `no_matching_flights`：取得明细，但没有航班满足条件。
- `no_data`：本轮没有可核实的数据，不代表没有航班。
- `calendar_only`：仅得到日历价，未冒充具体航班。
- `login_required` / `verification_required`：需要用户登录或完成官方验证。
- `session_busy` / `timeout` / `provider_error`：会话被占用、查询超时或采集失败。

批量查询有一个平台失败时仍保留其他平台的结果。浏览器采集有独立进程和时间上限；同一平台的会话串行使用，避免多个查询同时争抢登录资料。查询结果落 SQLite，后续可凭 `job_id` 重读；旧任务始终保留原始 `observed_at`。

## 登录

1. Agent 调用 `list_providers` 查看各平台状态，再调用 `start_login(provider="ctrip")` 等，平台官方窗口会在本机打开。
2. 用户自行完成登录、短信或页面验证，不把密码/验证码发送给 Agent。
3. 用户明确表示完成后，Agent 调用 `finish_login(login_id)`。
4. 会话状态先为 `saved_unverified`；真实查询取得航班后，该平台相应国内/国际范围才标为 `verified`。平台之间的验证证据、Cookie、登录窗口和锁互相独立。

平台 ID：`ctrip` 携程、`qunar` 去哪儿、`fliggy` 飞猪、`tongcheng` 同程、`tuniu` 途牛。飞猪的 PC 和淘宝 H5 登录态不共享，H5 登录另用 `start_login(provider="fliggy_h5")`。查询仍统一选择 `fliggy`，有 H5 登录态时可自动回退。

取消登录使用 `finish_login(login_id, cancel=true)`。登录窗口超过 10 分钟会自动结束。

从参考项目迁移已有会话：先关闭原项目的采集/登录浏览器，再运行：

```bash
.venv/bin/python -m flight_mcp --data-dir ./data import-session ./ticket-monitoring/user_data/ctrip
```

迁移会复制到独立目录，不覆盖原资料，也不覆盖本 App 已有的登录态。

## 本机数据

显式使用 `--data-dir ./data` 时：

```text
data/
  flights.sqlite3     查询任务、结果和不含凭据的会话状态
  profiles/<平台>/   各平台浏览器资料与 Cookie，仅本机保存
  bark.json          Bark 设备 Key 和通知偏好，仅本机保存
  jobs/               单次查询的中间结果
  logs/               采集进程日志
```

未指定路径时，默认使用 `~/.local/share/flight-agent-mcp`；也支持 `FLIGHT_MCP_DATA_DIR`。目录和凭据文件使用当前用户权限，整个 `data/` 已加入 `.gitignore`。MCP 不输出 Cookie 或 Bark Key，不接公共图床，不做自动购票。升级后保留旧携程登录态，并自动迁移旧 SQLite 数据库。

每个人在自己的电脑上登录携程；npm 包不包含开发者的 Cookie、浏览器资料或查询记录。运行环境和业务数据分开保存，升级不会覆盖登录态。运行环境缓存默认在 macOS `~/Library/Caches/flight-agent-mcp`、Windows `%LOCALAPPDATA%/flight-agent-mcp`，可通过 `FLIGHT_MCP_CACHE_DIR` 修改。不要在查询或登录进行中删除缓存。

下载受限时，可设置 `FLIGHT_MCP_UV_DOWNLOAD_BASE_URL`（默认 `https://releases.astral.sh/github/uv/releases/download`，镜像目录结构与官方一致、仍校验固定哈希）、`UV_PYTHON_INSTALL_MIRROR`、`UV_DEFAULT_INDEX` 和 `PLAYWRIGHT_DOWNLOAD_HOST`。不同下载源须由用户自行选择。

## iOS Bark 通知

在 iPhone 安装 Bark，复制 App 提供的设备推送地址（形如 `https://api.day.app/设备Key`，不带推送正文）。在运行本 MCP 的电脑上执行：

```bash
flight-mcp --data-dir ./data bark-configure
```

按提示私密输入推送地址，避免把 Key 留在 shell 历史或聊天里。若使用默认数据目录，可省略 `--data-dir ./data`；必须与 MCP 使用同一目录。支持自建 Bark，也可通过 `FLIGHT_MCP_BARK_KEY`、`FLIGHT_MCP_BARK_SERVER` 提供配置。Agent 的 `configure_bark` 工具接受同样的配置，返回值始终隐藏 Key。

默认每次查询的所有平台结束后，独立工作进程自动推送**一条**汇总；不需要 Agent 持续轮询或保持原 MCP 连接。部分平台失败会如实展示状态，成功平台报价仍保留。消息包含各平台最低标价、税费口径、当地起飞时间、采集时间及一个渠道链接。

```bash
flight-mcp --data-dir ./data bark-status
flight-mcp --data-dir ./data bark-test
flight-mcp --data-dir ./data bark-configure --manual-only
flight-mcp --data-dir ./data bark-configure --disable
```

配置操作不发送通知；`bark-test` / `test_bark` 才发送测试。单次查询设置 `notify=false` 可临时关闭自动通知，手动推送用 `push_search_result(job_id)`。同一任务已发送或送达状态不明时不自动重复发送；明确失败可由用户要求手动重试。Bark 服务接受请求不等于已经在 iPhone 锁屏展示，需允许系统通知。

使用 [Bark 官方 POST API](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md)，支持分组、铃声、普通/静默/时效通知。这里的自动通知针对**这次查价完成**；持续定时监控与降价提醒不在该版本中。

## 打包与发布 npm

开发者需有 Python 3.11+（含 pip），同时安装 Node.js 22+。`npm pack` 会构建 Python wheel 并装入 npm 包，用户不依赖 PyPI 上存在同名项目。

```bash
npm ci --ignore-scripts
npm test
npm pack --pack-destination dist
```

产物：`dist/flight-agent-mcp-0.2.0.tgz`。分发包采用白名单，仅包含 Node 启动器、Python wheel、约束版本表、许可证和说明；不包含 `.venv`、原参考仓库、`data`、日志或 Cookie。

确认包内容和平台验证结果后，以包所有者账号登录并发布：

```bash
npm login
npm publish ./dist/flight-agent-mcp-0.2.0.tgz --access public
```

若名称被占用，需要调整 `package.json` 的名称或使用自己的 npm scope；示例配置也要同步调整。发布新版本时，`package.json`、`pyproject.toml`、`src/flight_mcp/__init__.py` 及示例版本保持一致。

## 测试与参考来源

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src/flight_mcp
```

默认测试离线运行，覆盖筛选、机场范围、时间与税费、会话状态、部分失败、真实子进程超时/取消，以及实际 MCP stdio 工具发现与调用。真实渠道验证通过 `examples/try_flights.py` 单独进行。

Node 测试覆盖平台选择、归档校验与解压、并发首次安装锁、无依赖配置输出。`examples/verify_npm.py <已安装的 npm/cli.mjs> --check-browser` 会通过 npm 入口执行真实 MCP 工具发现、调用并启动无头浏览器；CI 对 macOS、Windows、Linux 都执行该流程。增加 CI 配置不代表已在本地完成 Windows 实机验证。

Bark 测试使用本机 HTTP 接收器验证请求格式、成功/失败、超时、去重、凭据隐藏，以及查询完成后的触发逻辑。`examples/verify_bark_completion.py <已安装的 npm/cli.mjs>` 会断开 MCP 后等待实际工作进程的通知，接收端仍为本机测试服务器；它不验证 iPhone/APNs 送达。

五平台采集和字典组件选自 [dengmeiluan/ticket-monitoring](https://github.com/dengmeiluan/ticket-monitoring)，固定到提交 `844c5e19dbfc`，保留 Apache-2.0 许可。引入 16 个采集/数据/链接文件，来源校验和及修改记录位于 `src/flight_mcp/vendor/source.json`；没有引入原项目的网页、图表和调度器。Bark 的凭据存储、任务摘要和推送去重在本 App 独立实现。

MCP 使用 [官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)。Codex 接入方式参考[官方 MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

持续价格监控、降价提醒和往返总价不在 v0.2 中；当前支持五平台对话查票与查价完成通知。
