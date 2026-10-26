# 本地工作日志

## Kimi 工具纠错修复（2026-10-26）

- 新建`fix/kimi-tool-recovery`，以已发布的`5021553`为代码基线；没有自动合入main。保留并发会话的README目录整理，不将其混入本次提交。
- 历史自然记录`.validation-runtime/omp-kimi-k3-7olxj28k`阶段2确有坏JSON错误，错误消息前已有思考和点号正文；旧状态机因此禁止同请求纠错。该日志不含原始封套，不能断定具体损坏字符，也不把用户新报缺少`i`误称为历史已捕获错误。
- 定向回归在修复前返回502；修复将有纠错预算的工具请求逐代暂存至完整校验成功，失败代丢弃。提示补充必填元数据和最终尾标记规则；未更改解析宽严、预算、总时限或工具执行权限。
- 修复后全量587项通过（8.23秒），其中纠错与流测试定向150项通过；旧取消测试中要求提前显示前缀的断言已移除，改由未校验输出保留心跳且可取消的行为测试覆盖。
- 真实omp分别执行三类故障注入与无注入业务任务，严格验证文件字节、AST、业务结果、读回与调用顺序；每组只发一个用户prompt，禁用客户端自动重试和监督器。详情见`validation/kimi-tool-recovery-2026-10-26.json`。
- 使用动态loopback端口和隔离omp配置，不改全局设置，不重启或停止已运行的正式31100/31110；临时服务随脚本退出关闭。旧实测表和长期模型结果不重写。
- 外部检索仅作模式参考：公开Pydantic AI的`RetryPromptPart`示例将校验失败反馈给模型，不代表它存在本项目的同一故障；修复依据是本地状态机、真实历史和回归，没有引入依赖或复制外部实现。
- 用户选择保留两组摘要、omp事件/会话与验收文件；已删除本轮临时smoke脚本、隔离模型/设置和HOME/缓存。旧证据不动，原始会话不提交到Git。

## README 导航与部署/设计分组（2026-10-26）

- 两仓库main README增加前置跳转目录，按“部署与接入”“功能与设计”“实测与资料”分区。部署步骤与协议/恢复设计不再混排。
- 两跳流程、角色分工移入设计部分，调整标题层级；原有Windows、客户端、工具原理、附件和实测章节仍可通过锚点跳转。
- 对照main修改前快照，代码块、表格、内联代码、callout及折叠标签均保留；仅重排章节、增加目录与分组标题。本轮不改服务配置、不启动反代、不重跑历史模型任务。
- 使用真实omp无工具、无会话子进程调用`OpenTechGemini/gemini-3.8-flash`，前后模型身份一致。根README按部署与设计两部分校对，只采纳自然的措辞，未采用删掉恢复限定或“发至此处”等更生硬的建议。
- 完整检查18份文档的144个链接、39个锚点，无失效引用；JSON/TOML示例语法通过。重排前后代码块、全部表格、内联代码、callout及折叠状态均核对一致，Windows命令仍直接显示；未启动或重启反代，未重跑历史模型测试。

## main 发布入口与定向停服（2026-10-25）

- 两仓库 README 的 Windows 安装命令直接展开，移除 Windows 的 details/summary 标签；其余客户端与附件折叠说明保留。安装命令和当前文档链接改用 FrozenYears 的 main，不再要求用户检出开发分支。
- 获取两个origin的最新引用后，relay快进合入main；根仓库以合并提交`ef2cfab`保留原main的4个Windows支持提交。仅pixi.lock冲突，逐项保留工作分支已有的Windows依赖记录；解决后锁文件与工作分支逐字节一致，`pixi lock --check --dry-run --offline`确认无需变更，没有安装或升级环境。
- 定向停止工具登记的 `GenaiFirstHopAutoRecovery` 与 `GenaiRelayAutoRecovery` 进程树。Windows 项目进程、31100/31110及旧临时端口31200/31210均无残留；Ubuntu-24.04的用户/系统GenAI单元及相关进程均未发现运行项。
- 对比停服前后快照，79个其他Windows监听项全部仍在；没有批量结束Python、关闭WSL、停止其他Windows/systemd服务或修改其启动配置。
- 已验证两个仓库所有本地分支及origin分支均为当前main的祖先，没有重置或强推。合并后使用现有隔离环境运行检查：第一跳562项通过，relay89项及259个子测试通过；没有重启反代或重跑长期模型任务。

