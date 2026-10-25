import runpy
import sys
from pathlib import Path

import pytest

from config import Config


@pytest.mark.parametrize("value", [-1, 31, True, False, 1.5, "3", float("nan"), float("inf")])
def test_empty_continuations_rejects_invalid_budget(value):
    with pytest.raises(ValueError):
        Config(None, 0, None, False, empty_continuations=value)


@pytest.mark.parametrize("value", [0, -1, True, False, float("nan"), float("inf"), -float("inf"), "600"])
def test_tool_generation_window_rejects_invalid_timeout(value):
    with pytest.raises(ValueError):
        Config(None, 0, None, False, tool_request_timeout_seconds=value)


@pytest.mark.parametrize("budget", [0, 1, 8, 10, 30])
@pytest.mark.parametrize("timeout", [0.5, 1, 600.0])
def test_explicit_valid_recovery_limits_are_accepted(budget, timeout):
    config = Config(None, 0, None, False, empty_continuations=budget, tool_request_timeout_seconds=timeout)
    assert config.empty_continuations == budget
    assert config.tool_request_timeout_seconds == timeout


@pytest.mark.parametrize("value", [-1, 11, True, False, 1.5, "3", float("nan"), float("inf")])
def test_tool_repair_rejects_invalid_budget(value):
    with pytest.raises(ValueError):
        Config(None, 0, None, False, tool_repair_attempts=value)


@pytest.mark.parametrize("budget", [0, 1, 3, 10])
def test_tool_repair_accepts_valid_budget(budget):
    assert Config(None, 0, None, False, tool_repair_attempts=budget).tool_repair_attempts == budget


def test_defaults_and_positional_timeout_remain_compatible():
    config = Config(None, 0, None, False)
    assert (config.empty_continuations, config.tool_repair_attempts, config.tool_request_timeout_seconds) == (8, 3, 900)
    config = Config(None, 0, None, False, "both", 2, 123)
    assert config.tool_request_timeout_seconds == 123
    assert config.tool_repair_attempts == 3


@pytest.mark.parametrize("source", ["cli", "env"])
@pytest.mark.parametrize("value", ["-1", "11", "bad"])
def test_invalid_repair_budget_is_rejected_before_login(monkeypatch, source, value):
    from auth.token_manager import TokenManager
    import dotenv

    logins = []
    monkeypatch.setattr(TokenManager, "initial_login", lambda self: logins.append(True))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args: None)
    args = ["main.py", "--token", "test@credential"]
    if source == "cli":
        args += ["--tool-repair-attempts", value]
    else:
        monkeypatch.setenv("GENAI_TOOL_REPAIR_ATTEMPTS", value)
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "main.py"), run_name="config_policy_test")
    assert exc.value.code == 2
    assert logins == []
