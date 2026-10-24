# deepseek-pro 反代能力测试报告

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