## README 内容回填与两跳职责说明（2026-10-25）

- 按用户要求恢复精简前的九行模型实测表（含表头和分隔行）；值与原记录一致，不重跑或改写模型结果。
- 流程图标明第一跳/第二跳和仓库名，启动标题加仓库括注；改用“逐段接收回复”等直接说法。
- Windows启动参数、折叠的客户端详情、图片/文档/搜索/思考和已知限制放回根README。`docs/USAGE.md`收敛为可选omp监督器说明，其他功能通过链接回到主文档。
- relay以用途、双向请求流程和职责表说明与第一跳的关系；明确第一跳也可独立接客户端，双跳部署时请求先到relay，工具仍由agent执行。
- 本轮只改文档，不改服务代码、环境和运行配置，不重启服务或重跑长期验收。
- 通过真实omp编辑子会话使用`OpenTechGemini/gemini-3.8-flash`，前后模型身份一致；根文档按章节校对。仅采用自然的句子，未接受过度缩写、自动刷新保证或删掉失败条件等建议，完整实测表保持逐字一致。
- 完整读取18份文档并检查130个链接、22个锚点，无失效引用；校验了JSON/TOML示例、默认关闭的折叠区域及两跳标签。启动和监督器的`--help`运行正常，没有新增临时文件或重跑模型长期任务。

## README 精简与入门配置保留（2026-10-25）

- 用户反馈两份README过长，改为步骤、短句、紧凑表格及GitHub callout。两跳安装、`.env`、密钥对应与客户端配置继续保留，避免为了缩短篇幅省掉启动所需信息。
- 将详细客户端、附件和监督器用法收录到`docs/USAGE.md`；relay完整参数、Windows说明及自定义backend接口收录到`docs/CONFIGURATION.md`，已有验证报告和原始材料不动。
- 新增无原生工具通道时的四步调用说明：客户端声明、网页提示生成、严格解析与整批校验、客户端执行并携带ID回传。第一跳支持恢复不意味着网页开放原生函数输入，也不意味着代理获得执行权限。
- 本轮只调整文档，不改反代配置、不重启服务、不重新运行长期模型验收。
- 通过真实omp编辑子会话调用`OpenTechGemini/gemini-3.8-flash`，前后身份一致；首轮出现扩写和英文简介，未采用。增加“只缩短、不翻译、不新增说明”约束后采用短句润色，并恢复被删掉的HOST限定、额外次数和已测样本边界。
- 按字符统计，根README由11855缩至5562（减少53.1%），relay由12012缩至3929（减少67.3%）；代码块、内联代码、链接、数值、标题、callout和折叠标签均核对保留。
- 已检查18份文档的126个链接、19个锚点，无失效引用；JSON/TOML示例语法通过，并运行第一跳启动参数和监督器的`--help`。没有安装依赖或新增临时文件，不将已有长期模型实测重标为本轮结果。

## README 整理与参考文档归档（2026-10-25）

