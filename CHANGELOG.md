# Changelog

## Unreleased

### 即时进度与未交付工具纠错（2026-10-26）

- 在回退后的新分支分开处理正文与工具交付：正文/思考即时发送，正常终态的未交付坏工具批次仍可在原预算内纠错。已显示内容不撤回，不承诺隐藏失败尝试或消除模型重复说明；工具仍原子校验、不重复执行历史操作。
- 586项回归通过；真实HTTP受控流确认正文/思考先于上游完成，之后缺参批次纠正为一个合法调用，首代工具没有交付。

### 回退整代暂存，恢复实时输出（2026-10-26）

- 按用户要求将功能代码及对应测试恢复至`99d12cf`之前的`05a83b7`状态，撤销正文/思考整代缓冲。通过新提交回退main，不重置或强推历史。
- 保留已发布的20阶段实测及机器摘要，但明确标为整代暂存版历史结果，不能据此证明后续实时流式实现。工具批次严格校验、8/3/900策略不变。

### Kimi 正式实测与 main 发布（2026-10-26）

- 将已验证的工具纠错修复与今日脱敏实测结果发布到main，两仓库README同步整代暂存的交付时机及首个内容延后的代价；保留完整10-25旧表，新增10-26 Kimi列。
- 正式31100/31110加载第一跳`99d12cf`后，真实omp/Kimi完成20/20阶段、71项独立验收；2180.765秒、122次工具、90次模型请求，额外反馈与人工介入均为0，客户端未收到未恢复协议错误。
- 阶段15一次SQLite迁移业务验收失败在同回合修正；共享服务并发流量使第一跳恢复次数无法单独归因，不记为0或用服务窗口计数冒充本次数据。原始会话、SQLite与凭据不入Git，公开结果见`validation/kimi-formal-2026-10-26.json`。
- 最后通过真实`OpenTechGemini/gemini-3.8-flash`校对两份完整README，核对前后模型身份，仅采纳不改变含义的自然措辞；代码、链接、标题、表格与数据保持不变。

### Kimi 工具纠错与整代交付

- 修复坏调用前已发思考或少量正文导致同请求纠错失效的问题。工具纠错开启时暂存本代正文、思考与调用，完整校验成功后交付；失败代不泄漏给客户端。
- 工具提示明确要求全部必填字段，包括元数据字段，并要求封套结束标记后只有空白；保留严格JSON、Schema和整批校验，不猜参数、不修补坏封套。
- 起始事件与心跳仍即时发送；默认工具请求的正文显示会延后。无工具或关闭工具纠错时保留实时正文。原有3次纠错、8次空继续及900秒累计窗口不变，网络/断流/截断/取消/到期仍不重放。
- 真实双跳和已安装omp验证三类注入错误后由学校Kimi-k3继续完成实际写/改/读回；另有无注入的依赖工具任务通过，两组均无监督器或额外用户反馈。注入不记为自然模型成功，详细结果见架构文档第12节。
- 第一跳全量587项回归通过，覆盖三协议流/非流的前缀后纠错，以及零预算、取消、总时限、断流和截断边界；临时环境按用户选择清理，核心证据保留。

### README 分组目录

- 两份 main README 前部增加可点击的分组目录，将部署与客户端接入、功能与设计、实测与资料分开。
- 两跳流程和职责说明移入设计部分；保留现有环境变量、安装命令、完整实测表格、Windows 直显与客户端/附件折叠状态。
- 交由`OpenTechGemini/gemini-3.8-flash`校对，筛除生硬或会改变含义的建议；目录、标题、配置、代码和实测数据保持不变。

### main 发布入口与 Windows 命令

- 两份 README 的 Windows 安装/启动命令直接显示，不再保留 Windows 折叠标签；客户端和附件详情仍可折叠。
- 安装命令、当前文档与跨仓库链接统一指向 FrozenYears 两仓库的 `main`，删除 README 中尚未合并的状态文字；历史冻结提交不改写。
- 已按用户要求停止本机 GenAI 第一跳与 relay。Windows 反代进程和端口均已退出，运行中的 WSL 未发现相关服务；停止前记录的79个其他监听项仍全部存在。

### README 内容与两跳分工

- 恢复完整模型实测表格；流程图标明两跳及仓库名，启动标题注明对应仓库，改掉不直白的接口表述。
- Windows、客户端配置详情、图片/文档/搜索/思考及已知限制放回主README；客户端和示例默认折叠。`docs/USAGE.md`只保留可选omp监督器及主文档导航，避免维护重复说明。
- relay README补充用途、请求方向、职责表和直连第一跳的适用情况，说明它不自带模型、不登录学校、不执行本地工具。
- 交由`OpenTechGemini/gemini-3.8-flash`校对语言，只采用自然且不改变含义的建议；保留完整实测表、代码、密钥示例、折叠结构和失败边界。

