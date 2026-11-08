# 上下文与双跳传输审计

日期：2026-11-08。范围：第一跳 `tools/prompts.py`、三个 API 及 provider 转换，relay 的 normalize/messages/responses/engine/upstream 和对应测试源码。承接 [阶段记录](LONGRUN_PHASE_2026-11-08.md) 的未解问题；本分工只静态调查，**未运行检查、模型请求、长测或服务重启，未修改运行代码**。以下“已证”指源码确定的本地行为，另单列主线程实测；测试列举表示已有断言，不表示本轮执行通过。未读取凭据或原始会话。

## 1. 结论与边界

1. **“最后一条 user”不是“真正最新用户目标”。** 工具回执先被改成 user，再选择最后 user 放入学校 `chatInfo`；真实任务通常留在 `messages` 历史。字段没有在本地消失，但学校后端是否消费、按何种角色/顺序消费，仍无证据。不能据此排除架构导致的目标失焦。
2. **relay 的 native 只覆盖 relay→第一跳，不覆盖第一跳→学校。** 默认 native 模式保留结构化工具/角色；第一跳仍将工具 Schema 和调用改写成普通文本。学校请求没有原生 `tools` 字段。默认值依据 `genai2agent-relay/relay/config.py:38-39,67`，不是对当前进程配置的推断。
3. **三入口并不语义等价。** Chat 工具结果是带说明的 JSON 文本；直连 Messages 是 XML 风格属性文本，且重排同条消息里的结果/文本；直连 Responses 合并连续同角色，可能把多个结果及新用户指令并为同一个 `chatInfo`。relay 三入口最后统一调用第一跳 Chat，走的是另一条转换路径。
4. **历史思考存在确定的路径相关丢弃。** relay 对原始 thinking/reasoning 的保留，不代表学校收到；第一跳重建带工具调用的 assistant 时会丢顶层思考元数据。是否应该重喂、如何标注，属于需决定的语义政策，不能把盲目重喂当修复。
5. 已有测试保护 ID、Schema、图片、流式及部分元数据，但没有把三个入口、双跳、最后回执、真实目标、当前 Schema 一起串到学校最终 payload，并验证模型任务续作的闭环。可用性指标必须是**真实下一步操作/交付**，不是字段存在、正常 stop 或监督器拦住假完成。

**本轮主线程离线实测（主线程回报，未在本分工重复运行）**：无网络构造探针的原角色 `system/user/assistant/tool` → prepared `system/user/assistant/user`；`chatInfo` 含最后 tool_result 和 Schema、不含原目标；history 仍保留原目标；`is_error=true` 保留。这支持“当前轮布局发生变化”的事实，**不支持“原目标已经丢失”或“学校没有消费历史”的结论**。下面多结果/跨协议示例仍为静态推导，不能借此探针冒称逐项实测。

## 2. 已证的逐层表示

下文 relay 路径均相对 `genai2agent-relay/`；第一跳路径相对本仓库。

### 2.1 客户端 → relay → 第一跳 Chat

