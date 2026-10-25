# 详细使用说明

基础安装和接入地址见[根 README](../README.md)。本页保留客户端配置、附件格式与任务监督用法；所有命令从仓库根目录运行。

## Windows 与启动参数

- 第一跳可用 `pwsh -File run-windows.ps1` 启动，脚本会安装 Pixi 环境并使用仓库内缓存。
- 第二跳的 Windows Python 路径是 `.venv/Scripts/python.exe`；WSL 使用 `.venv/bin/python`，不要共用虚拟环境。
- 第一跳参数说明：`pixi run serve --help`。命令行参数覆盖 `.env`。

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

omp 可通过 OpenAI Chat Completions 协议配置接入，base URL 对应表内 Chat 端点；目前的长期运行验证均基于 `GenAI` provider 下的 `deepseek-pro` 和 `Kimi-k3`。关于 Pi 在附件及工具链路上的具体表现，见[历史 Pi 验证](PI_REAL_WORLD_VALIDATION.md)。

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

将上述数组作为 `messages[].content` 提交，其中 `...` 需替换为 base64 数据。内联单张图片大小限制为 20 MiB，文档限制为 10 MiB；扫描文档的提取效果由学校平台处理服务决定。服务端无持久化 Files API、`file_id` 状态或跨请求缓存，每轮请求所附文件均会重新解析。客户端必须全量提交带有附件声明的历史记录。历史上下文的格式转换及异常边界参考[网页能力取证](GENAI_WEB_CAPABILITIES.md)与[功能验证记录](FEATURE_VALIDATION.md)，早期基于外部文件上传的探索不作为当前设计依据。

## 已知限制

- 学校平台决定模型供应、配额、延迟和上游空输出。元数据与附件处理阶段的过期认证恢复仍有已知缺口。
- 仅提供三类协议的核心功能子集。不支持 Responses 服务端状态维护、custom grammar 约束、未实现的供应商内置托管工具、独立 Files API 以及供应商私有思考状态。Messages 协议中的 `genai-compat-no-signature` 仅作为兼容占位符。
- `max_tokens` 由本地估算并限制，包含思考；`usage` 也是估算，不是厂商实际计费量。直连第一跳的普通文本截断有对应终态；经过 relay 时当前仅接受正常 `stop/tool_calls`，截断会报错。
- 客户端断开或到期会关闭已经取得的 HTTP 响应与 socket。尚未收到上游响应头时，无法立即打断阻塞中的 HTTP 请求，也不能保证学校远端推理同步停止。
- Schema 合法不保证生成的调用语义无害或逻辑正确。客户端仍须维护操作审计、安全环境隔离与实际测试验收；在未挂载任务监督器的标准客户端环境中，超出协议恢复预算后请求仍会直接中断。