- 本轮按现行学校网页双跳链路整理根项目和 relay README，区分当前使用契约、重构前对照与各阶段历史证据；不增加独立 API Key 或绕过第一跳的使用路线。
- 将阶段说明、工作日志、模型测试报告、Pi 验证、网页能力调查、功能验证和测速报告共七份文档从根目录迁入 `docs/`，保留文件名与历史结果；新增目录导航，并调整相对链接和 relay 跨仓库入口。`validation/` 的机器摘要、可执行 runner 与 `.validation-runtime/` 原始材料不移动。
- 本轮没有重跑 GenAI 模型长期任务、附件验收或测试套件，也没有重启服务；已运行四个文档命令的`--help`并确认双跳`/health`正常。下方各阶段的通过数、耗时和PID保留为历史结果，不重标为本轮验收。
- 本目录文档中的命令默认从仓库根目录运行；相邻 Markdown 相互引用，源代码与 `validation/` 链接返回上一级。固定提交链接保留旧快照路径，不随归档改写。
- 第一轮内容整理保留根README中文技术说明与relay英文主语言；用户随后明确要求relay改为中文，最终按该要求处理。README加入DeepSeek/Kimi真实20阶段实测摘要，同时保留自动恢复与失败边界。
- 内置任务工具不接受普通子任务的指定模型参数，改用已安装omp的独立RPC编辑子会话，实际前后身份均为`OpenTechGemini/gemini-3.8-flash`、`openai-responses`。关闭工具、会话保存、扩展、隐藏重试与fallback，只传入公开README内容，不改全局配置。根长文按RPC分片重组后成功取得；先前未提取正文是临时读取器漏处理分片，不是上游模型空响应证据。
- Gemini输出的代码块、内联代码、链接目标与数字均与润色前一致；父会话复核并校正了“直接透传”“全局窗口”、预算/学校配额混淆及原版代理认证等技术措辞。未建立新的原始验收目录或临时脚本，旧实测材料不动。

## 架构文档补充：原仓库分支对照（2026-10-25）

- 实时核对原作者第一跳`main=7e95aab`、第二跳`main=dbb8f65`；个人fork的两个`main`分别为`7e95aab`、`6b02115`。这些不是本地重构前的`e682010`/`5d3bd12`，也不是当前`feat/native-first-streaming`；对照采用冻结提交链接并保留不同阶段的改动归属。
- 架构第五节按两跳分别说明功能和设计差异，明确原版已有能力、本地先行修复、结构化流式迁移、严格校验和两层恢复；第六节限定“接近官方API体验”的适用范围。README和CHANGELOG同步入口，不把无对照长测当成官方API等价证明。
- 本轮仅修改文档；验证文档引用与基线对象，不修改代码、不重启正式服务，不把既有562/89项回归或20阶段模型结果称为本轮重新运行。

## 当前状态：未交付工具纠错与自动监督部署（2026-10-25）

- 实现前checkpoint根`c6f8959`、relay`100169e`，继续使用`feat/native-first-streaming`。第一跳默认空继续8（0..30）、未交付坏工具批次纠错3（0..10）、共享900秒窗口，分别累计且不重置。只对正常结束且尚未交付内容的协议错误要求模型重新生成，绝不猜参数、执行坏批次或重放已执行操作。
- 新增通用`supervisor.py`/`supervise.py`，原20阶段runner接入同一循环：额外8次自动反馈、整场45分钟、单轮1200秒；真实独立验收、停滞诊断、同一会话继续、逐轮记录与检查点。旧`--repairs`移除，改`--max-feedback`。旧“显式反馈”也原本是脚本自动提交，不是人工逐次输入。
- prompt超时先abort并确认本轮终结与RPC settled，最多另等30秒安全停稳；不能确认则`session_not_settled`，不并发重发。任务预算耗尽保存状态并失败退出，不保证无限成功或跨进程自动恢复。默认通用agent无bash，可信验收固定argv、本地shell=False、有界输出和超时，权限模式不是OS沙箱。
- 根562 passed；relay89 passed/259 subtests。首轮唯一失败是旧raw-generator测试锁定内部空done，将其迁移为公共HTTP空完成502/非空无工具不受工具deadline的行为检查，未压制生产错误。真实socket12路径验证空→缺参→坏JSON→合法调用只交付一次；正文/思考/截断不重放，耗尽保留具体错误，持续ping约0.235秒触发0.2秒窗口。
- 已重启正式第一跳Python PID107568、relay PID104472，服务`GenaiFirstHopAutoRecovery`/`GenaiRelayAutoRecovery`均persistent，原31100/31110健康；显式8/3/900，relay native仅连本地第一跳，只用学校网页认证/聊天。未改用户全局模型配置。
- 正式真实omp长测：DeepSeek595.000秒、20/20、150工具、104响应、71累计断言通过，agent额外反馈0；第一跳自然8空继续+4工具纠错（3请求，最大第2次纠错）。4工具失败含2策略拒绝、代码语法与旧库迁移，均同轮解决，不扩大权限。
- Kimi1825.531秒、20/20、110工具、94响应、71累计断言通过；3阶段共4次自动反馈、没有人工输入。阶段2坏JSON与未接线、阶段3未知工具、阶段4连续只给计划均自动恢复；前两次已交付思考/正文，正确由监督器新回合续作，不由第一跳撤回重放。阶段20一次edit失败同轮修正。单阶段最多2反馈，不能宣称自然用满8次；本次金额通过不抹去旧业务失败。
- 通用CLI真实Kimi库存项目：65.063秒agent轮次、4文件工具、8独立用例通过，无bash、无额外反馈。完整摘要`validation/automatic-recovery-2026-10-25.json`；架构第十节、README与报告已同步，历史部署明确标注。
- 用户选择保留本轮两个正式长测目录与通用任务证据，只删除受控HTTP临时故障注入脚本；旧保留目录不动。会话、生成代码、SQLite、任务/验收器、机器摘要均保留用于复查。