| 要素 | relay 规范化与移动 | relay 发给第一跳的表示 |
| --- | --- | --- |
| system/developer | Chat 保持角色与消息次序；Messages 顶层 system 汇为 `RelayRequest.instructions`；Responses 顶层 instructions 独立保留，message 的 developer 不降级 | engine 在开头插入 instructions 的 system；native 不再改消息。`normalize.py:213-248`，`messages.py:119-163,187-197`，`responses.py:129-160,199-228`，`engine.py:205-210` |
| 最新用户目标 | 不跟踪“任务目标”身份，只存普通消息；没有独立 goal/state 字段 | `TextMessage.as_dict()` 逐条发出；没有从成功/失败回执中恢复目标的逻辑。`engine.py:15-45`，`upstream.py:50-55` |
| 当前工具 Schema | `ToolSpec` 保留 name/description/parameters、strict；Responses 命名空间用限定名，自由文本 custom 包为 `{input:string}` | native 发 `tools`、`tool_choice`、`parallel_tool_calls`；custom 以 function wrapper 发给第一跳。`normalize.py:144-166`，`responses.py:76-116`，`upstream.py:56-71` |
| assistant 调用 | 历史 ID、JSON arguments 保持关联；Messages 同段多个 tool_use 收入 calls；Responses 相邻 reasoning/assistant 与后续 call 可合并成一条 assistant | 原生 `tool_calls`。历史调用不等于执行证据。`normalize.py:99-141`，`messages.py:132-163`，`responses.py:150-192` |
| tool 失败/后台通知 | Chat 顶层额外字段进 extra；Messages 的 tool_result 转独立 tool，保留 `is_error`；Responses output 额外字段进 extra | `as_dict()` 展开 extra，因此 relay 可传出错误标记/额外元数据；**没有专门后台通知状态机**。运行/完成语义若只在文本里，也只是文本。`normalize.py:226-235`，`messages.py:166-178`，`responses.py:183-192`，`engine.py:24-32` |
| 思考元数据 | Chat 保留 reasoning_content 和其它 extra；Messages 保留 thinking/redacted_thinking 原块（含原 signature）；Responses 保留完整 reasoning item（含 opaque 字段），并提取可读摘要 | native 可把这些顶层字段发到第一跳，但没有把它们变成第一跳必读正文。`messages.py:137-162`，`responses.py:150-160`，`engine.py:24-32` |
| 附件与次序 | 图片、文件转 Chat 形状；文本先合并、附件后置，不保留原 text/image 交错位置；Messages 在 tool_result 前 flush，可保持“普通文本段→结果→普通文本段”消息顺序 | `image_url`/`file` 内容块；`content.py:79-88`，`normalize.py:13-40`，`messages.py:45-78,166-178`，`responses.py:45-73` |

relay 的显式 **text 模式**另有实质语义变化：system/developer 抽走，合成 Operating instructions 加到第一条 user；tool 改 user 的 `@@RESULT@@` 数据；assistant 调用改 ACTION；历史思考合成一条额外 user 数据消息，放在原消息之前；Schema 加到最后一条 user。没有原生 tools 字段（`engine.py:211-266`，`actions.py:15-18`，`upstream.py:56-72`）。这会增加“技术上 user、语义上不是新目标”的候选，不能与 native 同组混算。第一跳 Chat 在无 tools、无 tool_calls 时不会再做工具提示注入（`api/chat.py:54-60`）；因此不能把 text 模式想当然描述成“两次原生 Schema 注入”。

### 2.2 第一跳三入口 → 统一消息

| 要素 | Chat `/v1/chat/completions` | 直连 Messages `/v1/messages` | 直连 Responses `/v1/responses` |
| --- | --- | --- | --- |
| system/developer | 有工具时 system 只取可读 content，developer 经通用规范化仍保留 role；不是统一折为 user | 顶层 system 插到历史开头；message 非 user/assistant/system 降为 user | instructions 插 system；developer 明确映为 system |
| assistant 调用 | `tool_calls`→同条 assistant 的 ACTION 文本，包含历史 id/operation/解析后 parameters；原 content 在封套前 | tool_use→每个调用各一个 ACTION 封套，排在普通文本后 | function/custom_call→assistant ACTION；连续 assistant 会合并 |
| tool 结果 | tool→user：说明“not a new user request”，JSON `call_id/output/is_error`，附件另保留 | 同条消息全部 tool_result 先汇成 `<tool_results>`，各块属性 `tool_use_id/is_error`；再拼普通 text，整条 role=user | output 先走 Chat 的 tool→user 包装；连续 user 合并，包括结果和后续真实用户消息 |
| 历史思考 | 通用 content 里的 thinking/redacted_thinking 被跳过；重建带调用 assistant 时只留下 role/content，丢 reasoning_content/thinking_blocks/reasoning 等顶层元数据；无调用消息可保留顶层额外字段 | 历史 thinking/redacted_thinking 明确丢弃，signature 不回喂 | reasoning item 明确跳过；message 项重建只留 role/content，额外顶层元数据不保留 |
| 当前 Schema | 工具定义正文仅 name/description/parameters；附加到规范化后**最后 user**；strict 等工具注解不写进该正文，参数 Schema 仍在 | 同一注入函数；tool_choice=none 时跳过注入 | 同一注入函数；tool_choice=none 时跳过注入 |

