import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from auth.cas_login import LoginError
from auth.token_manager import TokenManager
from config import Config
from app import create_app

load_dotenv(Path(__file__).resolve().parent / ".env")

parser = argparse.ArgumentParser(description='GenAI Flask API Server')
parser.add_argument('--token', type=str, default=None,
                    help='JWT token (eyJ...) or student_id@password for auto-login (or set GENAI_TOKEN)')
parser.add_argument('--port', type=int, default=None,
                    help='Flask server port (or set PORT; default: 31100)')
parser.add_argument('--debug', action='store_true',
                    help='Enable debug logging')
parser.add_argument('--api-key', type=str, default=None,
                    help='API key for client authentication (or set API_KEY env var)')
parser.add_argument('--api-format', type=str, default=None,
                    choices=['openai', 'anthropic', 'both'],
                    help='API format: openai, anthropic, or both (default: API_FORMAT env var or both)')
parser.add_argument('--empty-continuations', type=int, default=None,
                    help='Extra generations after completely empty tool turns, 0-10 (or GENAI_EMPTY_CONTINUATIONS; default: 3)')
parser.add_argument('--tool-request-timeout', type=float, default=None,
                    help='Shared upstream generation deadline in seconds for a tool request (or GENAI_TOOL_REQUEST_TIMEOUT_SECONDS; default: 600)')
args = parser.parse_args()
token_input = args.token or os.environ.get("GENAI_TOKEN")

if not token_input:
    parser.error("token is required via --token or GENAI_TOKEN")

logging.basicConfig(
    level=logging.DEBUG if args.debug else logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

try:
    token_manager = TokenManager(token_input)
except ValueError as e:
    logger.error("Failed to initialize token: %s", e)
    sys.exit(1)

try:
    config = Config(
        token_manager=token_manager,
        port=args.port if args.port is not None else int(os.environ.get("PORT", "31100")),
        api_key=args.api_key or os.environ.get("API_KEY"),
        debug=args.debug,
        api_format=args.api_format or os.environ.get("API_FORMAT", "both"),
        empty_continuations=(args.empty_continuations if args.empty_continuations is not None
                             else int(os.environ.get("GENAI_EMPTY_CONTINUATIONS", "3"))),
        tool_request_timeout_seconds=(args.tool_request_timeout if args.tool_request_timeout is not None
                                      else float(os.environ.get("GENAI_TOOL_REQUEST_TIMEOUT_SECONDS", "600"))),
    )
except ValueError as exc:
    parser.error(str(exc))

# 先拒绝本地无效预算，再进行实际网页登录。
try:
    token_manager.initial_login()
except (LoginError, ValueError) as exc:
    logger.error("Failed to initialize token: %s", exc)
    sys.exit(1)

app = create_app(config)

if __name__ == '__main__':
    if config.api_format == "anthropic":
        api_format_name = "Anthropic (/v1/messages)"
    elif config.api_format == "openai":
        api_format_name = "OpenAI (/v1/chat/completions + /v1/responses)"
    else:
        api_format_name = "OpenAI (/v1/chat/completions + /v1/responses) + Anthropic (/v1/messages)"
    logger.info("Starting GenAI proxy on port %d", config.port)
    logger.info("API Format: %s, Auth: %s, Token mode: %s",
                api_format_name, "enabled" if config.api_key else "disabled",
                token_manager.mode)
    logger.info("Tool request policy: empty_continuations=%d total_timeout_seconds=%s",
                config.empty_continuations, config.tool_request_timeout_seconds)
    app.run(host='0.0.0.0', port=config.port, debug=False)
