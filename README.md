# GenAI2OpenAI

把上海科技大学 GenAI 网页模型接入本地 agent，提供 Chat Completions、Messages 与 Responses 兼容接口。

## 目录

- [部署与接入](#部署与接入)
  - [快速开始](#快速开始)
  - [Windows 与启动参数](#windows-与启动参数)
  - [客户端配置](#客户端配置)
- [功能与设计](#功能与设计)
  - [两跳分工](#两跳分工)
  - [网页模型如何调用工具](#网页模型如何调用工具)
  - [工具、恢复与边界](#工具恢复与边界)
  - [图片、文档、搜索与思考](#图片文档搜索与思考)
  - [已知限制](#已知限制)
  - [与重构前的区别](#与重构前的区别)
- [实测与资料](#实测与资料)
  - [模型实测](#模型实测)
  - [更多文档](#更多文档)

## 部署与接入

> [!NOTE]
> 两个仓库均使用 `main` 分支，安装时保持版本一致。
> 需学校 GenAI 网页权限，无需申请模型厂商或学校独立 API Key。

### 快速开始

需 Git、[Pixi](https://pixi.sh/) 与 [uv](https://docs.astral.sh/uv/)。以下为 Bash 命令（Linux / WSL）；Windows 命令直接列在下方。

#### 1. 第一跳（shanghaitech-genai2api）

```bash
git clone --branch main https://github.com/FrozenYears/shanghaitech-genai2api.git
git clone --branch main https://github.com/FrozenYears/genai2agent-relay.git
cd shanghaitech-genai2api
pixi install
cp .env.example .env
```

编辑本仓库 `.env`：

```dotenv
GENAI_TOKEN=student_id@password
API_KEY=replace-with-first-hop-key
PORT=31100
API_FORMAT=both
GENAI_EMPTY_CONTINUATIONS=8
GENAI_TOOL_REPAIR_ATTEMPTS=3
GENAI_TOOL_REQUEST_TIMEOUT_SECONDS=900
```

- `GENAI_TOKEN`：学号密码或网页 JWT；学号密码模式经 CAS 登录并尝试刷新。
- `API_KEY`：第一跳本地密钥，填入 relay 的 `UPSTREAM_API_KEY`。
- 恢复参数保留默认即可；命令行参数可覆盖 `.env`。

```bash
pixi run serve
```

> [!WARNING]
> 第一跳当前监听 `0.0.0.0`，不支持通过 `HOST` 改为回环地址。
> 请设置本地 Key 并限制网络访问；切勿暴露公网或提交 `.env`。

#### 2. 第二跳（genai2agent-relay）

在两仓库的父目录下另开终端：

```bash
cd genai2agent-relay
uv venv .venv
uv pip install --python .venv/bin/python -e .
cp .env.example .env
```

编辑 relay 的 `.env`，并保持两个服务同时运行：

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

| 凭据 | 谁使用 |
| --- | --- |
| `GENAI_TOKEN` | 第一跳登录学校；不交给客户端 |
| `API_KEY` = `UPSTREAM_API_KEY` | relay 访问第一跳，两处值相同 |
| `RELAY_API_KEY` | 客户端访问 relay，另设一个值 |

#### Windows 与启动参数

- 复制配置：`Copy-Item .env.example .env`。
- 第一跳启动：`pwsh -File run-windows.ps1`，脚本会安装 Pixi 环境并使用本地缓存。
- relay 安装：`uv pip install --python .venv/Scripts/python.exe -e .`。
- relay 启动：`.venv/Scripts/python.exe main.py`。
- WSL 与 Windows 各自建环境，不共用 `.venv`；跨环境回环互通需自行确认。

第一跳的命令行参数覆盖 `.env`，例如：

```bash
pixi run serve --port 31100 --api-format both --empty-continuations 8 --tool-repair-attempts 3 --tool-request-timeout 900
```

全部参数可用 `pixi run serve --help` 查看；`HOST` 只控制 relay，不控制第一跳监听地址。

#### 3. 检查连接

```bash
curl -fsS http://127.0.0.1:31100/health
curl -fsS http://127.0.0.1:31110/health
curl -fsS http://127.0.0.1:31110/v1/models \
  -H 'Authorization: Bearer replace-with-relay-key'
```

健康检查仅说明服务存活。请求须使用模型列表中的真实 `id`（如 `deepseek-pro`、`Kimi-k3`），代理不做别名映射。

### 客户端配置

客户端 Key 填 `RELAY_API_KEY`，支持 Bearer 或 `x-api-key`。

| 协议 | Base URL | 请求路径 |
| --- | --- | --- |
| Chat Completions | `http://127.0.0.1:31110/v1` | `/chat/completions` |
| Messages | `http://127.0.0.1:31110` | `/v1/messages` |
| Responses | `http://127.0.0.1:31110/v1` | `/responses` |

<details>
<summary>展开 Claude Code、Codex、omp/Pi 配置与请求示例</summary>

#### Claude Code

在启动 Claude Code 的同一终端设置：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:31110
export ANTHROPIC_AUTH_TOKEN=replace-with-relay-key
export ANTHROPIC_MODEL=deepseek-pro
claude
```

- 若配置了子任务默认模型，也须使用平台实际存在的 ID。
- 这是第三方协议适配，不代表 Anthropic 官方支持学校模型。[网关配置参考](https://code.claude.com/docs/en/llm-gateway-connect)

#### Codex CLI 与其他 Responses 客户端

在用户级 `~/.codex/config.toml` 中配置：

```toml
model = "deepseek-pro"
model_provider = "genai"

[model_providers.genai]
name = "ShanghaiTech GenAI relay"
base_url = "http://127.0.0.1:31110/v1"
wire_api = "responses"
env_key = "GENAI_RELAY_API_KEY"
```

启动前将 `GENAI_RELAY_API_KEY` 设为 relay Key：

```bash
export GENAI_RELAY_API_KEY=replace-with-relay-key
codex
```

客户端须发送完整历史，不要使用 `previous_response_id`、`item_reference` 或 `store=true`。若客户端要求 custom grammar 或未实现托管工具，需要调整工具声明或配置，不能保证只改地址就能用。[provider 配置参考](https://developers.openai.com/codex/config-advanced/)

#### omp 与 Pi

- omp 使用上表的 Chat 地址。长期测试记录使用 `GenAI` provider 下的 `deepseek-pro`、`Kimi-k3`。
- Pi 的 `@PDF` 不等于文档上传；附件延迟、超时和显式上传的区别见[Pi 实测](docs/PI_REAL_WORLD_VALIDATION.md)。

#### 最小流式请求

```bash
curl -N http://127.0.0.1:31110/v1/chat/completions \
  -H 'Authorization: Bearer replace-with-relay-key' \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-pro","messages":[{"role":"user","content":"回答 READY"}],"stream":true}'
```

</details>

## 功能与设计

### 两跳分工

```mermaid
flowchart LR
    A[本地 agent] --> R["第二跳 · 31110<br/>genai2agent-relay"]
    R --> F["第一跳 · 31100<br/>shanghaitech-genai2api"]
    F --> W[学校 GenAI 网页后端]
```

两跳名称沿用项目约定：客户端请求先到第二跳 relay，再到第一跳。

- 本仓库：网页登录、附件处理、网页请求与严格工具解析。
- [relay](https://github.com/FrozenYears/genai2agent-relay/tree/main)：协议转换，结构化工具与 SSE 转发。
- 本地 agent：执行工具、读写文件、运行命令；代理不执行这些操作。

### 网页模型如何调用工具

网页无原生工具通道时，代理通过“文本约定 + 校验 + 客户端执行”完成调用：

1. **声明**：agent 提交工具名、JSON Schema 与历史，relay 透传给第一跳。
2. **生成**：第一跳将规则注入网页提示词，模型以普通文本输出封套或工具标记。
3. **转换**：第一跳严格解析并整批校验，relay 返回标准工具事件；不猜参数，不放行错误批次。
4. **执行**：本地 agent 按权限执行，携带调用 ID 回传结果，进入下一轮；模型和代理都不代替执行器读写文件。

例如客户端声明 `read_file` 工具时，模型可按对应 Schema 输出：

```text
@@ACTION@@{"calls":[{"operation":"read_file","parameters":{"path":"README.md"}}]}@@END_ACTION@@
```

模型须能遵循约定；输出格式错误将按限额纠错或直接报错。上游若直接返回结构化调用，则优先保留并校验。

### 工具、恢复与边界

- 正文与思考即时流式返回；工具参数收全且整批校验通过后才交付。已撤销整代缓冲，不再为了隐藏失败尝试而延后全部内容。
- 支持图片、文档、网页搜索与思考开关，格式与限制见[下方说明](#图片文档搜索与思考)。
- 第一跳默认最多额外空继续 **8 次**、未交付坏调用纠错 **3 次**，共用 **900 秒**请求窗口；计数不重置，relay native 不叠加重试。
- 当前回退版本在已交付正文/思考后不再纠错；断流、网络错误、截断、取消或到期均不重放，预算耗尽返回真实错误。
- [可选 omp 监督器](docs/USAGE.md#可选的-omp-任务监督)根据独立验收自动反馈续作；预算耗尽即保存状态退出，不自动接管其它客户端。

> [!IMPORTANT]
> `native` 仅指两跳间的结构化传输，学校网页仍需文本适配。
> 本项目不提供 Files API、Responses 服务端状态、custom grammar 或完整供应商思考状态；工具权限与隔离仍由客户端负责。

### 图片、文档、搜索与思考

| 功能 | 输入与处理 | 边界 |
| --- | --- | --- |
| 图片 | URL、data URL 或协议图片块；历史图片保留在原轮次 | 内联单张小于 20 MiB；效果与延迟取决于上游 |
| 文档 | 内联 base64、data URL 或 HTTP(S) URL；当前轮上传与聊天共用会话 | 小于 10 MiB；历史文档用平台解析文本恢复，扫描件效果不保证 |
| 搜索 | `web_search: true` 或受支持的搜索声明，映射为网页搜索 | 正文来源与补充结果分别标注，不实现厂商托管搜索工具的完整流程 |
| 思考 | `thinking: true/false`，其他推理参数映射为布尔开关 | 不支持精确 effort 档位或 token budget；只透传独立思考字段 |

<details>
<summary>展开各协议的开关与 Chat 附件示例</summary>

| 协议 | 关闭思考的写法 |
| --- | --- |
| Chat | `"reasoning_effort": "none"` |
| Messages | `"thinking": {"type":"disabled"}` |
| Responses | `"reasoning": {"effort":"none"}` |

启用时使用对应的受支持值；三协议也接受扩展字段 `thinking: true/false`。模型可能不返回思考，或把分析写入正文，代理不按语义拆分。

Chat 的 `messages[].content` 可使用以下数组，需将 `...` 替换为真实 base64：

```json
[
  {"type":"text","text":"分析所附图片和文档"},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}},
  {"type":"file","file":{"filename":"note.pdf","file_data":"data:application/pdf;base64,..."}}
]
```

每次请求会重新处理所附文件，客户端应发送完整历史及需要保留的附件。没有 Files API、持久 `file_id` 或跨请求附件缓存。协议证据与历史样本见[网页能力](docs/GENAI_WEB_CAPABILITIES.md)和[功能记录](docs/FEATURE_VALIDATION.md)。

</details>

### 已知限制

- 学校平台决定模型供应、配额、延迟和空输出；元数据与附件处理阶段的过期认证恢复仍有缺口。
- 不支持 Responses 服务端状态、custom grammar、未实现的托管工具或 Files API；客户端需发送完整历史。
- 不恢复厂商加密思考状态；Messages 的 `genai-compat-no-signature` 是兼容占位，不是真实 Anthropic 签名。
- `max_tokens` 和 `usage` 为本地估算，包含思考。直连第一跳的普通文本截断有对应终态，经过 relay 时截断会报错。
- 取消或到期会关闭已取得的响应和 socket；等待上游响应头的阻塞请求不能立即打断，也不保证远端推理同步停止。
- Schema 合法不保证操作安全或代码正确。客户端负责授权和隔离；未启用监督器时，恢复耗尽后任务仍可能停止。

### 与重构前的区别

原版两跳已有三协议与附件，原 relay 已有 Schema 校验。主要变化：

| 项目 | 重构前 | 当前 |
| --- | --- | --- |
| 两跳传输 | 第二跳将工具与历史写成文本，等待完整回复 | 保留工具结构，逐段接收第一跳的回复 |
| SSE | 第一跳已有流式；第二跳等待完整文本 | 起始、正文、思考和心跳实时返回，工具批次仍等完整校验 |
| 校验 | 第一跳存在宽松解析与参数修补 | 严格解析并整批校验，不猜参数 |
| 恢复 | 文本重试主要在第二跳 | 单次请求由第一跳恢复，任务验收由可选监督器负责 |

冻结版本、兼容代价与源码依据见[架构对照](docs/STAGE_NATIVE_FIRST.md#5-与双跳原仓库及本地重构前分支的对照)。

## 实测与资料

### 模型实测

下表保留历史实测；10-26一列对应已回退的整代暂存版`99d12cf`，不是当前实时输出版本的验证结果。任务均为同一套Python/CSV/SQLite持续开发项目。

| 测试项 | deepseek-pro（10-25） | Kimi-k3（10-25） | Kimi-k3（10-26，整代暂存版） |
| --- | ---: | ---: | ---: |
| 执行总耗时 | 595.000 秒 | 1825.531 秒 | 2180.765 秒 |
| 阶段完成率 | 20/20 | 20/20 | 20/20 |
| 工具调用总数 | 150 | 110 | 122 |
| 第一跳空输出续作 / 工具纠错次数 | 8 / 4 | 0 / 0 | 未单独归因 |
| 监督器介入反馈轮次 | 0 | 4 | 0 |
| 人工交互调整次数 | 0 | 0 | 0 |
| 独立自动化验收项通过数 | 71 项通过 | 71 项通过 | 71 项通过 |

- 10-26 样本无故障注入，关闭客户端自动重试，额外反馈预算为 0；90 次模型请求均在正式双跳完成，客户端未收到未恢复的协议错误。
- 阶段 15 有一次 SQLite 迁移业务验收失败，Kimi 在原回合修正后通过；这是一次 `bash` 验收错误，不是协议中断。共享服务有并发流量，第一跳恢复次数未单独归因，不能记作 0。
- 10-25 的 DeepSeek 在第一跳恢复空完成与坏调用；Kimi 经自动反馈恢复协议错误及只给计划的停滞。不同日期、预算和服务负载下的样本不是受控对照，不据此作速度排名或稳定性保证。
- [今日脱敏实测结果](validation/kimi-formal-2026-10-26.json) · [完整记录](docs/TEST_REPORT_deepseek_pro.md)

### 更多文档

[任务监督用法](docs/USAGE.md) · [架构](docs/STAGE_NATIVE_FIRST.md) · [工具参数](docs/TOOLS.md) · [文档索引](docs/README.md) · [变更记录](CHANGELOG.md)

测试命令：`pixi run test`。本轮发布收录 10-26 的正式 Kimi 实测，不重写 10-25 的历史结果，也未重测 DeepSeek。

基于 [jollyxenon/shanghaitech-genai2api](https://github.com/jollyxenon/shanghaitech-genai2api) 与 [HeZeBang/GenAI2OpenAI](https://github.com/HeZeBang/GenAI2OpenAI)，relay 基于 [negativegluon/genai2agent-relay](https://github.com/negativegluon/genai2agent-relay)。[MIT License](LICENSE)。