依据：`api/chat.py:37-60`；`tools/prompts.py:65-122,155-170,174-256`；`provider/anthropic.py:112-120,123-195,198-236`；`provider/responses.py:113-196`；`api/responses.py:36-63`。Chat 对有工具但 choice=none 仍注入“本次禁用工具”正文，另两直连入口不注入该正文；本地工具选择约束仍由校验处理，不能把这项提示差异说成放宽校验。

细节不能略过：

- Messages 把 `[text A, result B, text C, result D]` 改成“B、D 结果在前；A、C 文本在后”，不是原位置不动；relay Messages 则先拆成 user A/tool B/user C/tool D，之后再走第一跳 Chat。
- `normalize_tool_history` 的 tool 重建只带 ID、输出文本、is_error 和附件；其它顶层 error/details/job 元数据不进入新消息。如果 job 身份只在这些字段、不在 content，学校最终请求会失去它；这不等于已证真实客户端一定这样发送。
- 控制标记会转为字面 Unicode escape，保护它们不被误当当前调用。直连 Messages/Responses 已生成的结果 wrapper 再经注入的 user 规范化，部分 `<tool_result...>` 会被转义；Chat tool 分支当次创建的外层 wrapper 则保留。ID/结果文本仍可见，但分隔语法不等价（`tools/prompts.py:9-37,186-205,227-251`）。
- relay native 保留的 thinking 若附在带 `tool_calls` 的 assistant，仍在第一跳这一重建处分流丢弃；独立无调用 assistant 的 extra 可能到达学校 payload，但没有学校读取这些字段的证据。

### 2.3 统一消息 → 附件准备 → `chatInfo` / `messages` → 学校

`prepare_request` 根据**最后 user 的索引**处理附件，而不是根据真实目标角色：

- 历史图片留在对应消息内容；当前图片抽到 `imageUrl/imageUrls`；历史文件上传后必须拿到解析文本，内联带文件名文本，否则报错；当前文件只放上传后的 `fileIds`。所有文本合成后放图片前。来源 `provider/features.py:198-258`。
- 当前多图在 `_iter_genai_stream` 中改为历史数组末尾的一条独立 user 图片消息，清空顶层图片字段；不把当前文本塞回该图片消息。来源 `provider/genai.py:181-199`。学校如何配对 `chatInfo`、这条图片 user 和历史，仍需观测。
- Chat 的 `convert_messages_to_genai_format` 提取最后 user 文本；`split_history_messages` 删除该条 user，其它条保留。Messages 流同样反向 pop 最后 user；Responses 用自己的 `_split_chat_info`。来源 `provider/genai.py:24-31,61-68,437-440,478-481`，`provider/anthropic.py:322-333`，`provider/responses.py:204-213,279`。
- 最终学校请求是 `{chatInfo, messages, aiType, ...upstream_options}`，通过 `requests.post(..., json=genai_data, stream=True)` 发出，没有原生 tools。来源 `provider/genai.py:173-215`。日志目前只报模型/历史条数，不足以定位目标、Schema、回执在哪条（201-203）。

**代表性序列（静态推导，不是本轮实测）**：

```text
输入：system S, developer D, user G, assistant calls(A,B), tool B失败, tool A成功
Chat/relay-native→第一跳转换后：
  messages = [system S, developer D, user G, assistant ACTION(A,B), user result(B失败)]
  chatInfo = result(A成功) + 当前Schema/工具规则
```