### README 阅读结构

- 两份 README 改用短段落、操作步骤、小表格及少量 GitHub callout；保留安装、完整 `.env` 示例、密钥对应、客户端设置和模型实测摘要。
- 客户端高级用法、附件格式与完整协议参数分别收录到 `docs/USAGE.md` 和 relay 的 `docs/CONFIGURATION.md`；README 保留直接入口，不靠删掉必要配置缩短篇幅。
- 增加网页模型工具调用的四步说明：声明工具、提示生成、严格校验转换、客户端执行并回传结果；明确代理不执行工具，格式遵循仍取决于模型。
- 再次交由`OpenTechGemini/gemini-3.8-flash`做仅缩短、不扩写的润色；保留中文、代码和配置，复核调用身份与技术措辞。详细参数下沉后，关键入门信息仍可直接在README中查阅。

### README 与文档归档

- 按当前双跳实现整理两个 README，补充启动、三处认证、客户端接入、流式工具校验、请求恢复与可选任务监督，以及原作者/重构前版本对比。第一轮保留原有语言风格；随后按用户追加要求，将 relay README 改写为中文。
- README 简要记录 DeepSeek、Kimi 的真实20阶段验收结果、恢复过程与失败边界，明确是已有记录，不将本轮文档整理当作重新测试。
- 七份根目录架构、协议、验证、测速和工作日志文档移到`docs/`同名文件，增加文档导航与辅助工具参数说明，迁移相对和跨仓库当前链接；固定提交的历史链接及`validation/`材料保留原位。README、CHANGELOG、LICENSE与协作规则继续留在根目录。
- 最后通过真实 omp 子会话调用`OpenTechGemini/gemini-3.8-flash`，核对前后模型身份，完成根 README 润色与 relay 中文改写；代码块、内联代码、命令、链接和数值逐项核对，再校正技术措辞。

### 架构基线与设计差异说明

- 架构第五节分别对照两跳原作者`main`、个人fork的`main`、本地重构前冻结点与当前分支，明确已有能力、先行修复、native/SSE重构及自动监督的归属；源码证据使用固定提交链接，不以移动分支名替代版本。
- 补充协议/流式/校验/历史/恢复/附件/状态边界与迁移代价；“接近官方API”仅指已验证场景且配合监督器的连续工作体验，不宣称原生输入、完整供应商能力或同等级可靠性。本次仅文档更新，不改变服务配置或重跑历史模型长测。

### 未交付工具纠错与自动 agent 监督

- 第一跳新增 `GENAI_TOOL_REPAIR_ATTEMPTS` / `--tool-repair-attempts`，默认额外3次（0..10）：仅正常结束且未交付正文、思考或调用的坏协议/JSON/Schema批次可由模型重生成；不修参数、不放行部分批次。真实原生 `tool_calls` 的正常终态错误与文本协议共用校验和纠错。
- 空继续默认提高到8次，上限30；工具请求共享生成窗口提高到900秒。空继续与纠错分别累计、彼此不重置，默认最多12代。relay native不叠加重试；已交付内容、断流、网络错误、截断、取消或到期不重放。
- 新增可复用 `supervisor.py` 与通用 `supervise.py`，使用真实omp和操作者指定的独立验收，在未完成、业务错误或停滞后自动反馈；默认额外8反馈、整场45分钟、单轮1200秒。原20阶段runner接入同一循环，`--repairs` 移除，改为 `--max-feedback`。
- 单HTTP预算耗尽可由监督器自动接续；任务预算耗尽、权限/身份故障或无法确认会话停稳时保存会话和检查点、明确失败，不无限循环、不伪造成功。验收命令固定argv、无shell、有界输出及超时；默认agent无bash，显式授权模式也不是OS沙箱。不实现跨进程自动恢复。
- 根562项回归、relay89项及259个子测试通过。真实socket故障注入覆盖两跳三协议流/非流12条恢复路径：空→缺参→坏JSON→合法批次只交付一次；已输出/思考/截断不重放，耗尽保留具体错误，持续ping仍约0.235秒触发0.2秒deadline。故障注入不计为自然模型成功。
- 正式31100/31110重启后使用用户已配置GenAI的真实omp完成同一20阶段：DeepSeek595秒、150工具、无需额外agent反馈，第一跳自然8次空继续+4次纠错；Kimi1825.531秒、110工具、4次自动反馈恢复两次协议错误及SQLite计划停滞。两者最终71累计断言均通过、无人工逐次纠错；更高预算未自然用满，不将此当作无限成功或受控模型排名。

### 历史：可配置空继续预算与共享生成时限

