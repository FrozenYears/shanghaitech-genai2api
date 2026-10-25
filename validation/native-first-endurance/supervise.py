"""使用已有 GenAI 配置监督真实 omp；验收命令必须由操作者显式信任。"""
import argparse
import json
from pathlib import Path
import tempfile
import time

from omp_client import OmpClient
from supervisor import feedback_budget, positive, run_checker, save_json, supervise


def parser():
    cli = argparse.ArgumentParser(description=__doc__, epilog='默认不向 agent 提供 bash；--allow-bash-pattern 只约束 omp 工具权限，不是 OS 沙箱。验收器由本地进程运行，请将可信验收文件放在 agent 工作目录之外。--verify-command 必须放在所有其他参数之后。')
    cli.add_argument('--model', choices=['deepseek-pro', 'Kimi-k3'], required=True)
    cli.add_argument('--workspace', type=Path, required=True)
    cli.add_argument('--task-file', type=Path, required=True)
    cli.add_argument('--artifact-dir', type=Path)
    cli.add_argument('--max-feedback', type=feedback_budget, default=8)
    cli.add_argument('--max-minutes', type=positive, default=45)
    cli.add_argument('--turn-timeout', type=positive, default=1200)
    cli.add_argument('--verify-timeout', type=positive, default=120)
    cli.add_argument('--output-limit', type=int, default=16384)
    cli.add_argument('--allow-bash-pattern', help='显式授权一个 omp bash 匹配模式；未匹配命令全部拒绝')
    checker = cli.add_mutually_exclusive_group(required=True)
    checker.add_argument('--verify-argv-file', type=Path, help='可信 UTF-8 JSON 字符串数组；只读取一次')
    checker.add_argument('--verify-command', nargs=argparse.REMAINDER, help='可信 argv，不经过 shell')
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        cli.error('--workspace 必须是已有目录')
    if not 256 <= args.output_limit <= 1048576:
        cli.error('--output-limit 必须在 256..1048576 之间')
    if args.allow_bash_pattern is not None and not args.allow_bash_pattern.strip():
        cli.error('--allow-bash-pattern 不能是空字符串')
    try:
        task = args.task_file.read_text(encoding='utf-8-sig')
        checker = json.loads(args.verify_argv_file.read_text(encoding='utf-8-sig')) if args.verify_argv_file else args.verify_command
        if not task.strip():
            raise ValueError('task-file 不能为空')
        if not isinstance(checker, list) or not checker or any(not isinstance(s, str) or '\0' in s for s in checker) or not checker[0]:
            raise ValueError('验收 argv 必须是非空 JSON 字符串数组')
        if Path(checker[0]).suffix.lower() in ('.bat', '.cmd'):
            raise ValueError('验收命令不能是 bat/cmd，请使用真实可执行程序')
    except (OSError, ValueError) as exc:
        cli.error(str(exc))
    if args.artifact_dir:
        artifact_dir = args.artifact_dir.resolve()
        if artifact_dir.exists():
            cli.error('--artifact-dir 必须是新目录，避免覆盖历史证据')
    else:
        artifact_dir = None
    if artifact_dir and (artifact_dir == workspace or workspace in artifact_dir.parents):
        cli.error('artifact-dir 必须在 agent 工作目录之外')
    if artifact_dir:
        artifact_dir.mkdir(parents=True)
    else:
        artifact_dir = Path(tempfile.mkdtemp(prefix='omp-supervise-'))
    # 固定 argv 和任务快照；模型不能通过反馈修改验收命令。
    save_json(artifact_dir / 'invocation.json', {'model': args.model, 'workspace': str(workspace),
              'verify_argv': checker, 'allow_bash_pattern': args.allow_bash_pattern,
              'max_feedback': args.max_feedback, 'max_minutes': args.max_minutes,
              'turn_timeout': args.turn_timeout, 'verify_timeout': args.verify_timeout,
              'output_limit': args.output_limit, 'os_sandbox': False})
    (artifact_dir / 'task.txt').write_text(task, encoding='utf-8')
    deadline = time.monotonic() + args.max_minutes * 60
    result = None
    client = None
    print(json.dumps({'artifact_dir': str(artifact_dir), 'bash_enabled': bool(args.allow_bash_pattern), 'os_sandbox': False}), flush=True)
    try:
        client = OmpClient(workspace, artifact_dir, args.model, command_pattern=args.allow_bash_pattern)
        identity = client.assert_model()
        save_json(artifact_dir / 'identity.json', identity)
        prompt = (task + '\n仅在当前工作目录增量工作。先用glob/grep/read检查当前文件；真实使用工具，不要模拟结果。'
                  '独立验收由监督器本地运行，禁止修改验收器或伪造验收结果。'
                  + ('bash 只允许操作者显式授权的模式：' + args.allow_bash_pattern if args.allow_bash_pattern else '未授权bash，使用read/write/edit/glob/grep完成任务，不要请求扩大权限。'))
        result = supervise(client, prompt,
                           lambda remaining: run_checker(checker, workspace, timeout=min(args.verify_timeout, remaining), output_limit=args.output_limit),
                           artifact_dir=artifact_dir, max_feedback=args.max_feedback, deadline=deadline,
                           turn_timeout=args.turn_timeout,
                           on_progress=lambda state: print(json.dumps({k: state[k] for k in ('passed', 'automatic_feedback_count', 'stagnation_count', 'stop_reason')}, ensure_ascii=False), flush=True))
    except Exception as exc:
        result = {'passed': False, 'stop_reason': getattr(exc, 'reason', 'controller_failure'),
                  'error': type(exc).__name__ + ': ' + str(exc), 'human_interventions': 0}
        save_json(artifact_dir / 'result.json', result)
        save_json(artifact_dir / 'checkpoint.json', {'workspace': str(workspace),
                  'sessions_dir': str(artifact_dir / 'sessions'), 'session': getattr(client, 'last_state', None),
                  'stop_reason': result['stop_reason'], 'automatic_cross_process_resume': False})
    finally:
        if client:
            client.close()
    print(json.dumps({'passed': result['passed'], 'stop_reason': result['stop_reason'], 'artifact_dir': str(artifact_dir)}, ensure_ascii=False), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
