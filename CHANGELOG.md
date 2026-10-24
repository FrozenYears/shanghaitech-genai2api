# Changelog

## Unreleased

### 工具调用可靠性

- 将完整工具定义和 JSON Schema 放在当前 user 轮次，采用 `@@ACTION@@` JSON envelope 作为主调用协议；兼容既有 `<tool_call>` 与 DSML 输出及历史。保留 system/developer 指令、历史结果与附件，要求依赖型写入／编辑／读取分轮执行并回读核验。
- 对 user/tool-history 中的 `<think>`、tool/DSML 标签及 action 分隔符使用可逆 JSON Unicode 转义，防止模型将文件内容误判为控制语法；解析后恢复原字符串。
- 真实 ChatGLM 测试发现含 `<think>`、`</tool_call>` 的文件内容被模型改写，但模型仍声称写入成功。转义输入并改用 JSON envelope 后，真实 write → edit → read 回读逐字符匹配通过。普通文本或必需 `tool_choice` 未返回调用时，三种 API 都报错而非报告成功。
- 拒绝不完整 action envelope、截断／损坏调用及提前结束的上游 SSE，不交付可执行的部分参数。保留 legacy XML/DSML parser compatibility。
- 针对 deepseek-pro／Kimi-k3 的真实工具任务修复完成说明被误判为调用：在 think/DSML 清理前识别原始封套与 Markdown 引用，流式保留未闭合引用，Anthropic 保留引用中的 think 字面数据；relay 原生语法检查不再扫描引用及合法参数。
- 明确工具结果是已执行回执和验证后的正常退出条件。新增两模型 8,389 字节流式文件、截断不交付及 edit 失败恢复的实测记录；如实记录 Kimi 首次复制错误后经回读修正和 DeepSeek 额外一次 read 的限制。
- 新增完整 Kimi K3 XTML typed tools 通道解析，修复实际调用输出被 Anthropic 直连当作普通正文正常结束的问题。三个输出协议共用严格解析，保留原始字符串与 typed JSON；不接受截断、坏类型、未知工具或部分批次。
- 增加真实 33 KB 全文、320 KB 局部编辑、12 阶段持续开发及 11 类工具验证记录；区分模型复制错误、协议遗漏和测试装置错误。用户批准重启后在原端口复核两个模型的 Chat／Messages／Responses 实际工具执行。

### Relay 修复

- 修复 action 标记位于已闭合 Markdown 代码引用中时，普通说明回复被误判为工具调用而返回 502；保留真实调用的 JSON、工具白名单及参数校验，参数中的 Markdown 不参与引用扫描。
- 中继的已有 JSON 序列化重试提示增加解析原因及封套内行列号；不新增日志、配置或重试次数，最终客户端错误保持不变。两次修改记录见 `genai2agent-relay/docs/action-envelope-fixes.zh-CN.md`。
- 中继不再把只有深度思考、没有正文或实际调用的回复当作正常结束；使用现有重试预算提示“请继续”，仍为空则明确报错。空回复同样处理，不新增日志或重试次数。
- 中继拦截引用外少一个尾部 `@` 的 action 开始标记，以及有效开始标记之前的结束标记，接入现有纠错重试；不再原样作为正文返回，不自动修补执行。
- 中继将完整工具定义从历史开头移至当前轮附近；结构及参数校验失败时补充相关 Schema 和缺失字段；保留上游 `finish_reason`，长度截断明确报错且不以相同预算重试，不交付截断回复中的调用。保留历史、附件及原有重试预算，不新增日志或依赖。
- 中继在正文交付前识别代码围栏或 calls XML 中的当前工具调用，触发现有纠错重试而非返回正文结束；不直接执行替代格式，符合调用结构的展示示例也会触发纠错。
- 中继默认及示例配置的 `UPSTREAM_ACTION_RETRIES` 提高到 2，最多三次生成；可恢复错误共享预算，长度截断不重试。

### 验证记录

- 新增 Pi 0.85.1 屏蔽用户插件后的真实场景报告、样本和脱敏请求记录：思考与搜索通过，图片存在长延迟/超时，PDF 需显式调用上传命令；记录工具未执行和令牌过期时流中断的问题，不将这些场景判为通过

### New Features

