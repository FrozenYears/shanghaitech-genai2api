# 任务客户端与 omp 监督器

基础配置已放回主 README：

- [Windows 与启动参数](../README.md#windows-与启动参数)
- [客户端配置与折叠示例](../README.md#客户端配置)
- [图片、文档、搜索与思考](../README.md#图片文档搜索与思考)
- [已知限制](../README.md#已知限制)

本页保留显式任务客户端及可选omp验收/反馈用法。所有命令从第一跳仓库根目录运行。普通API客户端不会被自动接管。

## 显式任务客户端试点

仅主动使用本入口的任务启用行动/完成申请/阻塞握手。普通Chat/Messages/Responses的`auto`问答不变，也不改变双跳工具执行权。

```bash
pixi run python -B -m tools.task_run \
  --base-url http://127.0.0.1:31110 --model Kimi-k3 \
  --env-file genai2agent-relay/.env \
  --workspace D:/work/project --task-file D:/trusted/task.txt \
  --verify-argv-file D:/trusted/checker-argv.json \
  --allow-python --max-requests 30 --max-seconds 900 \
  --output D:/audit/new-task-result.json
```

路径须替换为真实绝对路径，工作区预先存在，输出须为工作区外的新文件。`--env-file`显式读取relay密钥，不改变全局环境；默认先取环境中的`RELAY_API_KEY`，也可用`--api-key-env`指定变量名。学校令牌不交给客户端。验收配置是固定argv字符串数组，直接`.py`验收脚本必须是工作区外的绝对路径；退出0且执行完整才通过，stdout里的PASS不算证据。

- 本地工具为`read_file/write_file/list_files`；`--allow-python`才开放`run_python`执行工作区`.py`，不运行任意shell。路径拒绝越界/链接、读与列举最多1MiB，Python进程最长120秒且受剩余总时限约束，过滤常见凭据环境，输出有界。**Python仍有当前账户权限，不是OS沙箱**，可信checker也不能靠路径约束抵御任意恶意脚本；只在可信任务目录使用。
- 模型每轮可提出一个独立动作批次；先严格整批校验再执行。`task_complete`与`task_blocked`必须独占批次，不能与普通动作混用。正文和独立思考即时流式输出，不代表任务结束。
- `task_complete`只是申请：客户端运行独立验收，失败把真实缺口回传，成功才`completed/passed=true`。`task_blocked`为模型报告阻塞，返回`blocked/passed=false/model_reported=true`，不是已验证的外部故障或新增权限。
- 固定请求/总时间预算，不自动重放网络失败、截断、半调用或执行结果未知的动作。取消/到期不再启动工具或宣告完成；同步execute/checker不能被客户端强制抢占，HTTP等待最迟受当前15秒读超时限制（DNS除外）。无跨进程自动恢复或服务端任务存储。
- 输出JSON包含完整本次history与执行状态，可能含任务文件内容，需私有保存，不要提交原始记录。普通文本终答没有握手不能让任务通过；持续无有效操作仍会耗尽预算，不能承诺任意任务无限成功。

Python接入接口为`TaskClient(...).run(goal, tools, execute, verify, ...)`；已有客户端可接自己的工具执行与可信验收，不必使用内置文件adapter。它目前是独立试点入口，不宣称omp/Claude Code/Codex已自动获得相同机制。实际实验、初版失败与适用边界见[空转试点记录](STALL_EXPERIMENTS_2026-11-08.md)。



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


### 内置20阶段验收的完成标准

先启动同分支的31100/31110，并配置真实GenAI/Kimi-k3的omp。在Windows环境运行：

```bash
pixi run python -B validation/native-first-endurance/run.py run --model Kimi-k3 --stages 20 --max-feedback 0 --max-minutes 45 --stage-timeout 1200
```

该runner既独立运行累计业务检查，也核对模型实际调用指定验收命令的工具回执。最后一次源码修改后须重新成功验收；仅说“接下来运行”或事后由控制器检查通过，不算模型完成全部工作流。单独`verify`子命令仍只检查业务，避免递归要求自己的工具回执。`--max-feedback 0`失败即停，不自动发送“继续”；旧报告中的业务通过数不等于满足本轮新增的完整执行条件。

验收回执还须满足执行与时序条件：Bash 结果的非零 `details.exitCode`、`timedOut` 或未成功结束的 `details.async.state` 均不能被正文中的 `passed:true` 覆盖。只采用最后启动的指定验收，旧验收晚到不会覆盖新失败；源码写入/编辑尝试（包括失败尝试）结束后须重新验收，有未结束的源码修改时不能通过。这些规则只约束此 runner 的工作流证据，不是对任意任务的发布或视觉验收。

后台化验收不按“已开始”通过：runner只接受与原获准Bash调用jobId一致的可信单任务`async-result`完成通知，核对成功状态及对应阶段JSON；错误任务、失败或模型自述不作回执。RPC的`prompt_result(sessionSettled=false)`也不是完成，客户端保留该终态，待`session_settled`后再次查询真实会话状态，确认身份与停稳才结束。

同一阶段的自动反馈回合共享真实执行事件：前一回合已成功验收、随后上游错误，下一回合正常结束且未再修改文件时，不要求无意义重跑命令。累计事件仍按最后修改/最后启动验收排序；后续失败验收或源码/文档改动会使旧回执失效。新阶段不继承旧阶段回执。

验收管道固定UTF-8：`run_checker`向子进程写UTF-8 JSON并按UTF-8读取输出，同时只在子进程设置`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，不继承Windows终端的GBK配置。本轮曾出现模型命令检查通过、命名服务内独立检查把BOM读坏的假失败；已通过相同源码/相同用例的环境对照复现并修复。该失败不计为模型业务回归，受影响续测材料单列保留。

### 可选30阶段与隔离模型配置

```bash
pixi run python -B validation/native-first-endurance/run.py run --model Kimi-k3 --extended --stages 30 --max-feedback 8 --max-minutes 120 --stage-timeout 1200
```

将`--model`改为`deepseek-pro`可执行同一套任务。最后阶段累计303次独立业务子进程请求，另检查模块边界、受保护fixture、交付清单及模型实际验收命令回执；不要求为凑数量反复读取文件。此命令建立全新工作区和会话，不自动恢复旧阶段。

第30阶段需要真实计算文件摘要。若操作者授权工作区Python脚本，可加`--allow-project-scripts`，允许当前解释器执行`"./脚本名.py"`（命令形如`"绝对Python路径" -B "./generate_manifest.py"`）；仍禁止复合shell且保留固定验收命令。默认不开放。Python脚本具备当前账户权限，不是OS沙箱；不能因为使用相对路径就承诺文件隔离。此权限变更须在实测结果中记录，不能与旧只验收命令条件混算。

可选`--relay-base-url http://127.0.0.1:端口 --agent-dir 可信模型配置目录`连接隔离候选服务。配置目录须已有`models.yml`或`models.yaml`，模型provider仍须为`GenAI`且ID一致；只修改子进程`PI_CODING_AGENT_DIR`，不更改用户全局配置，启动禁用父目录context文件。人工复制源码后在新会话补做剩余阶段必须单列，不能与从头30阶段连续结果混算。

### 2026-11-08 扩展长测与平台可用性记录

本轮实时目录核对确认 `Kimi-k3` 的展示名为 `Kimi K3.1 Pro`，不是 model ID 变更；`deepseek-pro` 仍为 `DeepSeek-V4.1-Flash`，两者解析的 `rootAiType` 均为 `xinference`。间隔约18分钟后，DeepSeek 最小探针返回 HTTP 200/`READY`，Kimi 仍返回 HTTP 502，错误为 `no_available_workers`，上游明确指向 `kimi-api` worker 池及熔断/不健康状态。公开目录没有提供 worker 诊断，因此不能把它归因于客户端 ID 或代理路由。

为候选服务增加了显式隔离端点和 `PI_CODING_AGENT_DIR`，并准备了可选的扩展 21–30 阶段任务：精度/币种换算、CSV 原子导入、最大余数分配、批事务与 compare-and-swap、快照恢复、逐项技术/审查发布门禁、按币种结算及交付清单摘要。Bootstrap 已分别确认 Kimi 与 DeepSeek 使用隔离 relay、目标模型和限定工具；Kimi 随后单次扩展长测运行 320.328 秒、0 阶段/0 工具，因 `kimi-api/no_available_workers` 结束；不是成功长测。DeepSeek 单次扩展长测运行 1160.91 秒，进入24阶段、通过23阶段，执行191次工具调用、133次模型请求、43次阶段尝试；第24阶段在8次自动反馈后因`unknown_operation`及上游认证/服务错误停止，25–30未执行，也不是完整成功长测。
本轮不以 DeepSeek 代替 Kimi K3.1 Pro，也不通过增加重试掩盖平台不可用；`no_available_workers` 需要学校平台恢复健康 worker 后再复测。

### 同日重启后的真实续测与闭环

正式双跳已从修复分支重启，保留31100/31110；真实Kimi和DeepSeek均返回合法工具调用，Kimi结果回忆通过。DeepSeek首个回忆样本不匹配，后续三次差分历史探针均匹配，保留未归因边界，不据此宣称完全稳定。

- DeepSeek复制旧源码、新会话续24–30：7阶段全部业务与实际命令回执通过，1557.406秒、106工具调用、11工具错误、77模型请求、9反馈。不是从头30阶段无中断长测。第30阶段在旧权限下曾借临时业务模块副作用计算真实摘要，最后恢复源码再验收；全过程保留，不能称无错误完成。
- 显式项目脚本权限下另测交付：真实生成脚本、执行和305项检查及验收回执通过，133.687秒、零反馈。文档另经源码审计发现两项错误说明（restore输入排序、snapshot连接初始化/迁移写入例外），由真实DeepSeek只改文档、源码哈希不变，170.39秒、零反馈通过；两份原说明与修正版均保留。
- Kimi初次恢复后第1–13阶段通过，外层命令3600秒期限中断第14阶段反馈，不能算模型失败或整场通过。随后续测暴露GBK验收环境假失败，主动中断并修复；第14阶段业务及实际回执后按累计事件纠正审计有效，旧失败摘要不覆盖。
- Kimi新会话续15–30：第15–24共10阶段通过，87工具调用、2工具错误、58模型请求、3反馈；7200.188秒达到任务时限，在第25阶段停止。用户随后明确追加90分钟，仅续25–30：第25–29共5阶段通过，42工具调用、0工具错误、40模型请求、8反馈，5400.313秒在第30阶段到期。第30阶段三份文档/清单仍缺失，前两轮仅读取或给计划，末轮到期abort并确认停稳，未交付。Kimi累计分段验证至29，第30未完成，不能合称一次完整30阶段通过。

本轮闭环修复包括：HTTP200业务认证拒绝、独立parameter/calls坏外壳、延迟停稳RPC、可信后台验收回执、UTF-8管道一致性、同阶段跨反馈回执累计。最终第一跳804项回归通过；relay93项及301子测试通过。原始运行材料留在`.validation-runtime/longrun-resume-20261108/`，不提交凭据、原始会话或模型工作区。
