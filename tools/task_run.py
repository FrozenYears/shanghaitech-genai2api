"""显式任务模式CLI；仅执行操作者提供的任务与固定验收，不修改全局配置。"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

from dotenv import dotenv_values
from tools.task_client import TaskClient
from tools.task_workspace import WorkspaceTools


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:31110')
    parser.add_argument('--model', required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--task-file', type=Path, required=True)
    parser.add_argument('--verify-argv-file', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--api-key-env', default='RELAY_API_KEY')
    parser.add_argument('--allow-python', action='store_true', help='显式允许工作区Python脚本；不是OS沙箱')
    parser.add_argument('--max-requests', type=int, default=30)
    parser.add_argument('--max-seconds', type=float, default=900)
    parser.add_argument('--output', type=Path, required=True, help='必须是工作区外的新JSON文件，包含完整任务历史')
    args = parser.parse_args(argv)
    if args.max_requests < 1 or not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        parser.error('请求次数必须为正整数，时间预算必须为有限正数')
    try:
        workspace = args.workspace.resolve(strict=True)
        output = args.output.resolve()
        if output.exists() or output.is_relative_to(workspace):
            raise ValueError('output须为工作区外的新文件，不能覆盖原证据')
        task = args.task_file.read_text(encoding='utf-8-sig')
        checker_file = args.verify_argv_file.resolve(strict=True)
        if checker_file.is_relative_to(workspace):
            raise ValueError('可信验收配置须在模型工作区外')
        checker = json.loads(checker_file.read_text(encoding='utf-8-sig'))
        if not isinstance(checker, list) or not checker or any(not isinstance(arg, str) for arg in checker):
            raise ValueError('verify-argv-file须为固定字符串argv数组')
        configuration = dotenv_values(args.env_file) if args.env_file else {}
        key = os.environ.get(args.api_key_env) or configuration.get(args.api_key_env) or ''
        adapter = WorkspaceTools(workspace, allow_python=args.allow_python)
        client = TaskClient(args.base_url, key, args.model)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    cancel = threading.Event()
    adapter.deadline = time.monotonic() + args.max_seconds
    def progress(event):
        if event['type'] == 'text':
            print(event['text'], end='', flush=True)
        elif event['type'] in ('tool_start', 'tool_result', 'verification'):
            print('\n' + json.dumps(event, ensure_ascii=False), flush=True)
    try:
        result = client.run(task, adapter.tools, adapter.execute,
                            lambda remaining: adapter.verify(checker, remaining),
                            max_requests=args.max_requests, max_seconds=args.max_seconds,
                            on_event=progress, cancel=cancel)
    except KeyboardInterrupt:
        cancel.set()
        result = {'status': 'cancelled', 'passed': False, 'error': 'operator_interrupt'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n' + json.dumps({'status': result['status'], 'passed': result['passed'],
                             'requests': result.get('requests'), 'output': str(output)}, ensure_ascii=False), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
