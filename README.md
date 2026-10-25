# GenAI2OpenAI

把上海科技大学 GenAI 网页模型接入本地 agent，提供 Chat Completions、Messages 与 Responses 兼容接口。

[快速开始](#快速开始) · [客户端配置](#客户端配置) · [重构对比](#与重构前的区别) · [实测](#模型实测) · [详细文档](docs/README.md)

> [!NOTE]
> 当前版本在两仓库的 `feat/native-first-streaming` 分支，尚未合并至 `main`。
> 需学校 GenAI 网页权限，无需申请模型厂商或学校独立 API Key。

```mermaid
flowchart LR
    A[本地 agent] --> R[relay · 31110]
    R --> F[本仓库 · 31100]
    F --> W[学校 GenAI 网页后端]
```

- 本仓库：网页登录、附件处理、网页请求与严格工具解析。
- [relay](https://github.com/FrozenYears/genai2agent-relay/tree/feat/native-first-streaming)：协议转换，结构化工具与 SSE 转发。
- 本地 agent：执行工具、读写文件、运行命令；代理不执行这些操作。

## 快速开始

需 Git、[Pixi](https://pixi.sh/) 与 [uv](https://docs.astral.sh/uv/)。以下为 Bash 命令（Linux / WSL）；Windows 命令见下方折叠项。

### 1. 第一跳

```bash
git clone --branch feat/native-first-streaming https://github.com/FrozenYears/shanghaitech-genai2api.git
git clone --branch feat/native-first-streaming https://github.com/FrozenYears/genai2agent-relay.git
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

### 2. 第二跳

在两仓库的父目录下另开终端：

```bash
cd genai2agent-relay
uv venv .venv
uv pip install --python .venv/bin/python -e .
cp .env.example .env
```

编辑 relay 的 `.env`，并保持两跳同时运行：

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

<details>
<summary>Windows 命令差异</summary>

- 复制配置：`Copy-Item .env.example .env`。
- 启动第一跳：`pwsh -File run-windows.ps1`，脚本会自动安装环境并使用缓存。
- 安装 relay：`uv pip install --python .venv/Scripts/python.exe -e .`。
- 启动 relay：`.venv/Scripts/python.exe main.py`。
- WSL 与 Windows 请独立建环境，不共用 `.venv`。

</details>

### 3. 检查连接

```bash
curl -fsS http://127.0.0.1:31100/health
curl -fsS http://127.0.0.1:31110/health
curl -fsS http://127.0.0.1:31110/v1/models \
  -H 'Authorization: Bearer replace-with-relay-key'
```

健康检查仅说明服务存活。请求须使用模型列表中的真实 `id`（如 `deepseek-pro`、`Kimi-k3`），代理不做别名映射。

## 客户端配置

客户端 Key 填 `RELAY_API_KEY`，支持 Bearer 或 `x-api-key`。

| 协议 | Base URL | 请求路径 |
| --- | --- | --- |
| Chat Completions | `http://127.0.0.1:31110/v1` | `/chat/completions` |
| Messages | `http://127.0.0.1:31110` | `/v1/messages` |
| Responses | `http://127.0.0.1:31110/v1` | `/responses` |

**Claude Code**，在同一终端下设置环境变量：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:31110
export ANTHROPIC_AUTH_TOKEN=replace-with-relay-key
export ANTHROPIC_MODEL=deepseek-pro
claude
```

**Codex CLI**，写入 `~/.codex/config.toml`：

```toml
model = "deepseek-pro"
model_provider = "genai"

[model_providers.genai]
name = "ShanghaiTech GenAI relay"
base_url = "http://127.0.0.1:31110/v1"
wire_api = "responses"
env_key = "GENAI_RELAY_API_KEY"
```

启动前将 `GENAI_RELAY_API_KEY` 设为 relay Key。omp 使用表中的 Chat 地址。若客户端依赖 custom grammar 或服务端状态，需调整配置，详见[接入说明](docs/USAGE.md#客户端接入)。

## 网页模型如何调用工具

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

## 工具、恢复与边界

- 支持整批工具校验；正文与思考可流式返回，工具参数收全且校验通过后才交付。
- 支持图片、文档、网页搜索与思考开关，格式与限制见[详细用法](docs/USAGE.md#图片文档搜索与思考)。
- 第一跳默认额外空继续 **8 次**、未交付坏调用纠错 **3 次**，共用 **900 秒**请求窗口；计数不重置，relay native 不叠加重试。
- 已交付正文/思考、断流、网络错误或截断均不自动重放；限额耗尽返回真实错误。
- [可选 omp 监督器](docs/USAGE.md#可选的-omp-任务监督)根据独立验收自动反馈续作；预算耗尽即保存状态退出，不自动接管其它客户端。

> [!IMPORTANT]
> `native` 仅指两跳间的结构化传输，学校网页仍需文本适配。
> 本项目不提供 Files API、Responses 服务端状态、custom grammar 或完整供应商思考状态；工具权限与隔离仍由客户端负责。

## 与重构前的区别

原版已支持三协议、附件与 Schema 校验，架构差异如下：

| 项目 | 重构前 | 当前 |
| --- | --- | --- |
| 两跳传输 | 第二跳将工具与历史拼成文本，调用 `complete()` | 默认结构化 native，消费 `stream()` |
| SSE | 第一跳已有流式；第二跳等待完整文本 | 优先发送起始事件，正文/思考增量返回 |
| 校验 | 第一跳存在宽松解析与参数修补 | 严格解析并整批校验，不猜参数 |
| 恢复 | 文本重试主要在第二跳 | 单次请求由第一跳恢复，任务验收由可选监督器负责 |

冻结版本、兼容代价与源码依据见[架构对照](docs/STAGE_NATIVE_FIRST.md#5-与双跳原仓库及本地重构前分支的对照)。

## 模型实测

2026-10-25，真实 omp 经双跳完成同一套 Python/CSV/SQLite 任务，均无人工纠错：

| 模型 | 完成阶段 | 耗时 | 监督器自动反馈 |
| --- | ---: | ---: | ---: |
| DeepSeek | 20/20 | 595.000 秒 | 0 次 |
| Kimi | 20/20 | 1825.531 秒 | 4 次 |

- 两者最终独立验收均通过 71 项。
- DeepSeek 在第一跳恢复空完成与坏调用；Kimi 经自动反馈恢复协议错误及只给计划的停滞。
- 这是已有样本，非官方 API 对照或稳定性保证。[完整记录](docs/TEST_REPORT_deepseek_pro.md)

## 更多文档

[配置与附件](docs/USAGE.md) · [架构](docs/STAGE_NATIVE_FIRST.md) · [工具参数](docs/TOOLS.md) · [文档索引](docs/README.md) · [变更记录](CHANGELOG.md)

测试命令：`pixi run test`。平台配额、模型延迟及认证失效处理见[已知限制](docs/USAGE.md#已知限制)。

基于 [jollyxenon/shanghaitech-genai2api](https://github.com/jollyxenon/shanghaitech-genai2api) 与 [HeZeBang/GenAI2OpenAI](https://github.com/HeZeBang/GenAI2OpenAI)，relay 基于 [negativegluon/genai2agent-relay](https://github.com/negativegluon/genai2agent-relay)。[MIT License](LICENSE)。