## 上一阶段：多次空继续预算部署与正式复测（2026-10-25）

- 本轮checkpoint根`7a86b50`、relay`286cd38`。第一跳默认额外空继续3次（合法0..10，0禁用），首次加继续最多4次；默认600秒覆盖整个工具请求上游生成窗口，ping/心跳/新继续不续期。配置与CLI已写入架构文档，原一次策略标为历史。
- 根388 passed、relay89 passed/259 subtests。真实socket持续ping测试在0.2秒窗口下约0.234秒返回`upstream_request_timeout`、实际连接关闭、读取线程退出，未交付工具。正常取消的主动shutdown不再误报第二个网络故障。
- 正式服务已重启：第一跳Python PID62956、relay Python PID107928，服务句柄`GenaiFirstHopBudget`、`GenaiRelayBudget`均persistent。启动参数显式3次/600秒，日志确认策略加载，原31100/31110健康。
- 通过正式双跳的真实omp20阶段复测已完成：DeepSeek851.204秒、20/20阶段、165工具、96模型响应尝试，最终71累计断言通过；源日志20次空继续（17次第1、3次第2），另两次edit缺path须显式反馈修复。Kimi330.282秒、0/20、17工具、11响应尝试；三轮均正常非空结束但最后仍有金额舍入错误，并非空继续预算耗尽或超时，不能记成功。固定fixture均不变。
- 用户选择保留本轮两个正式复测目录`.validation-runtime/omp-deepseek-pro-bj6xlsuj`、`.validation-runtime/omp-kimi-k3-k7vtu9dw`，已清理socket临时脚本和两个测试字节码缓存；精简结果`validation/recovery-budget-2026-10-25.json`。此前正式长测与根因关键取证目录保持不动。

## 上一阶段：持续工作故障根因修复
- 本轮 checkpoint：根`75afcfc`、relay`9e0c6e6`。真实网页SSE已复现零正文/零思考/零调用却正常stop；未发现完整内容被relay丢掉的证据，网页内部成因仍不可见。确认第一跳空成功和relay覆盖具体错误是代理缺陷。
- 第一跳只对工具请求的完全空正常完成发起一次保留历史的明确继续，禁止重放已有增量、坏参数、断流或截断；持续空与仅思考回答不伪报成功。两跳统一保留安全message/type/code，Schema错误不回显参数值；没有放宽parser、猜参数或改变模型。
- 回归根305 passed、relay89 passed/259 subtests。实际旧失败历史副本续作：DeepSeek任务10–12独立50断言通过，Kimi任务2–4独立20断言通过；录制空帧故障注入后接真实GenAI，预算阶段46断言通过。额外DeepSeek取证两次自然空stop自动恢复，后遇坏JSON安全拒绝，独立检查还发现模型预算实现错误，未将其算作业务成功。
- 正式服务本轮已再次重启：第一跳Python PID88780、relay Python PID68920，服务句柄为`GenaiFirstHopRepaired`、`GenaiRelayRepaired`，均persistent。重启后两模型在原31110均通过真实omp write/read。下面旧验收PID与数字是历史记录，不是现行进程。
- 用户选择保留本轮六组关键取证，已清理临时采集脚本、隔离模型/配置缓存、初期不忠实重放与两个正式端口短试点；保留路径列在`validation/relay-rootcause-2026-10-25.json`。前一轮两个正式长测目录未动。

