# GenAI2OpenAI

将上海科技大学 GenAI 网页端模型接入本地 agent，提供 OpenAI Chat Completions、Anthropic Messages 和 OpenAI Responses 兼容接口。本仓库为第一跳，负责网页登录、附件处理与工具协议适配；[genai2agent-relay](https://github.com/FrozenYears/genai2agent-relay/tree/feat/native-first-streaming) 为独立的第二跳，负责面向客户端的协议转换。

A two-hop proxy for ShanghaiTech GenAI web-chat models, exposing OpenAI Chat Completions, Anthropic Messages, and OpenAI Responses interfaces for local agents.

当前实现位于两仓库的 `feat/native-first-streaming` 分支，尚未合并至 `main`。用户需具备学校 GenAI 网页访问权限，无需申请模型厂商或学校独立 API Key。服务直接对接学校网页聊天后端，不提供绕过该后端的官方 API 链路。

```mermaid
flowchart LR
    A[本地 agent] -->|三种兼容协议| R[第二跳 relay · 31110]
    R -->|结构化 Chat tools / 历史 / SSE| F[第一跳 · 31100]
    F -->|网页登录与网页请求| W[学校 GenAI 网页后端]
    W -->|正文 / 思考 / 工具表示| F
    F -->|校验后的结构化输出| R
    R -->|正文增量与完整工具调用| A
```

模型生成工具调用后，由本地 agent 在自身运行权限内执行文件读写、命令执行等操作并回传结果。两跳代理均不代行任何本地工具。两跳间的 `native` 仅指代双跳间采用结构化传输；面向学校网页端时仍需通过文本提示和协议解析进行交互，网页端本身并不等于开放了原生函数调用接口。

## 双跳快速开始

### 获取分支代码

将两仓库克隆至同一父目录下，分别维护各自的依赖环境与 Git 历史。以下命令基于 Bash，适用于 Linux 或 WSL：

```bash
git clone --branch feat/native-first-streaming https://github.com/FrozenYears/shanghaitech-genai2api.git
git clone --branch feat/native-first-streaming https://github.com/FrozenYears/genai2agent-relay.git
```

### 启动第一跳

安装 Pixi 后，在第一跳根目录下执行：

```bash
cd shanghaitech-genai2api
pixi install
cp .env.example .env
```

配置 `.env` 文件，填入网页登录凭据与本地访问密钥：

```dotenv
GENAI_TOKEN=student_id@password
PORT=31100
API_FORMAT=both
API_KEY=replace-with-first-hop-key
GENAI_EMPTY_CONTINUATIONS=8
GENAI_TOOL_REPAIR_ATTEMPTS=3
GENAI_TOOL_REQUEST_TIMEOUT_SECONDS=900
```

`GENAI_TOKEN` 支持配置网页 JWT 或账号密码。学号密码方式通过统一身份认证（CAS）登录，Token 过期后自动尝试刷新。若采用 JWT，需在[学校 GenAI 网页](https://genai.shanghaitech.edu.cn/)的网络请求头中提取 `x-access-token`；切勿提交 `.env` 文件或泄露登录凭据。

```bash
pixi run serve
```

Windows 环境支持通过 `pwsh -File run-windows.ps1` 启动；脚本会自动利用本地缓存完成 Pixi 安装与服务拉起。命令行参数优先级高于 `.env`，例如：

```bash
pixi run serve --port 31100 --api-format both --empty-continuations 8 --tool-repair-attempts 3 --tool-request-timeout 900
```

第一跳当前默认绑定 `0.0.0.0`，而非仅监听回环地址。务必配置 `API_KEY`，并通过防火墙或受信局域网限制访问来源，严禁直接暴露于公网。第一跳不支持通过 `HOST` 配置监听地址。

### 启动第二跳

在另一终端窗口进入第二跳目录。使用 uv 构建独立虚拟环境以避免全局污染：

```bash
cd genai2agent-relay
uv venv .venv
uv pip install --python .venv/bin/python -e .
cp .env.example .env
```

Windows 环境对应的 Python 解释器路径为 `.venv/Scripts/python.exe`。编辑第二跳 `.env`：

```dotenv
UPSTREAM_BASE_URL=http://127.0.0.1:31100
UPSTREAM_TOOL_MODE=native
UPSTREAM_API_KEY=replace-with-first-hop-key
RELAY_API_KEY=replace-with-relay-key
HOST=127.0.0.1
PORT=31110
```

```bash
.venv/bin/python main.py
```

认证配置隔离要求如下：

| 配置项 | 职能范围 |
| --- | --- |
| 第一跳 `GENAI_TOKEN` | 用于登录学校网页端，仅第一跳持有 |
| 第一跳 `API_KEY` 与第二跳 `UPSTREAM_API_KEY` | 两处取值必须一致，用于第二跳访问第一跳服务 |
| 第二跳 `RELAY_API_KEY` | 用于客户端访问第二跳代理，独立于网页登录凭据 |

第二跳默认监听 `127.0.0.1`。双跳服务均需保持常驻运行。第一跳支持客户端直连（监听端口 `31100`），下文接入示例统一基于双跳架构展示。

### 连通性测试

```bash
curl -fsS http://127.0.0.1:31100/health
curl -fsS http://127.0.0.1:31110/health
curl -fsS http://127.0.0.1:31110/v1/models \
  -H 'Authorization: Bearer replace-with-relay-key'
```

健康检查接口无需鉴权，仅表明网络端口可达。请求中的模型参数必须匹配模型列表返回的 `id`，如当前平台的 `deepseek-pro`、`Kimi-k3`；代理服务不会将通用别名自动映射为平台 ID。模型列表展示的能力标识源于平台静态元数据，不代表所有组合均通过全功能实测。

## 客户端接入

| 客户端协议 | 服务地址 | 路由端点 |
| --- | --- | --- |
| OpenAI Chat Completions | `http://127.0.0.1:31110/v1` | `/chat/completions` |
| Anthropic Messages | `http://127.0.0.1:31110` | `/v1/messages` |
| OpenAI Responses | `http://127.0.0.1:31110/v1` | `/responses` |

客户端需携带 `RELAY_API_KEY`，支持置于 `Authorization: Bearer ...` 或 `x-api-key` 请求头。流式请求验证示例：

```bash
curl -N http://127.0.0.1:31110/v1/chat/completions \
  -H 'Authorization: Bearer replace-with-relay-key' \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-pro","messages":[{"role":"user","content":"回答 READY"}],"stream":true}'
```

### Claude Code

在当前终端中导出对应环境变量后启动：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:31110
export ANTHROPIC_AUTH_TOKEN=replace-with-relay-key
export ANTHROPIC_MODEL=deepseek-pro
claude
```

若客户端默认配置了其他辅助模型，须同步修正为当前平台存在的模型 ID，避免内部子任务调用失效。网络连接参考 [Claude Code 网关配置文档](https://code.claude.com/docs/en/llm-gateway-connect)；对学校模型的接入适配由本项目实现，不代表 Anthropic 官方支持。切勿为了跳过权限提示而全局授予高危本地执行权限。

### Codex CLI 及其他 Responses 客户端

在 `~/.codex/config.toml` 中配置自定义 provider：

```toml
model = "deepseek-pro"
model_provider = "genai"

[model_providers.genai]
name = "ShanghaiTech GenAI relay"
base_url = "http://127.0.0.1:31110/v1"
wire_api = "responses"
env_key = "GENAI_RELAY_API_KEY"
```

启动前设置 `GENAI_RELAY_API_KEY` 环境变量为第二跳的 `RELAY_API_KEY`。规范参见 [Codex 自定义 provider 文档](https://developers.openai.com/codex/config-advanced/)。当前仅支持 Responses 协议核心子集：客户端必须全量提交上下文历史，不支持 `previous_response_id`、`item_reference` 或 `store=true`。若客户端要求服务端状态管理、custom grammar 或未实现的托管工具，必须调整客户端模式或声明定义；单纯修改 base URL 无法保证协议完整兼容。

omp 可通过 OpenAI Chat Completions 协议配置接入，base URL 对应表内 Chat 端点；目前的长期运行验证均基于 `GenAI` provider 下的 `deepseek-pro` 和 `Kimi-k3`。关于 Pi 在附件及工具链路上的具体表现，见[历史 Pi 验证](docs/PI_REAL_WORLD_VALIDATION.md)。

## 工具调用与自动恢复

第二跳默认向第一跳发送结构化工具定义、角色、调用 ID 及执行结果。第一跳将其适配为网页消息；上游返回结构化 `tool_calls` 时保留这些字段，并在交付前校验。没有结构化字段时，严格解析 action、XML、DeepSeek DSML 或 Kimi XTML 等明确表示。

正文内容与独立思考支持增量流式输出，等待模型输出期间维持保活心跳。工具调用参数必须在完整接收并通过 JSON 语法、Schema 模式、工具选取范围及并行度约束检查后，整批交付客户端。普通代码示例不作为工具调用解析；任一成员参数异常即导致整批判定失效，系统不执行局部调用筛选，也不尝试补全括号或猜测缺失参数。

错误恢复预算集中在第一跳管理，第二跳 native 模式下不追加重试循环：

| 环境变量 | CLI 参数 | 默认值及范围 |
| --- | --- | --- |
| `GENAI_EMPTY_CONTINUATIONS` | `--empty-continuations` | 空输出续作追加 `8` 次，取值 `0..30` |
| `GENAI_TOOL_REPAIR_ATTEMPTS` | `--tool-repair-attempts` | 工具调用纠错追加 `3` 次，取值 `0..10` |
| `GENAI_TOOL_REQUEST_TIMEOUT_SECONDS` | `--tool-request-timeout` | 单请求共享生成窗口 `900` 秒，有限正数 |

空输出续作仅在上游正常返回 `stop` 但无任何原始正文、思考或调用时触发。工具纠错仅处理正常结束、调用不合法且尚未交付任何正文、思考或调用批次的生成。两项恢复预算独立累计、互不重置；单次请求默认最多生成 `1 + 8 + 3 = 12` 次，共用一个请求级 deadline。次数设为 `0` 只禁用对应恢复，不关闭总时限；心跳和新一代生成不会续期，附件预处理不计入该窗口。

正文或思考一旦交付，代理不会撤回重放。网络错误、断流、截断、取消和到期也不触发上述纠错。空恢复预算耗尽返回 `upstream_empty_response`，工具纠错耗尽保留具体错误码，累计生成窗口到期返回 `upstream_request_timeout`。流式失败不发送成功终态，非流式上游失败返回 HTTP 502。

仅在配置 `UPSTREAM_TOOL_MODE=text` 时，第二跳才会激活基于纯文本提示和序列化重试的兜底逻辑。该模式仅用于纯文本后端，并非 native 模式失败后的运行时降级路径。第二跳参数详见[第二跳文档](https://github.com/FrozenYears/genai2agent-relay/blob/feat/native-first-streaming/README.md)。

## 可选的 omp 任务监督

单次请求级的重试机制无法判断上层业务目标是否达成。当模型仅输出规划未执行改动、修改导致业务测试失败，或交付数据后发生协议异常时，可采用内置的任务监督器：

```bash
pixi run python -B validation/native-first-endurance/supervise.py \
  --model Kimi-k3 --workspace D:/work/project \
  --task-file D:/trusted/task.txt --verify-argv-file D:/trusted/checker-argv.json \
  --artifact-dir D:/audit/new-run --max-feedback 8 --max-minutes 45
```

参数路径需替换为真实本地路径。`workspace` 目录必须预先存在，`artifact-dir` 须指定工作目录之外的新路径。`task.txt` 存放任务需求，`checker-argv.json` 为可信的自动化验收命令列表，例如：

```json
["D:/shanghaitech-genai2api/.pixi/envs/default/python.exe", "-B", "D:/trusted/check.py"]
```

监督器调用已安装、已配置相应 GenAI 模型的真实 omp，在同一会话中工作。每轮结束后，它在本地运行验收命令，以进程退出码判断结果；未通过时把真实验收差异交回模型，要求先读取当前文件和执行状态，再增量修复。默认每任务额外反馈 8 次、整场 45 分钟、单轮 1200 秒。该入口使用当前 Windows omp 安装路径，并非通用跨平台启动器。

验收命令使用 `shell=False`，限制运行时间和输出，并过滤环境中的凭据。agent 默认没有 bash，只有显式传入 `--allow-bash-pattern` 才开放相应模式。验收脚本应放在工作目录外；这些措施不是 OS 沙箱，文件和命令权限仍需由客户端限制。

单请求恢复耗尽后，监督器可在剩余任务预算内自动开启下一回合。任务总时间或反馈预算耗尽时，保存 `result.json`、`checkpoint.json`、会话与验收记录，并非零退出。身份/权限故障，或超时 abort 后无法确认会话停稳，也会停止，避免并发提交。当前不支持跨进程自动恢复；它是可选入口，不会自动接管 Claude Code、Codex 或普通 omp 会话。

## 图片、文档、搜索与思考

| 功能特性 | 规范说明与支持边界 |
| --- | --- |
| 思考机制 | 三种协议均支持 `thinking: true/false`；客户端 reasoning effort 等枚举统一映射为布尔状态，不支持细粒度档位与明确的 token budget 约束 |
| 联网检索 | `web_search: true`、受支持的搜索参数或工具定义统一转换为网页端联网搜索；输出中包含检索来源标注，但不维护模型供应商托管搜索工具的状态机 |
| 图像输入 | 接收 URL、data URL 以及对应协议的图像块；当前轮图片直接提交网页后端渲染，历史上下文图像保留轮次标记，不复用旧上传 ID |
| 文档解析 | 支持内联 base64、data URL 及 HTTP(S) URL；当前轮文档在同一会话内上传，历史轮次文档通过学校端提取的纯文本注入上下文恢复 |

思考配置项包括 Chat 的 `reasoning_effort: "none"`、Messages 的 `thinking: {"type":"disabled"}`、Responses 的 `reasoning: {"effort":"none"}`；启用思考需指定对应的支持值或开启标识。上述参数仅映射为网页端布尔开关；若模型未拆分输出或直接在正文中输出推导步骤，代理不对正文实施拆分过滤。

Chat 协议多模态消息体结构：

```json
[
  {"type":"text","text":"分析所附图片和文档"},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}},
  {"type":"file","file":{"filename":"note.pdf","file_data":"data:application/pdf;base64,..."}}
]
```

将上述数组作为 `messages[].content` 提交，其中 `...` 需替换为 base64 数据。内联单张图片大小限制为 20 MiB，文档限制为 10 MiB；扫描文档的提取效果由学校平台处理服务决定。服务端无持久化 Files API、`file_id` 状态或跨请求缓存，每轮请求所附文件均会重新解析。客户端必须全量提交带有附件声明的历史记录。历史上下文的格式转换及异常边界参考[网页能力取证](docs/GENAI_WEB_CAPABILITIES.md)与[功能验证记录](docs/FEATURE_VALIDATION.md)，早期基于外部文件上传的探索不作为当前设计依据。

## 与重构前版本的区别

对比基线为上游作者第一跳 `main@7e95aab`、第二跳 `main@dbb8f65`；本地 native 重构前冻结版本为 `e682010` 与 `5d3bd12`。上游版本已支持三类客户端协议、网页登录、附件挂载、联网检索及思考字段解析；早期第二跳实现中已包含白名单和 Schema 校验。

| 模块 | 重构前版本 | 当前版本 |
| --- | --- | --- |
| 双跳交互 | 第二跳将工具定义、调用状态与上下文历史压平成字符串传递给 `backend.complete()`；第二跳主动阻断结构化调用透传 | 默认采用 native 模式传递结构化工具定义及上下文，第二跳直接消费第一跳 SSE 结构，网页端的文本化交互集中由第一跳完成 |
| 流式交付 | 第一跳已有正文和思考流式输出；第二跳等待整份文本并校验后才发 SSE | 开始事件先发，正文和思考随上游增量返回；工具调用收全并校验后整批交付 |
| 数据校验 | 第一跳包含宽松 JSON 解析、字面量提取及参数局部修补；在特定路径下丢弃校验失败的调用 | 统一协议规范；严格实施 JSON 语法校验、Schema 约束、ID 校验及并发约束，整批拒绝非法工具调用，禁止参数推断补全 |
| 异常恢复 | 文本重试集中在第二跳层级，缺乏针对单次请求的累计全局时限约束 | 第一跳对空输出与工具语法错误分别计数并受统筹超时控制；native relay 消除冗余重试层 |
| 任务续作 | 本地验收脚本已有自动反馈，原 relay 本身没有通用任务监督器 | 提供独立的 omp 监督入口，依靠真实验收处理计划停滞和业务错误 |
| 边界定义 | 未支持的字段被静默忽略，端点存在易产生完整原生能力的误解 | 对未实现的状态机制、语法约束等显式拦截并返回标准化错误终态 |

过去能被猜测修好的输入，现在可能报错或要求模型重新生成。工具参数仍需收全，恢复也会增加请求数和耗时。自定义 relay backend 需将旧 `complete()` 接口迁移为 `stream()`。完整源码对照与迁移说明见[架构文档第五节](docs/STAGE_NATIVE_FIRST.md#5-与双跳原仓库及本地重构前分支的对照)。

## 验证记录

2026-10-25 验证记录基于真实 omp 环境，经双跳完整架构连接学校网页端模型，分别在独立路径和连续会话下执行覆盖 20 个阶段的 Python/CSV/SQLite 项目：

| 测试项 | deepseek-pro | Kimi-k3 |
| --- | ---: | ---: |
| 执行总耗时 | 595.000 秒 | 1825.531 秒 |
| 阶段完成率 | 20/20 | 20/20 |
| 工具调用总数 | 150 | 110 |
| 第一跳空输出续作 / 工具纠错次数 | 8 / 4 | 0 / 0 |
| 监督器介入反馈轮次 | 0 | 4 |
| 人工交互调整次数 | 0 | 0 |
| 独立自动化验收项通过数 | 71 项通过 | 71 项通过 |

DeepSeek 本轮无需额外监督反馈，第一跳在原请求内恢复了空输出与坏调用。Kimi 的坏 JSON、未知工具和两轮只给计划的停滞由监督器自动反馈恢复；两模型都不是无错误运行。该实现另有第一跳 562 项回归、relay 89 项测试和 259 个子测试通过。以上是已有验收记录，本次文档整理没有重新运行长期测试。

这些记录说明，在已测编码场景中配合监督器，可以连续工作并自动恢复。它们不是官方 API 对照或跨模型性能排名，也不保证任意任务成功；自然样本没有用满配置的恢复预算。详见[实际报告](docs/TEST_REPORT_deepseek_pro.md)与[机器摘要](validation/automatic-recovery-2026-10-25.json)。

## 已知限制

- 学校平台决定模型供应、配额、延迟和上游空输出。元数据与附件处理阶段的过期认证恢复仍有已知缺口。
- 仅提供三类协议的核心功能子集。不支持 Responses 服务端状态维护、custom grammar 约束、未实现的供应商内置托管工具、独立 Files API 以及供应商私有思考状态。Messages 协议中的 `genai-compat-no-signature` 仅作为兼容占位符。
- `max_tokens` 由本地估算并限制，包含思考；`usage` 也是估算，不是厂商实际计费量。直连第一跳的普通文本截断有对应终态；经过 relay 时当前仅接受正常 `stop/tool_calls`，截断会报错。
- 客户端断开或到期会关闭已经取得的 HTTP 响应与 socket。尚未收到上游响应头时，无法立即打断阻塞中的 HTTP 请求，也不能保证学校远端推理同步停止。
- Schema 合法不保证生成的调用语义无害或逻辑正确。客户端仍须维护操作审计、安全环境隔离与实际测试验收；在未挂载任务监督器的标准客户端环境中，超出协议恢复预算后请求仍会直接中断。

## 开发与排查

在第一跳根目录下执行 `pixi run test` 运行测试套件。第二跳环境测试在其自有虚拟环境内运行，见[第二跳文档](https://github.com/FrozenYears/genai2agent-relay/blob/feat/native-first-streaming/README.md)。常用调试命令均从仓库根路径调起，参数说明见[工具使用说明](docs/TOOLS.md)：

```bash
pixi run context-probe --help
pixi run bench-speed --help
pixi run bench-speed --models deepseek-pro --output docs/BENCH_speed.md
pixi run python -B validation/native-first-endurance/run.py run --model deepseek-pro --stages 20 --max-feedback 8 --max-minutes 45 --stage-timeout 1200
```

测速、上下文探测和长期验收会发送真实模型请求并消耗学校额度；写入已有报告路径会覆盖文件，保留历史时请另选路径。排查先核对 `/health`、模型 ID 和三处认证，再看错误 `code`；心跳只表示连接仍在，不代表模型已有有效输出。日志不要包含令牌、base64 或完整文档。

## 文档索引

归档索引见 [docs/README.md](docs/README.md)；实现细节与演进历程见[架构说明](docs/STAGE_NATIVE_FIRST.md)，版本流转记录见 [CHANGELOG](CHANGELOG.md)。网络抓包记录、能力测试记录、Pi 测试细节、模型报告、基准测速数据以及工程记录统一存放于 `docs/` 目录下；`validation/` 下的自动化测试脚本与状态摘要保持同步维护；个人本地测试会话记录、环境缓存与真实秘钥排除在仓库之外。

本项目初始设计承接自 [jollyxenon/shanghaitech-genai2api](https://github.com/jollyxenon/shanghaitech-genai2api) 与前序工作 [HeZeBang/GenAI2OpenAI](https://github.com/HeZeBang/GenAI2OpenAI)；第二跳实现源自 [negativegluon/genai2agent-relay](https://github.com/negativegluon/genai2agent-relay) 的独立中继设计。项目基于 [MIT License](LICENSE) 开源。