G 没被本地截断；但最后成功回执获得当前提问槽，失败 B 留历史。若交换两个独立回执的到达顺序，`chatInfo` 与附着 Schema 的宿主随之变化；没有任何按 call ID 重排序或“先聚合本批全部结果再选当前”的代码。直连 Responses 会把相邻 B/A 的 user 回执合并，二者都进 `chatInfo`；再有新 user 指令时也合并进去。直连 Messages 同条多结果也共处当前槽。因此不能只看某个入口的快照替其它入口背书。

若最后一个 user 后还有 assistant（例如 Messages assistant prefill、或 relay text 生成的历史思考 user 后接 assistant），split 会取走中间的 user，保留后面的 assistant；最终只有两个 sibling 字段，没有原始 current-index。**学校重建时是否把当前 user 移到所有历史 assistant 之后属于未知**，不把 JSON 字段排列误当模型时间线。

### 2.4 返回与下一轮

学校 delta 的 reasoning_content/reasoning 能被第一跳作为 reasoning 输出；Chat 映 reasoning_content，Messages 映 thinking 并给明确兼容 signature，Responses 映 reasoning 输出项。来源 `provider/genai.py:34-40,368-371,489-501`，`api/messages.py:141-167`，`provider/anthropic.py:12-14`。relay 再输出对应协议（`relay/messages.py:200-208`，`relay/responses.py:231-247`）。这些是**输出呈现**，不保证下一轮历史推理能往返。

工具始终由客户端执行。提示已明确这一点，也区分后台启动/子代理 completed/真实交付（`tools/prompts.py:49-56,197-200`）。但文本要求不是任务状态机；本地看到 `is_error=false`、finish=stop 或完整历史，均不能证明任务完成。第一跳和 relay 的流式/整批校验不应因本报告改变。

## 3. 已有测试能证明什么、缺什么

| 现有源码断言 | 覆盖边界与缺口 |
| --- | --- |
| `tests/test_prompts_unit.py:57-85,139-181` | 保持 system/developer/附件、取最新 user、乱序结果 ID/is_error；样本有后续真实 user，未覆盖“最后是多条 tool，Schema 跟最后回执走”的学校 payload |
| `tests/test_anthropic_unit.py:28-87,365-384`；`tests/test_responses_unit.py:67-87` | 工具文本化、相邻合并、历史思考明确丢弃；不是学校对历史角色的消费测试 |
| `tests/test_image_history_unit.py:28-63,149-184` | 历史/当前图像保持、三协议多图及纠错不重复；不能代替文档、混合结果排序、双跳最终上下文语义覆盖 |
| `tests/test_endurance_error_unit.py:246-290` | 内部重生成时原历史/model 保持、当前问题前缀保持；直接传入已构造 history，未走全部 API→split 路径 |
| `genai2agent-relay/tests/test_reliability.py:21-126` | relay 规范化对象保留角色、thinking、error、附件、命名空间；到第一跳后仍可被重建丢弃，不能外推“全链路无损” |
| `genai2agent-relay/tests/test_engine.py:96-106,119-142`；`tests/test_upstream.py:60-75`（relay 内） | text 模式结果关联/角色降级/历史思考数据化；native HTTP payload；backend/session 是本地替身，不是学校模型输入证据 |

下一阶段首先补跨边界的**确定性 payload 契约样本**，再做有限真实对照；本轮没有执行这些样本，也没有把未发现的测试视为不存在的证明。

## 4. 必需的传输观测 seam（候选，尚未实施）

沿同一 trace/turn/attempt 关联四个点：

