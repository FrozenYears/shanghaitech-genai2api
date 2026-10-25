# deepseek-pro 反代能力测试报告

## 2026-10-25：持续工作故障根因修复与复验

证据摘要：[relay-rootcause-2026-10-25.json](validation/relay-rootcause-2026-10-25.json)；架构与边界见 [阶段说明第八节](STAGE_NATIVE_FIRST.md#8-持续工作故障的根因与修复)。本节不覆盖或改写下方此前长时验收结果。

### 定位结论

1. **自然空回复来自学校网页上游。** 从保留失败会话的副本继续实际 omp 工作，原始网页 SSE 两次只出现空 assistant 起始帧和正常 stop，正文、思考、调用都为零；一例等待期间仍有 ping。它不是 relay 丢掉了完整答案。网页内部的 worker/缓存/排队原因不可由现有日志进一步确定，不能把它说成模型缺乏工具能力。
2. **第一跳的“空 stop=成功”与第二跳错误泛化是代理缺陷。** 旧第一跳会正常结束空回答，relay 才报 no answer；另将第一跳具体解析、Schema、网络错误统一覆盖为 stream error，丢掉 type/code。原长测 Kimi 两次流错误缺少当时原始 SSE，不能事后硬归因到某一项。
3. **生成代码错误与格式错误不是可任意修补的传输损坏。** 实际金额验收失败、edit 歧义由客户端正确拒绝。本轮还抓到模型输出已闭合但 JSON 语法错误的 action：第1行第724列少了正确对象边界，批次在执行前被拒绝。没有自动补括号、猜参数或放行部分批次。

取证先从历史重构请求时漏了 OMP 在线路上增加的必需 `i` 字段，导致了人工制造的 Schema 错误；这批结果被排除。后续使用真实 OMP 发出的 HTTP 请求及原始网页 SSE，不把不忠实重放当作故障证据。

对同一保留请求分别做原样、增加会话ID、预算30000、关闭思考的对照：正文分别为2484、456、2422、0字符，耗时波动很大。原样也能再次成功，关闭思考仍为空；样本不足以确认字段因果。因此没有加入随机会话ID、强制串行、预算夹取或关闭思考来“解决”问题。

### 已实现的修复

- 第一跳对工具协议请求只在**零正文、零思考、零调用、正常 stop**时，保留历史并追加一次明确继续指令。不是重复原问题或执行工具；此前没有新调用交付，且上限一次。第二次仍空返回 `upstream_empty_response`，不伪装成功。
- 只要已有思考/可见正文、坏参数、解析错误、断流或截断，就不会触发这条继续路径。Schema、白名单、整批原子交付与客户端权限保持不变。
- 第一跳三个协议统一识别空完成、保留错误代码；参数错误报告工具/字段/约束，不回显参数值。
- relay 的流式和非流式错误安全保留 message/type/code，限制错误体和字段长度、遮蔽凭据，不回传 headers、traceback 或完整错误体。非JSON/过大/过深错误体保持通用状态错误，失败不发成功终态。

错误分类包括 `upstream_empty_response`、`upstream_stream_incomplete`、`upstream_stream_error`、`tool_call_parse_error` 和 `tool_arguments_invalid`。结构化 SSE error 的处理参照 [OpenAI Python SDK 的流错误边界](https://github.com/openai/openai-python/blob/main/src/openai/_streaming.py)，未引入第三方代理或更换模型。

### 验证：分清自然复验、故障注入与业务验收

| 验证 | 实际结果 |
| --- | --- |
| 根回归 | 305 passed |
| relay 回归 | 89 passed，259 subtests passed |
| DeepSeek 失败历史副本续作任务10–12 | 三个用户轮次正常结束，真实工具执行24次；实际 Python/SQLite 累计50项断言通过 |
| Kimi 失败历史副本续作任务2–4 | 三个用户轮次正常结束；实际累计20项断言通过 |
| 真实空响应记录故障注入 + 后续真实 GenAI | 第一次源响应故意注入旧录制空帧；仅索引1触发继续，历史不变；后续4次请求连接真实网页，omp执行8次工具，预算阶段46项断言通过 |
| 正式端口重启后 | 两模型均经用户原有GenAI配置完成真实omp write/read，分别约3.891秒与18.969秒 |

进一步的 DeepSeek 续作取证中，**两次自然空 stop**（源请求15、17）分别由唯一的继续请求16、18恢复，生成2220、346字符；这不是故障注入。之后任务14出现真实坏 JSON，返回具体 `tool_call_parse_error` 信息并拒绝批次。该取证段虽有任务10–13的正常 agent 结束，最终独立52项业务检查仍发现预算功能返回 invalid_row，不算业务全通过；这是模型实现错误，不能被协议成功掩盖。

此次取证续作使用复制的旧失败历史和项目，未重跑此前完整半小时的20阶段验收，也不是同任务无变量的旧/新版 A/B。已证明边界修复和两次自然空完成恢复，**没有证明学校空输出已根治、模型不会再生成坏 JSON，或可以任意小时无人值守**。

正式服务已加载修复并保持运行：第一跳 `0.0.0.0:31100` 的 Python PID88780，relay `127.0.0.1:31110` 的 Python PID68920。仍使用学校网页登录反代，不使用独立官方 API。


## 2026-10-25：正式双跳重启后的 omp 持续开发验收

**结论：Kimi 在两次显式恢复后完成约半小时的累计开发；DeepSeek 在第 10 阶段停止，不能认定长时稳定。** 原始事件、实际文件和独立验收结果与短样本分开保存，精简证据为 [omp-endurance-2026-10-25.json](validation/omp-endurance-2026-10-25.json)。

### 服务与客户端身份

- 已按用户授权停止旧第一跳 PID54264、relay PID77140，在原端口加载重构代码：第一跳 Python PID103556，relay Python PID89612。实现基线为根 `a7b3186`、relay `44639ec`，分支均为 `feat/native-first-streaming`。
- 两端健康检查正常；第一跳非法工具 Schema、relay 不支持的 previous_response_id 均按新契约返回 400。验收结束后服务继续运行。第一跳沿用 `0.0.0.0:31100` 监听，relay 为 `127.0.0.1:31110`；“正式端口”不代表已迁移到生产 WSGI 服务。
- 使用实际 omp 18.12.6 RPC 进程，不是自写 HTTP agent 冒充 omp。启动与每轮前后都核对 provider/model/base URL：`GenAI/deepseek-pro`、`GenAI/Kimi-k3`，`openai-completions`，`http://127.0.0.1:31110/v1`。
- 路线为 omp → 正式 relay31110 → 第一跳31100 → 学校 GenAI 网页聊天端点。未使用学校独立官方 API，也未访问模型厂商官方 API。不能申请独立 Key，现行方案已放弃该路线。

### 方法

两个模型各自持有一个完整连续会话，启动时间相同，并发运行；DeepSeek 提前停止后 Kimi 继续。没有切模型、换供应商、清空历史或自动压缩。关闭 provider 自动重试、fallback、advisor、扩展、技能、用户规则和 MCP，仅提供 omp 内置 read/write/edit/glob/grep/bash。bash 仅允许调用指定的验收程序；这是工具策略，不是 OS 级沙箱。没有修改用户全局模型设置。

任务是有意带初始错误的多模块 Python 财务流水 CLI，逐阶段增加：十进制金额、分组、CSV、SQLite 持久化、事务回滚、筛选、原子转账、冲正、期初余额、预算、批合并、CSV 导出、对账、分页、旧库迁移、模块边界、月报、分类修改、账户账单及日现金流，共 20 阶段。每阶段运行全部已有契约；独立期望在模型工作区外，实际启动新 Python 进程和 SQLite 库验收，不接受模型自报。

每阶段最多两次**显式新用户反馈**，要求先检查实际文件再继续，不假定错误前工具未执行；不做请求级静默重放。每个用户轮次最多 600 秒、整个模型任务最多 45 分钟，任务完成或持续失败即结束，不 sleep 凑时长。固定 73,728 字节 fixture 不允许修改，最终哈希均保持 `d39bd84e70309e2ebfd1cb0d5dd3e9788f519845144beafa40a8330b4e960a79`。

### 实际结果

| 指标 | deepseek-pro | Kimi-k3 |
| --- | ---: | ---: |
| 实际墙钟时长 | 480.000 秒（8 分钟） | 1,903.828 秒（31 分 44 秒） |
| 累计阶段完成 | 9 / 20 | 20 / 20 |
| 用户轮次（含反馈） | 19 | 22 |
| 显式反馈继续次数 | 9 | 2 |
| 客户端记录的模型响应尝试（含错误） | 57 | 74 |
| 实际工具执行 | 73 | 91 |
| 工具错误（含验收失败、编辑歧义） | 2 | 1 |
| 用户轮次以模型/传输错误结束 | 10 | 2 |
| 最大客户端上下文估算 token | 46,341 | 56,969 |
| 生成 Python 源码字节 | 16,312 | 28,798 |

“响应尝试”来自 omp assistant 事件，不是供应商计费请求数；上下文与代理 usage 都是估算。完整用户轮次时间包含模型、工具和等待：DeepSeek 合计 415.252 秒，Kimi 合计 1,796.470 秒，其余为独立验收及运行器开销，不称为纯模型推理时间。

### 失败、恢复和结束条件

- **DeepSeek**：前 9 阶段累计 45 项断言通过；第 10 阶段的预算功能仍返回 unknown_operation，46 项累计断言中该项失败。记录 9 次 `First-hop returned no answer or tool call` 和 1 次 `First-hop service returned a stream error`。部分空回复发生在代码已经修改、独立验收已经通过之后，须新用户反馈才能正常结束该阶段；预算阶段初始及两次反馈均未完成，按预设边界终止。转账时一次实际验收发现 invalid_amount，后修正；一次 edit 不匹配也修正。
- **Kimi**：第 2、6 阶段各有一次 `First-hop service returned a stream error`，显式反馈后恢复，后续历史继续使用。第 14 阶段 edit 匹配到两处，工具明确拒绝后模型补充上下文修正。最终 71 项累计断言全部通过并正常结束，固定 fixture 不变。它证明这次有界任务可恢复完成，不证明全程零错误或任意小时无人值守。
- relay 的通用 stream error 没有给出底层序列化、校验或网络原因，本轮记录不足以把每次错误归因到某一层；空回复也只能确认第一跳交付没有可用正文/调用，不能据此断言模型底层能力不足。
- 本次客户端选择 off，但正式第一跳日志实际为 `thinking=true`；omp 兼容映射使用的 minimal 被代理折叠为开启。这不是思考开/关对照实验。

### 复现、材料与限制

运行器与阶段契约位于 [validation/native-first-endurance](validation/native-first-endurance/)。正式服务及用户现有 `GenAI` 模型配置就绪后，可运行：

```bash
pixi run python -B -u validation/native-first-endurance/run.py run --model deepseek-pro
pixi run python -B -u validation/native-first-endurance/run.py run --model Kimi-k3
```

运行器会拒绝不匹配的 provider/model/relay base，禁止其他模型兜底。每次生成独立 `.validation-runtime/omp-*` 目录，不自动删除旧材料。用户选择保留本次两个正式验收目录，删除试点和中止的 Claude Code 产物：

- `.validation-runtime/omp-deepseek-pro-heo6olng`
- `.validation-runtime/omp-kimi-k3-4gnve5pv`

Claude Code 初始试点被 Windows 8.3 短路径权限检查拦截；后续 Claude Code 任务因改用 omp 而中止，不计入成功统计。omp 写/读试点同样独立：Kimi 通过，DeepSeek 写入后空回复，不能用这些短样本充当长测。

本轮没有做同客户端/同任务的旧版 A/B，不能把与旧报告的差异全部归因于重构。也未重新覆盖图片、文档、搜索、托管工具或所有协议组合。**当前更合理的使用判断是：Kimi 可在有监控与恢复机制下尝试长工作；DeepSeek 的空回复和流错误仍是无人值守阻碍。**


## 2026-10-25：原生优先与流式重构验收

分支：根仓库和 relay 均为 `feat/native-first-streaming`。使用新代码在临时 loopback 端口启动真实 HTTP 服务，连接实际 GenAI 网页模型；原 31100/31110 未重启。下面的“native relay”指 relay 与第一跳之间的结构化工具协议，不代表网页端点已接受原生工具定义。

### 回归与实际工作流

| 范围 | 验收结果 |
| --- | --- |
| 根项目 `pixi run test -q -p no:cacheprovider` | 275 passed |
| relay，使用根项目 pixi Python 执行 pytest | 79 passed，164 subtests passed |
| native relay：DeepSeek / Kimi × Chat / Messages / Responses | 6 条实际文件工作流通过 |
| 第一跳直连：DeepSeek Chat，两模型 Messages / Responses | 5 条实际文件工作流通过 |
| 显式 text relay：DeepSeek Chat | 1 条实际文件工作流通过 |
| custom text + namespace | 第一跳 Kimi、relay DeepSeek 均完成真实写入与结果回传后的正常结束 |

每条文件工作流均实际执行 write → edit → read → run_python，5 轮请求后结束；共 48 次工具执行。`demo.py` 保留中文、`<think>`、`</think>` 和 `@@ACTION@@` 字面注释，局部 edit 后由真实 Python 进程验证 `add(2, 3) == 6`，最终磁盘字节均匹配。统一 SHA-256：`6782575cf888073589a1b3ddc539d4a09c5b4d127b0a9b41709fe5a6a3f96810`。

两个 custom 场景分别写入 62 字节，验证 `namespace=workspace`、`name=write_note`、带控制标记和 Unicode 的 input 原文、call_id 关联的 custom_tool_call_output。实际写入并回读后，下一轮 `tool_choice=none` 正常结束，无额外调用。

### 流式与取消

- 修复后 11 条文件工作流的首个非空 SSE 行在 0–32 ms 内收到；这是协议首事件，不是模型首 token，且受本机时钟分辨率影响，不是延迟承诺。
- 真实慢 HTTP 源的第一段正文约 16 ms 到达，第二段约 219 ms，终态约 406 ms；证明 relay 不是完成后重放正文。
- 真实 socket 验收收到正文后进入空闲，桥接仍发出 heartbeat；取消后服务端观察到 EOF，`genai-stream` 线程退出。响应头到达前的取消仍受 requests 返回/超时限制。
- 33 KB 参数逐字符 SSE 分片、原始 wire 超过 1 MiB 的边界由确定性回归覆盖：重复协议元数据不消耗参数预算。未在本轮重新执行旧报告的 33 KB 模型全文复制或 12 阶段耐力测试。

### 失败与边界

首次真实验收中 7 条 native 路径失败，均未交付工具；根因是完整 action 封套末尾的 `@@` 被旧前缀检查误判为截断。删除重复的截断判断、由严格完整解析器负责终态判定后，上述 11 条新工作流全部通过；显式 text 路径在首次验收已通过。另修复了错误流仍追加 `[DONE]`、普通末尾 `@` / `<` 被判截断，以及 text relay 未闭合 `<think>` 内封套被误识别为调用的问题。

本轮未获得学校独立 API Key，未实测 `genaiapi.shanghaitech.edu.cn` 的原生工具接口。公开 OpenClaw 指南证明存在独立授权 API 路径，但不能将网页模型成功推导为该 API 所有工具、签名或约束均支持。custom grammar 明确拒绝；custom text 已实际验证。学校网页的文件/图片/搜索组合没有在本轮重新全面验收。

用户选择删除本轮 3 个临时测试目录及 3 个新模块字节码缓存；保留本报告的结果、哈希、失败原因及边界。先前用户保留的长文件/耐力测试产物不在清理范围。


## 长文件、持续开发与多类型工具补充验证

证据摘要：[validation/kimi-endurance-2026-10-24.json](validation/kimi-endurance-2026-10-24.json)。全部使用真实模型和本地实际执行器；不将 HTTP 200、模型声称完成或部分文件正确作为通过标准。

### 首次复制错误与原生工具遗漏是不同问题

上次 Kimi 错误的原始输出已复核：第 043 行的散列字符串在上游参数里就被复制错误，8,389 字节变成 8,390 字节。它不是代理少写字节，不能靠 JSON Schema 消除语义复制错误。现有文件优先局部 edit，写后用完整哈希或实际程序校验，而不是让模型反复重抄全文。

本次又抓到实际协议故障：Kimi 在 Anthropic 请求中输出 `<|open|>tools<|sep|>` / typed `argument`，第一跳把它当普通文本、以 `end_turn` 结束，没有执行工具。新增 `tools/kimi.py` 严格解析完整 K3 XTML，三个输出适配器共用；原始 string 不做 JSON 二次解码，array/object/number/boolean/null 按声明类型处理。未知工具、重复参数、坏类型、截断、第二个调用损坏时整个批次失败，不补齐执行。真实模型还返回了 tools 后的 response/message 结束标记，已覆盖。

协议参考采用 [Xinference Kimi K3 parser](https://github.com/xorbitsai/inference/blob/main/xinference/model/llm/tool_parsers/kimi_k3_tool_parser.py) 和 [MLX K3 parser](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/tool_parsers/kimi_k3.py) 的实际格式证据，未安装新依赖。原生语法不再只能依靠提示避免。

### 长文件与长会话

| 场景 | Kimi-k3 | deepseek-pro |
|---|---|---|
| 33,803 字节全文复制、edit、read | 首次与最终字节均匹配；4 请求；首次写参数约 445.95 秒 | 首次与最终字节均匹配；5 请求；首次约 57.42 秒 |
| 1,039 行、实际初始 82,550 字节源码，累计 12 个增量功能 | 50 请求、45 次工具；12 阶段实际 Python 累计断言通过；固定注释字节不变 | 55 请求、45 次工具；12 阶段断言通过；固定注释字节不变 |
| 3,202 行源码局部修改并验证全文件哈希 | LF 316,877 字节和 CRLF 320,079 字节均匹配；各 3 请求，约 8 秒 | CRLF 文件匹配；4 请求，约 86 秒 |
| 持续历史中的工具切换 | 同一会话扩展至 86 请求、79 次工具，约 795,208 序列化历史字符；原 12 功能仍通过 | 独立多工具会话 19 请求、19 次工具，全部场景结束 |

Kimi 的 12 阶段开发快照跨度约 23.6 分钟，其中模型请求累计约 14.6 分钟；包含期间穿插其他顺序场景的间隔，不能称为 23.6 分钟连续模型推理。DeepSeek 第 9 阶段虽已通过验证仍继续优化，达到该阶段 9 请求上限，由下一条新任务推进；不能声称始终自然收敛。Kimi 在一次 edit 文本不匹配后恢复，两模型都确实运行只读 verifier，而不是只报告成功。

### 不止文件工具

覆盖 `write`、`edit`、`read`、`run_python`、`glob`、`grep`、`command`、`sql_query`、`http_get`、`start_job`、`job_status` 共 11 类。实测包括无匹配 grep（正常 exit 1）、命令 exit 2 后修正、SQLite 参数绑定错误后重查（A 收入 250，总收入 750）、HTTP 404 后正确请求、后台启动与终态区分，以及单封套三个独立调用和结果 ID 回传。两个模型均完成这些场景。长会话第一次缺失 fixture 的错误和一次 LF/CRLF verifier 基线错误是测试装置问题，已如实保留，补齐或修正后另行验证，不算模型成功样本。

用户批准后，原 31100/31110 已重启加载修复。两个模型分别在 relay Chat、第一跳 Anthropic Messages、第一跳 Responses 的六条原端口工作流都实际执行 5–6 次工具（共 32 次）并正常结束；接受标准包含实际命令失败／恢复、SQL、glob、HTTP 的返回结果，不是 HTTP 状态。单独生成的真实 K3 XTML command 经新解析器后真实执行，exit 0。根项目回归 131 passed；relay 39 passed、42 subtests passed。

### 使用边界

- 现有长文件优先小 edit，不重建固定数据或全文；首次复制准确不能作为模型承诺。
- 低延迟、长输出和全自动收敛不等价，本轮样本不证明任意无人值守小时级任务稳定。
- 代码改动必须运行实际检查；数据文件用独立字节哈希。read 看起来正确不足以证明所有长行都一致。
- 保留完整历史；后台 running 状态不等于完成；失败退出不能被客户端执行器包装成成功。

## 2026-10-24：DeepSeek / Kimi 真实工具闭环复测

本节是当前版本复测；下文原报告的 60 秒超时、工具选择及能力结论保留为历史记录，不代表当前实现。

- 平台 ID：`deepseek-pro`、`Kimi-k3`，均为 `owned_by=xinference`。平台元数据显示 DeepSeek 不提供思考开关，Kimi 提供；本轮不覆盖思考参数，使用上游默认值。
- 独立启动当前代理与 relay，不重启原服务；真实 HTTP 请求、真实模型输出、隔离目录实际 write/edit/read，不模拟模型。顺序执行，避免并发干扰。
- Chat Completions 长内容流式样本：8,389 字节、80 行数据，混合 Unicode、反斜杠、引号、尾部换行及 think/tool/DSML/action 字面标记；`max_tokens=16000`、`temperature=0`、`tool_choice=auto`。落盘独立按完整字节比较，不能仅凭工具成功回执判断通过。

| 模型 | 链路 | 首次 write 参数准确 | 最终磁盘及回读准确 | 正常结束轮数 | 总请求耗时（秒） |
|---|---|---|---|---|---|
| deepseek-pro | 第一跳直连 | 是 | 是 | 4 | 13.93 |
| deepseek-pro | 经 relay | 是 | 是 | 5 | 19.99 |
| Kimi-k3 | 第一跳直连 | 是 | 是 | 4 | 181.26 |
| Kimi-k3 | 经 relay | 否，回读后 edit 修正 | 是 | 6 | 170.79 |

Kimi 的首份文件为 8,390 字节，一行数据复制错误；模型经 read 发现差异，edit 后再次 read 正确，不能把这次结果说成首次参数完全保真。DeepSeek relay 多执行了一次未改动文件的 read，最终正常结束。耗时仅为本轮观测，不是基准或稳定性保证。

两模型各自完成了 `max_tokens=64` 截断边界验证：直连流发送 error、relay 返回 502，均没有交付或执行部分调用。另在 edit 前实际把文件版本改为 0，造成 old_text 匹配失败；两模型都读取当前文件、修复并核验后正常结束，没有把失败回执报告为已完成。

实测修复：先保留原始封套／Markdown 引用边界，再处理 think/DSML；流式检测保留未闭合引用，引用中的标记不触发调用；Anthropic 不删除代码引用中的 think 字面数据；relay 原生语法检查排除引用和合法 JSON 参数；明确结果封套是已执行回执及核验后的退出条件。

完整测量摘要与样本 SHA-256：[validation/local-model-tools-2026-10-24.json](validation/local-model-tools-2026-10-24.json)。本轮未重测 512k 上下文、所有工具／协议组合，不据此声称模型不会再出现内容复制错误。

## 历史报告


- 测试对象：`http://127.0.0.1:31100`（本项目反代）→ 上游 GenAI 平台 `deepseek-pro`（root=xinference）
- 测试时间：2026（本次会话）
- 测试方式：三条接口（OpenAI Chat Completions / Anthropic Messages / OpenAI Responses）直接发 HTTP 请求；上下文用仓库自带 `context-probe --find` 二分 + 经反代复测
- 说明：首轮测试与 51 万 token 的上下文二分**并发**执行，个别空响应是并发干扰造成的假阴性；所有可疑项都在隔离环境复测过，下表以隔离复测为准。

## 一、结论摘要

| 维度 | 结论 | 评级 |
|---|---|---|
| 上下文窗口 | 模型真实上限 **524,288 token（512k）**，可用约 520k。但反代硬编码 `timeout=60`，超大 prompt 首 token 超过 60s 时**间歇性失败** | ⚠️ 有阻塞性缺陷 |
| 思维链（CoT） | 三条接口都正确分离透传：Chat `reasoning_content`、Anthropic thinking 块、Responses reasoning 事件；流式顺序正确；`<think>` 不泄漏正文 | ✅ 正常 |
| Tool call | 单工具 / 流式 / 并行 / 强制调用 / 多轮回喂 / Anthropic 往返 / Responses 往返**全部正常**；`tool_choice` 指定单个函数**不生效** | ✅ 主体正常，1 项缺陷 |
| Skill（API 模拟） | 工具优先、强制输出格式、元信息记忆、长文档标记召回、两步流程**全部正常** | ✅ 正常 |
| reasoning effort 粒度 | **不存在**：`reasoning_effort`、`thinking.budget_tokens`、`reasoning.effort` 全部被代理忽略；`max_tokens` 也随之无效（上游不支持） | ❌ 无此能力 |
| 稳定性 | 隔离后连续 8 次简单请求 0 空响应 | ✅ 正常 |

**一句话**：deepseek-pro 经反代能力可用，CoT / Tool / Skill 都过关；主要问题集中在**大上下文的 60 秒超时**、**输出长度与截断信号完全不可控**、**`tool_choice` 与 reasoning effort 参数形同虚设**。

## 二、测试环境与方法

- 代理进程已在 `31100` 运行，`API_FORMAT=both`，未开 `--debug`。
- 模型列表确认 `deepseek-pro` 存在（`owned_by=xinference`）。
- 测试脚本（临时件，未入仓）：
  - `/tmp/dspro/harness.py`：26 项功能测试（CoT / Tool / Skill / effort）
  - `/tmp/dspro/repro.py`：可疑失败项隔离复现 + 直连上游验证
  - `/tmp/dspro/final.py`、`/tmp/dspro/ctx_proxy2.py`：经反代的大上下文验证
  - `/tmp/dspro/skill2.py`：Skill 两步流程完整复测
  - 结果原始数据：`/tmp/dspro/results.json`

## 三、分维度结果

### 1. 上下文窗口

**直连上游二分（`pixi run context-probe --model deepseek-pro --find`）**

```
 ~  131,072 tok | actual=131149 | PASS
 ~  262,144 tok | actual=262223 | PASS
 ~  393,216 tok | actual=393281 | PASS
 ~  491,520 tok | actual=491594 | PASS
 ~  507,904 tok | actual=507972 | PASS
 ~  516,096 tok | actual=516169 | PASS
 ~  520,192 tok | actual=520266 | PASS
 ~  524,288 tok | OVER_LIMIT  exact limit=524288
 -> usable up to ~520,256 tokens, fails at ~524,288 tokens
```

**经反代的复测（`/v1/chat/completions`，非流式/流式）**

| 目标 token | 结果 | 耗时 |
|---|---|---|
| ~400,000 | 首次 60s 失败（错误文本当正文返回），另一次 4s 成功召回 ZEBRA | 60s / 4s |
| ~450,000 | 成功召回 ZEBRA | 17s |
| ~500,000 | 首次 60s 失败，重试 9s 成功召回 ZEBRA | 60s / 9s |

**根因**：`provider/genai.py:113` 与 `:122` 把上游请求超时写死为 `timeout=60`：

```python
response = requests.post(GENAI_URL, headers=headers, json=genai_data, stream=True, timeout=60)
```

超大 prompt 上游首 token 可能超过 60s（冷请求尤其明显），此时抛出 `ReadTimeout`，被 `iter_genai_stream` 捕获后转成错误事件：

- **流式 Chat**：错误被当作普通正文增量返回——客户端看到的是
  ```
  "[Error] HTTPSConnectionPool(host='genai.shanghaitech.edu.cn'...Read timed out"
  ```
  （`errors.py:23` 的 `make_error_chunk` 把错误塞进 `delta.content`，`finish_reason="error"`）
- **非流式 Chat**：`api/chat.py:88-99` 用 `json.loads(line[6:])` 解析，错误 chunks 是 `data: {...}\n\ndata: [DONE]` 两行拼接，`json.loads` 抛 `JSONDecodeError` 被 `pass` 掉，最终返回 **HTTP 200 + 空 content + finish_reason="stop"**，错误完全消失。
- Anthropic / Responses 路径把错误作为独立 `error` 事件返回，表现正常。

**建议**：把 `timeout=60` 改为可配置的长超时（例如 300s，与 `context_probe` 一致），或拆成 `(connect, read)` 元组；同时非流式 Chat 收集时应把错误 chunk 显式识别出来并以错误响应返回，而不是解析失败就丢弃。

### 2. 思维链（CoT）

全部通过，原始证据：

- 非流式 Chat：`reasoning_content` 与 `content` 分离，`reasoning_len=218 / content_len=347`，答案正确（鸡 23、兔 12）。
- 流式 Chat：`first_reason_idx=0 < first_content_idx=36`，思维链严格先于正文，`finish=stop`。
- `<think>` 过滤：要求把推理写进 `<think>` 时，正文只返回 `'391'`，标签未泄漏（`reasoning_len=163`）。
- Anthropic：`content` 块为 `['thinking','text']`，`signature=genai-compat-no-signature`（占位签名，符合 README 声明）。
- Responses：同时收到 `response.reasoning_summary_text.delta`（4 段，381 字符）与 `response.output_text.delta`，并有 `response.completed`。

### 3. Tool call

| 用例 | 结果 |
|---|---|
| Chat 非流式单工具 | ✅ `get_weather(location=Shanghai, unit=celsius)`，`finish_reason=tool_calls` |
| Chat 流式单工具 | ✅ `calculate(expression="42*58")`，参数 JSON 合法，且带 reasoning |
| 一轮并行多工具 | ✅ `['get_weather','calculate']` 两个调用 |
| `tool_choice="required"` | ✅ 强制调用 |
| **`tool_choice` 指定 `calculate`** | ❌ 3/3 都返回 `['get_weather','calculate']`，并未限制为指定函数 |
| 未给工具时 | ✅ 不产生 `tool_calls` |
| 多轮 `tool_result` 回喂 | ✅ 回喂 `83810205` 后给出最终答案 |
| Anthropic `tool_use` + `tool_result` 往返 | ✅ `stop_reason=tool_use` → 回喂后 `end_turn`，输入为 JSON 对象 |
| Responses `function_call` + `function_call_output` 往返 | ✅ `function_call` 事件正确，回喂 `42` 后输出 `7 × 6 = 42` |

**`tool_choice` 指定函数的缺陷**：`tools/prompts.py:124-128` 只是在 system prompt 里追加一句
`You MUST call the tool named "calculate" in your response.`，并没有在解析/输出层过滤其它工具。deepseek-pro 会调用它认为相关的**所有**工具（天气问题甚至凭空调了 `calculate(1+1)`）。属于"软提示"，客户端不能依赖它做严格的工具约束。

### 4. Skill（API 模拟）

按用户要求不启动 Claude Code，用 system prompt 注入 SKILL 文档 + 工具定义来模拟：

| 用例 | 结果 |
|---|---|
| 技能要求"先调用 calculate 再回答" | ✅ 调用 `calculate({"expression":"12345*6789"})` |
| 技能强制输出格式 `RESULT=<值>` | ✅ 回喂结果后最终输出精确为 `RESULT=83810205` |
| 技能元信息记忆（`SKILL-V9`） | ✅ 回答 `SKILL-V9` |
| 4.9k 字符技能文档，开头埋 `SKILL-MARKER-ZEBRA-42` | ✅ 原样召回，长 system prompt 未被截断/丢失 |
| 两步流程：`list_files` → 按结果 `read_file("report.txt")` → 总结 | ✅ 三步全部按技能执行，最终正确总结 |
| 财务合规技能"必须走 `format_money`" | ✅ 3/3 调用 `format_money({"amount":720,"currency":"CNY"})` |

### 5. reasoning effort 粒度

**结论：没有这个能力。** `reasoning_effort` 在项目 Python 代码里**零出现**（`grep -rn reasoning_effort` 无结果）。三个接口相关参数都只是被忽略或回显：

- Chat：`reasoning_effort` 不读、不传。
- Anthropic：请求里的 `thinking`（含 `budget_tokens`）不读；`thinking` 块只用于输出。
- Responses：`api/responses.py:106` 把 `body["reasoning"]` 存进 `response_meta` **仅用于回显**，不参与上游请求。

隔离复测的思维链长度（同一题目）：

```
reasoning_effort=minimal: 149, 140
reasoning_effort=high:    162, 137      # 与 minimal 区间重叠，非单调 → 纯采样噪声
Anthropic budget_tokens:  1024→119, 4096→208, 16384→129   # 非单调
Responses reasoning.effort: low→162, high→111              # high 反而更短
```

**输出长度控制同样失效**：`max_tokens` 会透传成上游 `maxToken`（`provider/genai.py:100`），但上游不执行：

- 直连上游 `maxToken=64`，仍产出 `content 2339 字符 + reasoning 222 字符`，`totalTokens≈2831`；`maxToken=256/2048` 结果相近。
- 经反代 `max_tokens=64/256/1024` 均产出 2000~4000 字符长文。

**截断与计费信号不可用**：

- Chat 的 `finish_reason` 无论上游是否截断都写死为 `"stop"`（`provider/genai.py:242`、`api/chat.py:118-120`），客户端**无法判断回答是否被截断**。（Responses 路径反而会检查 `finish_reason=="length"` 并回 `response.incomplete`，两者行为不一致。）
- `usage.prompt_tokens` 恒为 `0`（`api/chat.py:140`；Anthropic 非流式 `api/messages.py:179` 的 `input_tokens` 也是 0），只有 `completion_tokens` 是本地估算；Responses 的 `input_tokens` 是估算值。客户端无法据此做上下文裁剪或计费。
- Anthropic `count_tokens` 是 `总字符数/4` 的粗略估算（`api/messages.py:215`），与实测 token 数（每英文单词≈1 token）偏差较大。

## 四、问题清单（按优先级）

1. **【高】大上下文 60s 超时**：`provider/genai.py:113,122` 硬编码 `timeout=60`，导致 40 万~52 万 token 的请求间歇失败；流式把错误当正文、非流式把错误吞成空 200。
2. **【高】Chat 非流式错误被静默丢弃**：`api/chat.py:88-99` 解析错误 chunk 失败即 `pass`，用户拿到空回答却无任何错误提示。
3. **【中】输出长度不可控**：`maxToken` 上游不执行；`finish_reason` 恒为 `stop`；`usage.prompt_tokens=0`。客户端无法限长、无法感知截断、无法统计输入。
4. **【中】`tool_choice` 指定函数不生效**：仅为 prompt 软提示，未做输出层约束（`tools/prompts.py:124-128`）。
5. **【低】reasoning effort 无粒度**：`reasoning_effort` / `thinking.budget_tokens` / `reasoning.effort` 全部无效。若平台本身不支持，则应在文档中明确"该参数不生效"，而不是让客户端误以为可调。

## 五、复现命令

```bash
# 上下文精确上限（直连上游，耗时较长、消耗额度）
pixi run context-probe --model deepseek-pro --find

# 功能测试（临时脚本）
pixi run python -u /tmp/dspro/harness.py      # CoT / Tool / Skill / effort
pixi run python -u /tmp/dspro/repro.py       # 可疑项隔离复现 + 直连上游 maxToken 验证
pixi run python -u /tmp/dspro/skill2.py      # Skill 两步流程
pixi run python -u /tmp/dspro/ctx_proxy2.py  # 经反代的大上下文（抓 60s 超时证据）
```

---

## 六、修复记录与复测（补充）

针对上面三个高/中优先级问题做了修复，并在独立端口 31200 起新实例端到端复测。

### 修复内容

| 问题 | 改动 |
|---|---|
| 60s 硬超时 | `provider/genai.py` 新增 `UPSTREAM_TIMEOUT = 300`，替换两处 `timeout=60` |
| 非流式错误被静默丢弃 | 新增 `collect_genai_response()` 直接收集事件，出错抛 `UpstreamError`；`api/chat.py` 捕获后返回 HTTP 502 |
| 流式 Chat 错误当正文 | `errors.make_error_chunk` 改为标准 `{"error": {...}}` 事件，不再塞进 `delta.content` |
| `max_tokens` 无效 | `iter_genai_stream` 按估算 token 本地截断，命中上限返回 `finish_reason="length"`；Anthropic 映射为 `stop_reason="max_tokens"` |
| `prompt_tokens` 恒 0 | 新增 `estimate_messages_tokens()`，Chat `usage.prompt_tokens` 与 Anthropic `input_tokens` 改为估算值 |
| `tool_choice` 指定函数无效 | 三个接口在 `tool_choice` 指定函数时收窄 `allowed_tool_names`，输出层过滤其它工具；prompt 追加 "MUST NOT call any other tool" |
| reasoning effort 无粒度 | 上游确实不支持。三个接口收到相关参数时记录 warning，README 明确声明并说明被忽略 |

### 复测结果（31200 实例）

```
非流式无效模型      -> HTTP 502 {"error": {"type": "upstream_error", "message": "Upstream error: 未找到对应节点信息..."}}
流式无效模型        -> data: {"error": {...}} 然后 [DONE]
max_tokens=256      -> finish=length, content_len=0, reasoning_len=269（思维链计入预算）
max_tokens=4096     -> finish=stop, content_len=2969
usage.prompt_tokens -> 21 / 14 / 250025 / 500025（不再恒 0）
Anthropic           -> stop_reason=max_tokens, input_tokens=21
Responses           -> max_output_tokens=256 时 response.incomplete
Chat tool_choice    -> 3/3 只返回 calculate
Anthropic tool_choice -> 2/2 只返回 calculate
Responses tool_choice -> 只返回 calculate
500k token 上下文   -> 84s 成功召回 ZEBRA（旧代码 60s 超时会失败）
reasoning 参数      -> 三个接口各打出一条 warning
```

### 注意的行为变化

- **小 `max_tokens` 可能只返回思维链**：本地预算包含思维链 token，对推理模型给 `max_tokens=64` 之类的极小值时，思维链会用完预算导致正文为空（`finish_reason=length`）。这与 OpenAI / Anthropic 推理模型把思考计入输出预算的语义一致。
- **流式错误事件格式变更**：Chat 的流式错误从 `delta.content` 文本变成 `{"error": {...}}` 对象，依赖旧格式的客户端需要适配。