## 上一阶段：正式双跳部署与 omp 验收
- 用户授权大量重构；根仓库和 relay 均新建 `feat/native-first-streaming`。重构前 checkpoint：根 `e682010`（保存用户已有 CHANGELOG 修改）、relay `5d3bd12`。
- 第一跳统一工具交付边界：结构化调用优先，严格 action/XML/DSML/Kimi 解析兜底；删除裸 JSON、文档示例及损坏参数的猜测执行。完整 Schema、ID、choice、parallel、原子批次统一校验。
- relay 默认 native，保留 tools/roles/history 与调用 ID；text 必须显式选择。支持 custom text 和 namespace；grammar、服务端状态引用等不支持能力明确拒绝。
- 三协议即时开始事件、安全正文/思考增量及心跳；完整调用校验后交付。取消中断已取得响应的实际 socket；尚未返回响应头时仍受底层超时限制。
- 用户明确无法申请独立 API Key；现行方案放弃学校独立官方 API 和远程 base，只使用 GenAI 网页反代。`STAGE_NATIVE_FIRST.md` 记录两跳职责与重构前后差异，本地 Key 和 `UPSTREAM_BASE_URL=http://127.0.0.1:31100` 保留。
- 回归：根 275 passed；relay 79 passed、164 subtests passed。真实验证：12 条文件工作流、48 次实际工具执行，另外 2 条 custom text + namespace 写入/结果回传闭环；详细证据和首次失败修复见 `TEST_REPORT_deepseek_pro.md` 新节。
- 本轮已按授权停止旧第一跳 PID54264、旧 relay PID77140，在原 31100/31110 重启新代码并保持服务运行。实际监听的 Windows Python PID 为第一跳103556、relay89612（工具服务句柄 PID 不同）；第一跳全三协议，relay 明确 native + 本地31100。健康检查及两端新协议400错误行为通过。未推送远端。
- 原有 `.pixi-cache/`、诊断脚本、启动脚本等未跟踪用户文件不纳入重构提交。
- 长时验收主客户端按用户建议改用已安装的 omp 18.12.6。启动时读取真实RPC模型身份，确认 `GenAI/deepseek-pro`、`GenAI/Kimi-k3` 的 `openai-completions` 均指向 `http://127.0.0.1:31110/v1`；禁止模型fallback/自动请求重试/后台advisor/MCP/扩展/技能。没有修改用户全局配置。
- 正式 omp 验收已结束：DeepSeek 480秒、9/20阶段、57次响应尝试、73次工具、9次显式反馈，预算阶段仍失败；Kimi 1903.828秒、20/20阶段、74次响应尝试、91次工具，2次流错误经显式反馈恢复，最终71项累计断言通过。固定73,728字节fixture均不变。结果见`validation/omp-endurance-2026-10-25.json`和测试报告；没有宣称无人值守无错误。
- 用户选择保留 `.validation-runtime/omp-deepseek-pro-heo6olng`、`.validation-runtime/omp-kimi-k3-4gnve5pv` 两次正式验收原始材料，清理两次omp试点、两次中止Claude任务与外部初始Claude试点。Claude的路径权限失败、中止任务及omp短试点均不计入正式成功统计。验收源码位于`validation/native-first-endurance/`，不依赖官方API。

## 上一轮状态（重构前）

- 工作目标：修复工具调用截断、模型误报完成、工具不可见、Kimi K3 原生工具协议未执行，以及长时多轮工具工作的可靠性问题。
- 当前根仓库分支：`feat/windows-platform`。
- 当前 relay 子仓库分支：`main`。
- 原 31100（第一跳）和 31110（relay）已按用户批准重启，健康检查均返回 HTTP 200；本日志写入时未再次停止它们。
- 远端尚未推送。根仓库本地分支没有配置可用 upstream；relay 本地 `main` 相对 `origin/main` 领先 6 个提交。
- 用户已有改动与未跟踪文件未纳入本轮提交：`CHANGELOG.md`、`.pixi-cache/`、`_diagnose_auth.py`、`_kimi_test.py`、启动脚本及 `genai2agent-relay/` 外层未跟踪目录状态按原样保留。