1. 客户端提交入口：原消息索引、role、内容类型、调用/回执 ID、is_error；合成夹具额外标记 `G目标/S指令/D约束/T当前Schema/F失败/B后台/A附件` 的独特 canary。
2. relay `prepare_*` 后及 `UpstreamClient._payload` 最后返回处：记录字段投影与来源索引，识别 role 改写/元数据丢弃的最早位置。
3. 第一跳注入前后、`prepare_request` 后，以及 `_iter_genai_stream` **紧邻 post** 的最终 `genai_data`：记录 `chatInfo` 来源索引、每条 history 的 ID/类型/长度/摘要、当前 Schema 摘要、附件路由与数量。内部恢复要有 attempt，不把 HTTP 重生成数当客户端 turn。
4. 学校入口之后：若能拿到学校网关转 worker 的脱敏请求/官方第一手转换源码，才可证字段在哪个模型角色及顺序中被消费；拿不到时，只能用 canary 续作行为探测作有限证据，不能宣称已排除学校历史忽略/截断。

默认只做结构与每次实验内部可比摘要，不写认证 header、签名 URL、opaque thinking、原附件或真实任务全文；合成夹具可在用户允许的独立工件中保存精确 payload。摘要相同证明本地字节一致，**不证明模型看见或遵从**；模型复述 canary 也不证明完整目标持续生效。观测不能阻塞 SSE、改变整批校验或接管客户端工具执行。

## 5. 优先可证伪假设与一变量实验

全部为**假设，不是根因结论**。下一轮按用户已明确的根因调查方向推进合成夹具与有限对照，先固定模型版本/采样、工具列表、完整历史、超时和上下文长度；同模型配对、AB/BA交替，先短夹具，不直接再跑30阶段。若学校不支持seed/确定采样，只报告重复配对频率，不以单次成败归因。真实用户内容采集和实质设计变更按第6节另行征询。

### 通用夹具与评分

使用隔离的确定性客户端工具：`read_state({key:string})`、`commit_result({value:string})`，均 `additionalProperties:false`。原目标 G：读取两个状态并把它们的组合值提交；夹具中已执行调用和回执用固定 ID。只把**历史真实调用**加入历史，不由模型自述补执行。下一次允许客户端执行合法调用并核对确切参数、回执及最终交付；计划、重复读取、重复成功操作、等不存在的回执、错误宣称完成分别计数。每对最多 3 个模型回合；用尽判未完成，不自动追加预算。

每一项都带两种负对照：N1“普通问答，有工具可用但无外部动作需求”应可正常纯文本结束、零工具；N2“工具任务全部有成功回执且结果满足 G”应可正常总结、不重放、不强制再调用。禁止用 required/追加监督恢复掩盖这些负对照。外加一份不完整任务正对照，确保评分器确实能识别应该继续的场景。

| 优先级 / 可证伪假设 | 只改变一个变量的执行设计 | 支持信号 / 反例 |
| --- | --- | --- |
| H1：真实目标只留历史、当前槽被回执占据，会降低续作 | 保留 G 及全部历史不动；同一 `chatInfo` 回执/Schema 尾部，A 加等长无关中性占位，B 加逐字 G 的“当前目标引用”，除此相同；先捕获最终 payload 确认。该额外锚定只用于实验 | B 在不完整任务中更常准确 commit，且 N1/N2 不额外行动，支持目标可用性问题；A/B 无稳定差异或差异只伴随上下文长度变化，则不支持。即使有效也不能区分学校忽略历史与模型注意偏置 |
| H2：多条 tool→user 后，最后回执/Schema 宿主造成末条偏置 | 两个互不依赖的历史 read，一条成功、一条失败；G 明确失败时先用同工具读取唯一合法替代 key。A 排 B失败→A成功，B 只交换结果顺序，ID/内容/Schema 不变；三入口与 relay 路径分别配对 | 正确下一步应始终为替代 key，而不是重读成功 key/直接 stop；只随“成功是否最后”翻转的行为支持偏置。结果次序变了但行为稳定、或直连 Responses 合并后同样失败，反驳简单“最后回执独占”解释 |
| H3：system/developer 在学校角色映射中弱化或不被消费 | 独立分两组实验，只测一种 role 一次：A 把同一条“commit 值必须加前缀 P_”规则放 system（或 developer），B 只把它放 user；不复制、不改文本/相对位置，G 和工具/回执不动，先确认本地只差 role | 本地 payload 保持该字段而 A 持续违约、B 遵守，提示角色消费/优先级问题；两者一致则反驳该夹具下的角色不可达。规则不能仅靠复述验收，要看 commit 参数。N1/N2 保持无需额外工具 |
| H4：Schema 尾附在回执后，压过失败语义/目标 | 在**学校 payload seam 的实验替身**中，保持所有消息、chatInfo 内容总量及规则逐字不变，仅把 Schema+协议规则文本从当前回执尾移至头；不更换 native/text、不改变 choice、不允许补猜参数。通过同一路模型短请求观察 | 位置变化稳定改变正确续作/旧参数误用率，支持局部注意/提示组织问题；均正确或均同样失败则不支持。该实验不能证明把 Schema 移到 system 更好，后者混入角色变量。N1/N2 检查不被规则强迫无限工具 |
| H5：丢失的历史思考里承载未完成状态，导致反复计划 | 选 relay native 的“assistant 同时有调用与可读 reasoning_content”夹具：推理仅含非敏感状态 `read完成，下一步commit`，G/回执本身足够解题。A 走当前丢弃路径，B 在最终学校 payload 的同条 assistant 加回**同一个** reasoning_content 字段；其余字段完全相同，禁止把它改成 user/system | 若 B 稳定续作而 A 反复读，提示元数据消费需查；无差异不支持其在该学校路径的因果作用。先看 seam 确认唯一差异。不得用 opaque/encrypted 内容补正文；若加回字段无效，不能擅自升级为重喂所有思考 |

