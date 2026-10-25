# 原生优先阶段说明：两跳链路与重构边界

本文记录 `feat/native-first-streaming` 阶段的实现结构。重构实现基线分别为根仓库 `a7b3186`、relay 仓库 `44639ec`；“旧版”指这两个实现提交之前的版本。架构说明与正式服务验收分开记录：历史短探针、单次工具闭环不能合并成新版本的长时工作结论。

## 1. 当前采用的路线

```mermaid
flowchart TD
    A["Agent 客户端：持有完整历史、执行本地工具"] -->|"Chat / Messages / Responses；本地 RELAY_API_KEY"| R["第二跳 relay：127.0.0.1:31110"]
    R -->|"默认 native；结构化 Chat tools / 调用 ID / 结果；本地 UPSTREAM_API_KEY"| F["第一跳：127.0.0.1:31100"]
    F -->|"网页登录认证；chatInfo / messages / 附件 / thinking / netGo"| W["学校 GenAI 网页后端：genai.shanghaitech.edu.cn/htk/chat/start/chat"]
    W -->|"正文 / 独立思考 / 实际可得的工具表示"| F
    F -->|"严格解析与整批校验；结构化 SSE"| R
    R -->|"协议转换；安全增量；校验后的完整调用"| A
```

“第一跳/第二跳”沿用项目命名，不是按客户端请求经过的先后编号。第一跳自身也提供三种兼容 API，可以独立接客户端；**本阶段两跳路径中，relay 只通过第一跳的 Chat Completions 接口通信**。

因无法申请独立 API Key，本阶段明确放弃学校独立官方 API（`genaiapi.shanghaitech.edu.cn`）路线，不提供远程 base 示例、申请指南或绕过网页第一跳的直连方案。这一决策不删除本地 `UPSTREAM_BASE_URL`，也不把 `UPSTREAM_TOOL_MODE=native` 改为 `text`。

认证是三个不同边界：学校网页 JWT／账号认证由第一跳的 [`TokenManager`](auth/token_manager.py) 管理；relay 的 `UPSTREAM_API_KEY` 应对应第一跳本地代理 Key；客户端使用 `RELAY_API_KEY` 访问 relay。后两者不是学校独立 API Key。未配置本地 Key 不等于具备安全隔离，服务应限制在受信网络。

## 2. 每层做什么、不做什么

### 客户端：持有工作状态并真正执行工具

客户端发送模型 ID、系统指令、完整历史、工具 Schema、选择/并行约束以及附件。收到完整且校验通过的 `tool_calls`、`tool_use` 或 `function_call` 后，由客户端按自身权限执行 Read/Write/Edit/Bash 等工具，再携带同一调用 ID 回传结果。两层代理都不会执行模型提出的命令，也不会因为参数里出现路径就读取本地工作文件。

代理没有可恢复的服务端会话存储；Responses 的 `previous_response_id`、`item_reference`、`store=true` 不能替代完整历史。调用格式正确只是传输条件，不是执行授权，也不证明写出的代码或文件内容正确。

### 第二跳：统一协议，尽量不丢结构

- **请求与历史**：三种入口转换为 `RelayRequest`。默认 native 保留 system/developer 角色、assistant 历史调用、原调用 ID、tool 结果及 `is_error`；Messages 的 `tool_use_id`、Responses 的 `call_id` 转成对应 Chat 关联字段，不按结果顺序猜归属。模型 ID 不由 relay 猜测映射。
- **工具**：普通函数保留名称、Schema 和可表达的 strict 标记。custom text 工具包装为参数 `{"input": string}` 的上游函数；Responses namespace 内部使用 `namespace.leaf`，返回时拆回 namespace/name。custom grammar 和不支持的托管工具不是静默降级，而是拒绝。
- **附件**：保留文本、图片、文档内容块及工具结果中的附件，转换外壳而不把 base64 展开成提示词正文；真正网页上传由第一跳完成。
- **思考**：将客户端可表达的 thinking/reasoning 设置映射为第一跳布尔开关；历史思考及元数据在 Chat 可表达的字段中保留。保留字段不等于网页会消费它们，更不等于跨供应商加密思考状态可以恢复。
- **搜索**：识别支持的 web_search 声明，转为第一跳搜索选项，而不是交付为客户端本地函数，也不伪造供应商托管搜索生命周期。
- **输出**：立即消费第一跳 SSE；正文与独立思考增量向下传递，工具参数等待终态后整批校验。适配为客户端要求的协议，不把第一跳已经解析的调用再次退化成文本再猜一次。

