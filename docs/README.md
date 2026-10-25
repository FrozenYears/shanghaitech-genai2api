# 文档导航

当前安装、双跳接入与支持边界以[根 README](../README.md)为准。现行路线只连接学校网页：客户端 → relay（31110，默认 native）→ 第一跳（31100，网页适配与严格解析）→ 学校 GenAI 网页后端，不提供独立学校 API Key 路线。

## 按用途查阅

| 用途 | 文档 | 阅读口径 |
| --- | --- | --- |
| 入门与功能用法 | [根 README](../README.md) | 完整环境配置、Windows 启动、默认折叠的客户端详情、附件/搜索/思考和已知限制。 |
| 可选任务监督 | [omp 任务监督器](USAGE.md) | 验收命令、自动反馈预算、工作目录要求和停止条件。不会自动接管普通客户端。 |
| 当前架构与重构对照 | [原生优先阶段说明](STAGE_NATIVE_FIRST.md) | 两跳职责、native/text、流式与原子工具批次、第一跳恢复预算及可选 omp 监督器；第五节区分原作者、个人 fork 与重构前冻结提交，第七至九节是历史记录。 |
| 验收记录 | [模型与工具能力测试报告](TEST_REPORT_deepseek_pro.md) | 按阶段保留 DeepSeek/Kimi 真实工具闭环、失败、恢复与长期任务验收；顶部是最近已记录的验收，不是本轮文档整理重新运行。 |
| 历史取证：网页协议 | [GenAI 网页能力调查](GENAI_WEB_CAPABILITIES.md) | 2026-09-17 的平台观察与请求协议证据；不能替代当前使用说明。 |
| 历史取证：功能接入 | [网页功能接入验证](FEATURE_VALIDATION.md) | 图片、文档、搜索与思考的早期实现及验证限制，保留当时成功和失败。 |
| 历史取证：真实客户端 | [Pi 实际场景验证](PI_REAL_WORLD_VALIDATION.md) | 2026-09-17 的隔离配置、图片延迟/超时及 PDF 显式上传区别，不代表所有当前客户端已复测。 |
| 辅助工具用法 | [上下文探测与速度测量](TOOLS.md) | 当前命令、完整参数、认证位置、输出指标及额度/覆盖风险；与历史测速结果分开。 |
| 测速记录 | [首字时间与输出速度](BENCH_speed.md) | 2026-09-12 的历史样本，包含额度与上游失败；不是重构前后受控性能对照或本轮测速。 |
| 工作日志 | [本地工作日志](WORK_LOG.md) | 归档操作、实现顺序与各阶段部署/验证记录；历史 PID、端口状态和测试计数不代表当前运行状态。 |

## 原始摘要与可执行入口

[validation/](../validation/) 保留机器可读摘要、验证样本和可执行 runner，不随 Markdown 归档移动：

- [automatic-recovery-2026-10-25.json](../validation/automatic-recovery-2026-10-25.json)：双类别恢复与自动监督正式长测摘要。
- [recovery-budget-2026-10-25.json](../validation/recovery-budget-2026-10-25.json)、[relay-rootcause-2026-10-25.json](../validation/relay-rootcause-2026-10-25.json)：此前恢复预算与根因修复的阶段性证据。
- [omp-endurance-2026-10-25.json](../validation/omp-endurance-2026-10-25.json)、[kimi-endurance-2026-10-24.json](../validation/kimi-endurance-2026-10-24.json)、[local-model-tools-2026-10-24.json](../validation/local-model-tools-2026-10-24.json)：历史持续开发与本地工具闭环摘要。
- [native-first-endurance/](../validation/native-first-endurance/)：长期任务 runner、独立验收及可选 omp 监督器入口。启动方法以根 README 为准；监督器不是所有客户端自动启用的反代功能。
- [pi-2026-09-17/](../validation/pi-2026-09-17/)：Pi 隔离清单、请求字段、场景结果、样本及显式文档上传脚本。

原始 `.validation-runtime/` 材料保持原位，不作为稳定文档入口。本轮整理只检查文档引用、命令帮助与服务健康，并调用指定Gemini子会话编辑README；没有重跑GenAI长期任务、附件验收或测速。上表和摘要中的历史数值不重标为本轮通过结果。

## 路径约定

文档中的可执行命令默认在仓库根目录运行；归档仅改变 Markdown 的位置，不改变命令的工作目录。相邻文档使用同目录相对链接，源代码与 `validation/` 使用 `../`。固定 40 位提交的 GitHub 链接仍指向当时快照，保留旧路径；跨独立 relay 仓库的当前入口使用明确的 GitHub 分支链接。
