# omp 任务监督器

基础配置已放回主 README：

- [Windows 与启动参数](../README.md#windows-与启动参数)
- [客户端配置与折叠示例](../README.md#客户端配置)
- [图片、文档、搜索与思考](../README.md#图片文档搜索与思考)
- [已知限制](../README.md#已知限制)

本页保留可选的 omp 验收与自动反馈用法。所有命令从第一跳仓库根目录运行。


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