## 已完成修复

### 根代理

- 当前 user 轮次携带完整工具定义和 JSON Schema。
- 主工具协议改为 `@@ACTION@@...@@END_ACTION@@` JSON envelope；保留既有 `<tool_call>` 和 GenAI DSML 兼容解析。
- user/tool-history 中的 `<think>`、tool/DSML 标签及 action 分隔符使用可逆 JSON Unicode 转义，避免模型把文件内容识别为控制语法。
- action envelope 在移除 think/DSML 或处理 Markdown 引用之前解析；参数内部标记按原字符串恢复。
- Markdown 代码引用、反引号文本中的 action/tool 标记不再触发执行。
- Kimi K3 原生 XTML tools 通道增加严格解析：
  - `<|open|>tools<|sep|>`、`call`、typed `argument`、`json` 参数块；
  - string 原文保留；array/object/number/integer/boolean/null 按声明类型解码；
  - 未知工具、重复参数、类型错误、坏 JSON、截断批次或部分批次均拒绝，不自动补全执行。
- Chat、Anthropic、Responses 在必需工具调用未返回时返回错误，不再把普通文本报告为成功。
- 上游 SSE 提前 EOF、工具调用被 `length` 截断或 action 标记本身截断时，不交付部分工具调用。
- Anthropic 流式路径保留代码引用中的 `<think>` 字面文本，只过滤实际未引用的思考块。

### Relay

- action marker 检测排除闭合代码引用和合法 action 参数内容。
- user 数据中的控制标记转义；assistant 历史调用不再被当成 user 数据误转义。
- tool result 明确标记为“已经执行的结果”，要求模型在依赖操作完成后以普通文本结束，不重复同一 read。
- action envelope 仍执行白名单、JSON Schema、大小、完整性和原子批次校验。

## 真实模型验证

测试均使用实际 GenAI 上游模型、实际 HTTP 服务和隔离目录中的真实文件/进程/SQLite/HTTP 服务，不使用模拟模型输出。

### 模型

- `deepseek-pro`：平台 ID，`owned_by=xinference`。
- `Kimi-k3`：平台 ID，`owned_by=xinference`。

### 长文件

- 33,803 字节、256 行、含 Unicode、反斜杠、引号、尾部换行以及 think/tool/DSML/action 字面标记。
- 两模型均完成 write → edit → read，最终 SHA-256：`29e64d5bb3f7a66c03f8600c2b545acd11541cc419dd29ac167003491b53edbc`。
- Kimi 首次写入约 446 秒；DeepSeek 首次写入约 57 秒。单次样本不是延迟保证。
- Kimi 曾出现一次原始参数复制错误：预期 8,389 字节，模型生成 8,390 字节；错误在上游工具参数中已存在，不是代理落盘截断。read 发现后 edit 修正，再次 read 通过。

### 长时增量开发

- Kimi：同一持久历史完成 12 个增量功能，50 次请求、45 次工具调用，逐阶段运行只读 Python verifier；81,511 字节逻辑源文件，Windows CRLF 磁盘基线 82,550 字节，固定 fixture 字节保持不变，全部验证通过。
- DeepSeek：12 个增量功能，55 次请求、45 次工具调用，全部累计断言通过，固定 fixture 保持不变。
- DeepSeek 有一个阶段在 verifier 已通过后继续优化，达到该阶段请求上限；下一阶段由新的 user task 继续。不能把它描述为始终自然收敛。
- 3,202 行、约 317–320 KB 的既有源码局部 edit：Kimi 的 LF/CRLF 两种基线和 DeepSeek 的 CRLF 基线均逐字节通过，且运行实际 verifier。

### 多类型工具

实际覆盖 11 类工具：

- `write`、`edit`、`read`
- `run_python`
- `glob`、`grep`
- `command`
- `sql_query`
- `http_get`
- `start_job`、`job_status`

场景包含：无匹配搜索、命令非零退出后重跑、SQLite 参数绑定错误后修正、HTTP 404 后正常请求、后台任务 running 到终态、单 action envelope 内三个独立调用及结果回传。