- 三种 API 接入网页的图片、文档、联网搜索和深度思考开关；支持 URL/base64 图片与内联/URL 文档，当前轮文档通过同会话 `fileIds` 关联
- 图片直接交给上游读取：base64 图片以 data URL、网络图片以原始 URL 放入 `imageUrls`，不再经过网页的图片服务上传（也就不需要前端令牌与宽高参数）；历史图片按网页协议保留在原消息的内容数组中。实测图片延迟从上传方案的十几秒降到 0.3–1.8 秒，多轮历史图片追问也可正常回答
- 联网搜索返回正文链接，并追加独立标注的补充检索列表，避免混淆两次检索的引用编号
- `/v1/models` 增加 `capabilities`；附件日志记录数量、字节数、解析长度与准备耗时

- 新增 `tools/bench_speed.py`：经本地代理逐个测量模型的流式首字时间（TTFT）、正文首字时间与输出速度（tokens/s），提供 `pixi run bench-speed` 任务
- **DSML 工具标记兼容**：部分模型会绕过注入的 `<tool_call>`，直接输出 GenAI 原生的 DSML 标记。代理现在统一处理这些变体：
  - 归一化 `<｜DSML｜tool_call>` / `<｜DSML｜call>` / `<｜DSML｜_call>` / `<｜DSML｜l_call>`（含半角 `|`）为 `<tool_call>`
  - 支持工具名写在属性里的变体 `<｜DSML｜ name="Read">`
  - 兼容空标签名 `<｜DSML｜>` 与混用开闭标签（如 `<tool_call>` 配 `</｜DSML｜>`）
  - 三种接口（Chat / Anthropic / Responses）的流式检测都能识别 DSML 起始标记

### Changes

- 工具参数解析兼容 Python 字面量（单引号字符串、`True`/`False`/`None`），以及缺少 `arguments` 包裹、直接平铺参数键的形态
- 标签内“裸工具名 + 参数 JSON”（如 `read\n{"path": ...}`）也能解析

### Changes

- 上游请求超时由硬编码的 60 秒改为 300 秒（`UPSTREAM_TIMEOUT`），避免大 prompt 首 token 慢被误判为失败
- 流式 Chat 的上游错误改为标准 `{"error": {...}}` 事件，不再把错误文本塞进 `delta.content`
- `max_tokens` 由代理按估算 token 本地强制生效，命中上限时 Chat `finish_reason` 返回 `length`、Anthropic `stop_reason` 返回 `max_tokens`
- `usage.prompt_tokens` / Anthropic `input_tokens` 改为按请求消息估算，不再是写死的 0
- `tool_choice` 指定单个函数时收窄允许工具集合，输出层同时过滤其它工具调用
- `reasoning_effort` / Anthropic `thinking` / Responses `reasoning.effort` 现在映射为上游 `thinking` 布尔开关；不支持精确推理档位及预算，相关映射会记录日志

### Bug Fixes

- 图片和文档不再在工具提示词注入及协议转换时变成 `[image]` 或 base64 文本；工具结果里的附件也保留
- 上游流在结束、出错或客户端断开时关闭 HTTP 响应；附件无效返回 400，上传服务失败返回 502

- 修复上游在 SSE 通道里直接返回纯文本错误（如 worker 不可用时的 `No available workers (all circuits open or unhealthy)`）被当作 `JSONDecodeError` 跳过，导致三种接口都把“上游不可用”伪装成 HTTP 200 空回复；现在会转为标准 error 事件 / 502
- 解析器显式识别 `[DONE]` 结束标记与 SSE 注释/字段行，不再依赖 JSON 解析失败来跳过它们
- 修复 Chat 非流式路径解析流式错误块失败就 `pass`，导致上游错误被静默丢弃成 HTTP 200 空回复
- 移除 Anthropic 流式文本里对 DSML 标签的预先删除：它会在解析器看到工具调用之前把标签删掉，导致工具调用丢失、属性碎片（`name="...">`）泄漏为正文
- 解析失败时输出的是清理过 DSML 标签的文本，而不是原始标签内容

## v2.2.0

### New Features

- **OpenAI Responses API**：新增 `POST /v1/responses`，可直接接入 Codex CLI（`wire_api = "responses"`）与 OpenAI Agents SDK
  - 流式事件按官方格式输出：`response.created` / `response.in_progress` / `response.output_item.added|done` / `response.output_text.delta|done` / `response.reasoning_summary_text.delta|done` / `response.function_call_arguments.delta|done` / `response.custom_tool_call_input.delta|done` / `response.completed`
  - 支持 Responses 的 `input` items：`message`、`function_call`、`function_call_output`、`custom_tool_call`、`reasoning`
  - 支持 `type: "namespace"` 包裹的工具定义（Codex 的默认形态），展平其中的 function 与 custom 工具；`custom` 工具（如 apply_patch）输出为 `custom_tool_call`
  - 每个事件带递增 `sequence_number`，`response.completed` 携带完整 `output` 与 `usage`，并回显请求的 `tools` / `tool_choice` / `parallel_tool_calls` / `store` 等字段
  - 上游因长度截断（`finish_reason = length`）时发出 `response.incomplete`（`incomplete_details.reason = max_output_tokens`），不会误报为正常完成