关键实现：[`prepare_chat`](genai2agent-relay/relay/normalize.py)、[`prepare_messages`](genai2agent-relay/relay/messages.py)、[`prepare_responses`](genai2agent-relay/relay/responses.py)、[`request_options`](genai2agent-relay/relay/features.py)、[`ActionRelay.stream` / `_prepare_messages`](genai2agent-relay/relay/engine.py)、[`UpstreamClient._payload` / `stream`](genai2agent-relay/relay/upstream.py)。

### 第一跳：适配网页协议，而不是冒充学校原生函数 API

1. Chat 入口先检查工具定义与约束，再用 `inject_tool_prompt` 将工具提示放在最新 user 轮附近。`normalize_tool_history` 把历史 assistant 调用写成带 ID 的 action 封套，把 tool 结果写成含 `call_id/output/is_error` 的已执行结果上下文；用户/结果中的控制标记作可逆转义，附件内容不因此改写。
2. `prepare_request` 处理附件与网页选项。最新 user 的文本成为 `chatInfo`，其余规范化消息成为 `messages`；请求模型名作为 `aiType`，元数据用于取得 `rootAiType`。不会凭空把网页端点变成接受标准 `tools` 的函数接口。
3. 当前轮图片以原 URL/data URL 放入 `imageUrls`；历史图片保留在该轮多模态消息。文档上传到网页业务接口：当前轮以同一请求会话的 `fileIds` 引用，历史文档以平台解析文本恢复上下文。每轮重新处理随请求携带的附件，没有附件缓存、`/v1/files` 或持久 `file_id` 存储。附件预处理发生在第一跳开始 SSE 之前，因此开始事件并不保证附件准备零等待。
4. 思考参数只控制布尔 `thinking`，不能实现不同 effort 档位或精确推理预算；独立 `reasoning`/`reasoning_content` 才映射为客户端思考字段，不凭正文语义猜测。搜索映射为 `netGo`，正文引用保留，额外查询的链接单独标为“补充检索结果（独立于正文引用编号）”；补充查询失败保留已有正文，不编造引用对应关系。
5. 网页返回结构化 `tool_calls` 时优先保留；否则从明确闭合的 Kimi XTML、DeepSeek DSML、XML 或 action 封套中严格提取。代码引用和 `<think>` 中的示例不是执行通道；不从裸 JSON 或函数样式文本猜调用，不修补引号、标签、参数名或类型。

关键实现：[`chat_completions`](api/chat.py)、[`inject_tool_prompt` / `normalize_tool_history`](tools/prompts.py)、[`prepare_request` / `search_sources`](provider/features.py)、[`split_history_messages` / `_iter_genai_stream`](provider/genai.py)、[`extract_tool_calls`](tools/parsing.py)、[`parse_kimi_output`](tools/kimi.py)。

## 3. native 与 text 的准确含义

| 项目 | 默认 `native` | 显式 `text` |
| --- | --- | --- |
| relay → 第一跳 | 标准 Chat 工具字段、结构化历史、流式调用字段 | 普通对话文本，工具定义/调用/结果编码为文本 |
| 系统与开发者指令 | relay 保留角色 | relay 转为普通上下文 |
| 工具历史 | 保留调用 ID、tool 角色与关联字段 | 封套保留 ID，结果按 ID 关联；附件仍保留 |
| 当前工具协议 | 由第一跳负责网页提示/解析 | relay 要求单个最终 `@@ACTION@@…@@END_ACTION@@` 封套 |
| relay 生成重试 | 不自动降级或重放模型生成 | 只有明确尝试了坏封套且尚未交付正文/思考时，才可按预算请求重写 |
| 适用范围 | 当前本地第一跳，两个仓库一起升级 | 明确只有文本能力的后端；不是 native 出错后的自动补救 |

这里有两种不同的“原生”：**relay 与第一跳之间的原生结构化传输**，以及**模型实际输出的结构化字段或 Kimi/DeepSeek 原生标记**。二者都不证明学校网页开放了原生函数输入。此前向网页直接附加 `tools/tool_choice` 的 DeepSeek、Kimi 短探针均未返回结构化工具字段；它们只是当时的负面样本，不是所有模型/场景的完备能力证明。

## 4. SSE 生命周期、终态与取消