先执行 H2/H3 的离线端到端投影样本，可无模型确定角色/排序变换；真实请求再按上表优先级评估。后台通知可作为 H2 的替代夹具另立配对：只换两个独立回执的到达顺序，通知中的可信 call/job 身份与最终结果必须不变；**不能同时替换排序和通知文案/信任来源**。

## 6. 最小改动候选与必须问用户的变化

### 可先提出的最小候选（未修改、未证明有效）

- **P0：测试 seam，不先换控制架构。** 用本地 fake relay backend→真实第一跳转换→捕获学校 post 的同一夹具，断言 G/S/D、Schema、ID、失败、当前/历史附件及排序投影；覆盖末条多 tool、tool 后 user 通知、assistant 尾项、Messages 混合块、Responses 连续合并和带调用思考。不要仅给现有 normalize 单测加一个“字段存在”断言。
- **P1：结构化观测。** 在现有转换边界增加实验期 opt-in 观察器/本地替身，不先新增常驻任务存储或自动续作；先定位本地确定丢弃与学校未知 seam。安全记录政策和真实流量采集需用户许可。
- **P2：按证据做单点修正。** 若 H2 的投影/行为证据成立，优先修同批结果表示和来源身份，不先加“必须继续”大段提示；若 H1/H4 成立，最小尝试为显式当前目标引用或调整当前 Schema 位置，并保持完整原历史及附件。都必须带 N1/N2，不能把计划 stop 全改 error 当成功。

### 实施前必须用人话征得用户许可

1. 采集真实用户请求内容、扩大日志保存或申请学校侧可观测权限；说明采什么、保留多久、如何脱敏。合成夹具的结构摘要和既有源码审计按用户调查方向推进，不重复索要逐调用许可。
2. 改 `chatInfo` 选择策略、合并/拆分工具结果、重排原消息、将 system/developer 降级、把 Schema 移到其它角色、重喂历史思考。上述都改变语义而非纯日志；不能以“完整历史仍在”免除说明。
3. 引入持久 goal/state、自动合成用户续作消息、强制终答握手、改变 auto 的普通问答/已完成任务含义、由代理默认接管客户端执行。属于超出本报告的控制设计，不能暗中实现。
4. 超出既定范围/预算的大规模真实模型对照；先固定短配对的模型、次数、时限，失败即记失败，不增加无限重试、不把监督拒绝假完成计作可用性达标。
