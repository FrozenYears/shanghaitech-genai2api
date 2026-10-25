# 上下文探测与速度测量

这两个工具从第一跳仓库根目录运行，入口定义在 [`pixi.toml`](../pixi.toml)。`--help` 只显示参数；实际探测、测速会向学校模型发送请求并消耗额度。本文保留使用说明，历史测速结果仍在 [BENCH_speed.md](BENCH_speed.md)。

## 上下文探测

`context-probe` 在输入最前面放入唯一标记，再要求模型复述，用于观察可用上下文长度及平台拒绝信息。它使用根目录 `.env` 的 `GENAI_TOKEN` 直接访问学校网页后端，不经本地双跳，因此不能据此评价 relay 的历史转换或工具调用稳定性。

```bash
pixi run context-probe --help
pixi run context-probe --model deepseek-pro --tokens 8192,131072,524288
pixi run context-probe --model deepseek-pro --find
pixi run context-probe --all
```

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--model` | 指定平台模型 ID，与 `--all` 选择一种 | 无 |
| `--all` | 扫描可用聊天模型 | 关闭 |
| `--tokens` | 逗号分隔的目标输入规模 | `8192,32768,131072,524288,1048576` |
| `--find` | 倍增后用二分查找可用边界 | 关闭 |
| `--low` / `--high` | 查找区间起点与上限 | `8192` / `2097152` |
| `--max-output` | 每次探测的最大输出 token | `300` |
| `--timeout` | 单次请求超时，秒 | `300` |
| `--token` | JWT 或 `student_id@password`，覆盖环境变量 | 无 |

先选定一个自己有额度的模型。`--find`、`--all` 和较大的目标规模会产生多次大请求；额度拒绝、worker 不可用及网络错误都可能使探测失败，不能把任何失败直接解释为上下文上限。复述成功是标记召回结果，不证明长上下文中的每项业务信息都会被模型正确使用。

## 速度测量

`bench-speed` 调用兼容 Chat Completions 的本地代理，按模型顺序预热、测量并生成 Markdown 报告。默认测第一跳 `31100`；指定第二跳时要同时传它的客户端 Key。

```bash
pixi run bench-speed --help
pixi run bench-speed --models deepseek-pro --runs 3 --max-tokens 512
pixi run bench-speed --models deepseek-pro,Kimi-k3 --output docs/bench-speed-latest.md
pixi run bench-speed --base-url http://127.0.0.1:31110 --api-key replace-with-relay-key --models deepseek-pro
```

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--base-url` | 代理地址，不带 `/v1` | `http://127.0.0.1:31100` |
| `--api-key` | 对应代理的本地 Key；也可用 `API_KEY` 环境变量 | 无 |
| `--models` | 逗号分隔的平台模型 ID；未传时读取模型列表 | 全部可用聊天模型 |
| `--runs` | 每个模型正式测量次数 | `3` |
| `--warmup` | 每个模型预热次数，不计入结果 | `1` |
| `--max-tokens` | 单次请求输出上限 | `512` |
| `--timeout` | 单次请求超时，秒 | `300` |
| `--prompt` | 测试用用户消息 | 从 1 数到 200 |
| `--output` | Markdown 输出文件 | 只打印到终端 |

报告中的三个指标：

- 首字时间（TTFT）：请求发出到第一个正文或思考增量，不是 SSE 角色帧或心跳的时间。
- 正文首字：请求发出到第一个正文增量。
- 首字后输出速度：输出 token 数除以总耗时减首字等待；思考计入输出，token 来自代理估算或同规则的本地估算。

各次测量取中位数，额度不足和上游不可用保留为失败。不同时间、负载、模型及思考设置会影响结果，不能仅凭一份历史报告判断重构的性能收益。`--output` 会覆盖同名文件，保留历史时请另选路径，不要覆盖已提交的历史记录。

实际工具入口见 [`context_probe.py`](../tools/context_probe.py) 与 [`bench_speed.py`](../tools/bench_speed.py)。长期 agent 任务的执行、独立验收与自动反馈另见[根 README](../README.md)，与这里的吞吐/上下文测量不是同一项测试。