### 正常完成

1. relay 在模型完成前发出协议开始事件：Chat 的 assistant role chunk；Messages 的 `message_start`；Responses 的 `response.created`、`response.in_progress`。第一跳自己的流式接口也先开始，再读取网页输出。
2. 第一跳 `heartbeat_events` 在等待期间以 10 秒间隔产生心跳，通过有界队列桥接读取线程。relay 转发上游心跳：Chat/Responses 使用 SSE 注释，Messages 使用 `ping`，没有另开定时心跳线程。
3. 安全正文与独立思考持续交付；潜在工具标记跨 chunk 时留存前缀，发现真实工具区域后缓存，不把半截参数漏成可执行调用。
4. 第一跳确认网页终态、严格解析并校验整批调用后才输出结构化工具事件。relay 的 `UpstreamClient.stream` 还要求第一跳同时有 `finish_reason` 与 `[DONE]`，调用索引连续、调用终态一致；`ActionRelay` 再检查完整批次。
5. 校验通过后，relay 发出完整工具事件，再成功结束：Chat 的 finish chunk + `[DONE]`；Messages 的完整块及 `message_delta`/`message_stop`；Responses 的 item/arguments done 与 `response.completed`。Responses 的 `sequence_number` 单调递增，生命周期内 ID 稳定。协议可能使用名为 delta 的参数事件，但其中工具参数已经整体校验，不是允许执行的半成品。

### 校验、错误与截断

- 入口拒绝不合法请求；输出同时检查工具白名单、JSON Schema、重复 JSON 键、非有限数、重复 ID、`none/required/指定工具` 与禁止并行约束。任意坏成员使整批失败，不删除坏成员再交付其他调用。Schema 引用仅限本地，不为校验访问外部网络。
- 明确坏封套、多封套、缺标签、提前 EOF、工具参数截断均不能变成成功调用。已经交付的正文无法撤回，但工具不会因此提前执行。
- 已发送响应头后，错误通过 SSE error 传播，不再发送成功终态；relay Responses 还发送 `response.failed`。非流式上游/传输失败返回 502，入口参数错误返回 400。
- 第一跳按本地估算 token 强制输出预算，包含思考；普通文本截断分别为 Chat `length`、Messages `max_tokens`、Responses `response.incomplete`，工具截断报错。**经过 relay 时，当前引擎只接受 `stop/tool_calls` 成功终态；第一跳的 `length` 会变成 relay 错误，不应把直连第一跳的截断行为套用到两跳链路。**
- relay 的 `MAX_ACTION_BYTES` 限制解码后正文、思考和工具有效载荷，也约束单个未闭合 SSE 事件；重复帧元数据/心跳不占工具参数预算。Schema/strict 标记和交付前校验不是生成端 constrained decoding。

### 取消与重试边界

关闭消费流会沿生成器 `finally` 关闭上游响应；第一跳 `StreamControl` 设置取消标志并关闭已取得的响应/socket，避免后台继续读取。尚未收到响应头的阻塞连接仍需等待请求返回/超时才能清理；不能承诺立即终止学校远端推理，也不能回滚客户端已执行的工具。

relay native 不重试模型生成。显式 text 的有限封套纠错也不重试工具执行，不重放截断、空回答、普通示例或已交付内容。第一跳网页聊天遇到 HTTP 401 时可刷新网页登录令牌后重试，这是认证恢复，不是 native 失败改走另一种工具生成路径。元数据/附件阶段的过期令牌恢复仍有已知缺口。

关键实现：第一跳 [`heartbeat_events` / `StreamControl` / `validated_events`](provider/streaming.py)、[`validate_tool_request` / `validate_tool_calls`](tools/contracts.py)；relay [`validate_native_calls` / `decode_action` / `validate_choice`](genai2agent-relay/relay/actions.py)、[`UpstreamClient.stream`](genai2agent-relay/relay/upstream.py)、[`chat_events` / `message_events` / `response_events`](genai2agent-relay/relay/streaming.py)。

## 5. 与重构前的对照

下表以根仓库 `a7b3186^`、relay 仓库 `44639ec^` 的实现为对照，而不是把旧版描述成完全没有工具或流式支持。