两个模型的 fresh broad-tool session 均正常结束；同一 Kimi 长会话在 fixture 补齐后也完成全部场景。初次缺失 fixture 的失败被保留为真实失败，不算成功。

### Kimi 原生协议

实测捕获到 Kimi 原生输出：

```text
<|open|>tools<|sep|>
<|open|>call tool="command" index="1"<|sep|>
<|open|>argument key="argv" type="array"<|sep|>["python","command_fixture.py","--check"]<|close|>argument<|sep|>
<|close|>call<|sep|><|close|>tools<|sep|><|close|>response<|sep|><|close|>message<|sep|>
```

此前第一跳把它当普通正文，以 `end_turn` 结束，工具没有执行。新增严格 K3 parser 后恢复为 `command`，实际执行 exit code 0。格式参考公开的 Xinference Kimi K3 parser：

- https://github.com/xorbitsai/inference/blob/main/xinference/model/llm/tool_parsers/kimi_k3_tool_parser.py
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/tool_parsers/kimi_k3.py

### 原端口部署验证

用户批准重启后，原 31100/31110 使用新代码完成六条真实工作流：

- Kimi：relay Chat、第一跳 Anthropic Messages、第一跳 Responses。
- DeepSeek：relay Chat、第一跳 Anthropic Messages、第一跳 Responses。
- 共实际执行 32 次工具；验收包含命令错误恢复、SQL、glob 和 HTTP 返回值，不以 HTTP 200 作为成功标准。
- 六条工作流均通过并正常结束。

## 验证结果

- 根项目：`pixi run test` → **131 passed**。
- relay：`python -m pytest tests -q` → **39 passed, 42 subtests passed**。
- 根代理 Python 语法检查通过。
- Kimi parser、跨协议参数保真、截断、坏批次、必需工具、引用文本和流式边界回归均通过。

## 当前稳定性边界

- 模型生成的长文本仍可能语义复制错误；合法 JSON 或 Schema 不能证明内容符合用户原意。
- 长文件应优先局部 edit，不应要求模型反复全文重写。
- 写入/编辑后必须 read 或运行实际 verifier；重要内容使用独立字节哈希。
- relay 为安全校验会完整缓冲第一跳响应，双跳尤其是长输出的首事件延迟明显高于官方原生流式工具通道。
- 本轮验证是有界的串行压力测试，不等价于任意小时级无人值守稳定性保证。
- 原有服务的认证、上游平台状态、模型缓存和上下文上限仍可能影响体感。

## 提交记录

### 根仓库

- `b841eed chore: checkpoint before tool transport fixes`
- `b4cc31d fix: preserve tool call integrity`
- `91344bf fix: preserve local model tool verification replies`
- `9661357 fix: preserve tool action payloads`
- `a50d0a2 fix: parse complete Kimi K3 native tool calls`
- `9b7af87 chore: checkpoint before native tool transport fix`

### relay 子仓库

- `127d7e0 chore: checkpoint before tool transport investigation`
- `795aff0 fix: escape action markers in tool context`
- `5bc9fad fix: preserve quoted tool markers and clarify results`
- `5ad2e8e chore: checkpoint before native tool transport fix`

以上提交均为本地提交，尚未推送远端。

## 证据与保留产物

- `TEST_REPORT_deepseek_pro.md`：本地模型长文件、持续开发、宽工具覆盖和原生 K3 协议报告。
- `validation/local-model-tools-2026-10-24.json`：8,389 字节工具闭环摘要、哈希和截断/恢复结果。
- `validation/kimi-endurance-2026-10-24.json`：33 KB、320 KB、12 阶段开发、11 类工具和原端口部署结果。
- `C:\Users\FrozenYears\AppData\Local\Temp\genai-localmodels-9djlpkl6`：前一轮模型工作流及原始上游捕获，按用户选择保留。
- `C:\Users\FrozenYears\AppData\Local\Temp\kimi-endurance-2ffklxo2`：本轮 302 个隔离测试文件/捕获，约 2.1 MB，按用户选择保留。
- 两个临时代理端口 31200/31210 已停止；原 31100/31110 按批准保持运行。
