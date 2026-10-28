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