| 维度 | 重构前 | 当前阶段 |
| --- | --- | --- |
| relay 核心接口 | `TextActionRelay` 调用 backend `complete()`，等待整份普通文本回复 | `ActionRelay` 消费 backend `stream()`，默认 native |
| 两跳间工具协议 | relay 把工具定义/历史编成文本，解析最终 action 封套 | relay 向第一跳发送结构化字段，由第一跳单独承担网页文本适配 |
| relay 流式体验 | 核心先收齐结果，下游无法由核心实时获得正文/思考 | 开始事件先发、正文/思考增量传递、工具整批末尾交付 |
| 第一跳解析 | 已支持部分 Kimi/action/DSML；另有宽松 JSON、Python 字面量、裸 JSON/函数样式/围栏猜测、Bash 参数修补等恢复分支 | 保留明确协议，删除猜测/修补分支；优先结构化调用，显式 DSML invoke/parameter 严格解码 |
| 坏调用的处理 | 第一跳部分提取分支会跳过未知工具后继续收集；并非所有入口共用完整批次契约 | 第一跳三种 API 共用 contracts；relay 也做终态整批校验，非法成员不被过滤掉后继续交付 |
| 重试 | 旧 relay 对解析失败可请求重新序列化，对空回复可追加继续提示 | native 不重放；text 只在未交付内容且明确坏封套时有限纠错 |
| 历史关联 | 文本转换为主，结构保留受旧 `TextMessage(role, content)` 限制 | native 显式保留 tool_calls/tool_call_id 与可表达的思考元数据；网页文本层也保留 ID |
| 工具执行者 | 客户端 | 仍是客户端；代理没有新增执行权限 |
| 网页/附件/搜索依赖 | 依赖学校网页后端；已有附件、搜索、思考功能 | 继续使用同一网页路线，不声称改造为学校官方函数 API |

变化的目标是**少做一次有损文本转换、尽早交付安全增量、失败时不产生不完整调用**，不是承诺所有模型都会正确用工具，也不是用“兼容”掩盖格式错误。

## 6. 不能承诺的边界与证据口径

- 学校网页模型可用性、配额、缓存、速度和稳定性由平台决定。首事件较快只代表协议已开始，不代表模型首字或工具参数已完成；长参数仍需等待完整生成。
- 不保证所有客户端工具、附件格式、模型组合均兼容。custom grammar、服务端 Responses 状态及未实现的托管工具明确不支持。客户端能发送文档块，不等于模型会主动选择正确上传流程。
- 思考可能不返回，或以未标记正文出现；不补造思考。Messages 的 `genai-compat-no-signature` 只是兼容占位，不是真实 Anthropic 签名；Responses 不重建加密 reasoning 状态。
- 用量为本地文本估算，不是供应商真实计费量，也不包含准确视觉 token。精确采样、推理档位与预算不能因接收了某字段就宣称网页支持。
- 严格 Schema 只能证明结构符合声明，不能证明命令安全、复制内容正确或任务完成。重要写入仍须客户端实际运行、独立回读/哈希核验，长文件优先局部编辑。
- 本文前六节是源码与协议边界记录；正式重启后的实际验收单独列于第七节。此前的 [Pi 实际验证](PI_REAL_WORLD_VALIDATION.md)、[历史报告](TEST_REPORT_deepseek_pro.md) 不被改写，其旧版结果也不自动证明当前版本稳定。

## 7. 正式部署与长时工作结论

已在原 31100/31110 停止旧服务并加载重构版本。实际监听进程为第一跳 PID103556、relay PID89612；第一跳沿用 0.0.0.0 监听，relay 为 loopback。两端健康检查和新请求校验行为通过，验收结束后保持运行。

主客户端使用用户已配置的 omp 18.12.6，RPC 状态确认两模型都走 `GenAI` provider 的 `http://127.0.0.1:31110/v1`，不是官方 API。两个模型分别保持连续历史，运行同一套 20 阶段实际财务流水项目，使用真实文件/进程/SQLite 和独立累计验收。

Kimi 在约 31 分 44 秒完成全部阶段，但有两次流错误，需要显式继续；DeepSeek 运行约 8 分钟、完成前 9 阶段后，在第 10 阶段因空回复和流错误未能恢复。当前实现不能概括为“长时无故障”，更不能把端口健康或解析器通过等同于 agent 稳定完成任务。详细指标、失败和复现方法见 [正式 omp 验收报告](TEST_REPORT_deepseek_pro.md#2026-10-25正式双跳重启后的-omp-持续开发验收)，机器可读证据见 [验证摘要](validation/omp-endurance-2026-10-25.json)。