- 第一跳工具协议请求的空继续升级为可配置额外次数：`GENAI_EMPTY_CONTINUATIONS` / `--empty-continuations`，默认3、范围0..10，首次加继续最多4次；0禁用额外继续。每次使用原问题加真实次数标记，保留完整原历史，不积累空assistant、不重放客户端操作。
- 增加`GENAI_TOOL_REQUEST_TIMEOUT_SECONDS` / `--tool-request-timeout`，默认600秒，覆盖首次和全部空继续。源ping、下游心跳或新一轮继续不续期；到期以`upstream_request_timeout`失败并中断已取得的socket，不交付迟到或未校验的调用。
- 严格保留仅零正文/零思考/零调用/正常stop可继续的条件。断流、坏JSON/Schema、截断及已产生内容不自动重放；relay native不叠加重试。配置先校验再网页登录，正常取消引起的主动shutdown不再误报第二次网络故障。
- 架构文档统一多次预算、网络read timeout、生成总窗口与整场agent任务时长，明确预算耗尽仍必须报错，不能保证任务永不中断。下方“一次继续”条目是升级前历史。
- 正式31100/31110已重启加载3次/600秒配置并经真实omp复测：DeepSeek本轮20阶段、71累计断言通过，出现3个需第2次空继续的请求；Kimi本轮第1阶段业务舍入仍失败，未出现预算/timeout错误，按真实失败记录。结果不包装为模型能力排名或无限稳定保证。

### 持续工作故障根因修复

- 捕获真实网页SSE，确认存在零正文/零思考/零调用的正常stop；第一跳对工具请求仅在该精确条件下保留历史、追加一次继续指令，最多恢复一次，不重放客户端操作。持续为空明确失败，断流、坏参数、截断及已交付内容不适用。
- 第一跳统一空完成与结构化错误代码；Schema错误说明约束/字段而不打印参数值。relay安全保留上游message/type/code，不再把不同故障都改为笼统stream error；限制错误体、遮蔽凭据，失败不发送成功终态。
- 保留原长时验收结论，不将单次重放或故障注入当作新的无故障耐力测试；未因缺乏因果证据而修改并发、会话ID、输出预算或思考语义。

### 阶段文档与正式服务验收

- 新增 [STAGE_NATIVE_FIRST.md](docs/STAGE_NATIVE_FIRST.md)，记录两跳请求/历史/工具/SSE/错误/取消边界及重构前后区别；该文件随后归档至`docs/`。
- 因无法申请独立 API Key，现行方案放弃学校独立 API 与远程 base，固定采用客户端 → 本地 relay → 本地第一跳 → GenAI 网页后端；本地代理 Key 和 `UPSTREAM_BASE_URL` 仍保留。
- 正式31100/31110已重启加载重构版本，使用用户现有GenAI配置的omp完成有界实际开发验收：Kimi约31分44秒完成20阶段但有两次显式错误恢复；DeepSeek约8分钟通过9阶段后未完成预算阶段。报告保留空回复、流错误和真实工具错误，不将HTTP成功当作任务成功。

### 原生优先与流式重构

- 第一跳统一三协议的原生调用累积、严格文本解析与 JSON Schema 校验；保留调用 ID 和结果关联，支持 namespace、custom text、工具选择与并行禁用。已完成的调用才交付，任一非法成员拒绝整批。
- 删除裸 JSON、文档代码示例、损坏标签和参数猜测的自动执行路径；拒绝重复 JSON 键、非有限数、多封套和外部 Schema 引用；增加 DeepSeek DSML invoke/parameter 的严格解析。
- relay 默认 native 模式，向第一跳发送结构化 tools/roles/history，保留原生调用；显式 text 模式才使用封套与未交付内容前的受限纠错，不自动降级重放。
- 三协议首事件即时发送，持续输出正文与思考增量；第一跳有界心跳桥接，relay 转发心跳。取消先中断实际 socket 读取再关闭响应；取得响应头之前的取消仍受底层请求返回/超时限制。
- relay 响应大小预算在消费时按实际文本、思考、工具名称/ID/参数字节累计；SSE 重复元数据不挤占工具预算，单个未闭合事件另设上限。
- 不支持的 previous_response_id、item_reference、store=true、custom grammar 等明确拒绝，不再冒充已支持。学校独立 API 的授权入口与网页聊天端点分开说明，网页登录凭据不跨端点复用。
- 旧版封套纠错与宽松解析行为以下述历史记录为准；当前契约以 README 为准，代码示例不再被纠错为执行请求。

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
- 空正文／仅思考的重试改为追加到当前 user 轮次，不再制造连续 user 消息；保留任务、工具定义和附件，不复制思考内容，不改变重试次数。

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