- **思维链透传**：上游 `reasoning_content` 在三个接口分别以 Chat 的 `reasoning_content`、Anthropic 的 thinking 块、Responses 的 reasoning 事件输出

### Changes

- `provider/genai.py` 抽出统一的 `iter_genai_stream()` 上游流迭代器，Chat / Anthropic / Responses 三个适配层共用，统一处理 401 重试与错误上报
- Chat Completions 流式响应改为整个请求使用同一个 `chatcmpl-*` ID（此前每个 chunk 都会重新生成）
- 工具调用参数统一序列化为合法 JSON 对象（字符串参数会被解析，Bash 的裸字符串映射为 `command`）
- 模型名保持完全透传，不做映射

### Bug Fixes

- Chat Completions 工具流式路径不再丢弃 `reasoning_content`
- Anthropic 非流式响应现在包含 thinking 块，且流式 block index 在有 thinking 时按顺序顺延（不再硬编码 text 为 0、tool 为 1）
- Anthropic 历史里的 `thinking` / `redacted_thinking` 块不再作为普通 JSON 文本转发给上游

### Notes

- Anthropic thinking 的 `signature` 是明确的兼容占位值 `genai-compat-no-signature`，不是真实签名
- Responses API 不提供服务端会话存储，也不支持 `previous_response_id`

## v2.1.0

### Changes

- 依赖管理与运行由 `uv` 迁移到 `pixi`
- 移除 `uv.lock`、`.python-version` 与 `pyproject.toml`，由 `pixi.toml` 作为唯一清单
- 新增 `pixi` 任务：`pixi run serve` 启动代理（等价于 `python main.py`），`pixi run test` 运行单元测试
- 支持从项目根目录 `.env` 读取登录凭据、端口和 API 格式，默认端口为 `31100`
- README 与工具说明同步改用 `pixi` 命令
- 新增 `tools/context_probe.py`：实测 GenAI 模型真实上下文长度，提供 `pixi run context-probe` 任务

### Bug Fixes

- 修正请求构造：把最后一条 user 消息作为 `chatInfo`（当前提问），其余作为历史 `messages` 传入上游，避免提问重复
- 兼容上游以 `reasoning` 字段返回思考内容（原先只识别 `reasoning_content`）
- 处理上游返回 `choices` 缺失但带 `error`/`errMsg` 的响应，避免静默失败

## v2.0.0

### Breaking Changes

- `--token` 参数现在为必需项，移除了硬编码的默认 token
- `Config.token` 字段替换为 `Config.token_manager`，使用 `TokenManager` 对象管理 token 生命周期
- 新增 `pycryptodome` 依赖

### New Features

- **学号密码登录**: `--token` 支持 `学号@密码` 格式，自动通过 CAS 统一身份认证获取 JWT
- **Token 自动刷新**: 学号密码模式下，JWT 过期时自动重新登录，对客户端完全透明
- **401 自动重试**: 上游返回 401 时，自动刷新 token 并重试当前请求
- **JWT 离线校验**: 解码 JWT payload 中的 `exp` 字段，预留 60 秒安全余量提前刷新
- **启动时快速失败**: 学号密码模式启动时立即尝试登录，密码错误直接报错退出

### Internal

- 新增 `auth/cas_login.py`: CAS 登录流程（AES-128-CBC 密码加密、IDS 表单解析、重定向跟随）
- 新增 `auth/token_manager.py`: `TokenManager` 类（JWT/学号密码模式识别、线程安全刷新）
- `app.py`: `before_request` 改用 `token_manager.get_token()`，新增 `LoginError` 错误处理
- `provider/genai.py`: 401 响应时调用 `force_refresh()` 并重试

## v1.0.0

### Features

- OpenAI 兼容的 Chat Completion 代理（流式/非流式）
- 动态模型列表，自动从 GenAI 平台拉取
- Tool Calling 支持（通过 prompt 注入，兼容非原生模型）
- 流式 Tool Calling 解析
- Token 过期流式错误推送
- API Key 客户端认证
- 健康检查端点
