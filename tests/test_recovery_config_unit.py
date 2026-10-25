import pytest

from config import Config


@pytest.mark.parametrize("value", [-1, 11, True, False, 1.5, "3", float("nan"), float("inf")])
def test_empty_continuations_rejects_invalid_budget(value):
    with pytest.raises(ValueError):
        Config(None, 0, None, False, empty_continuations=value)


@pytest.mark.parametrize("value", [0, -1, True, False, float("nan"), float("inf"), -float("inf"), "600"])
def test_tool_generation_window_rejects_invalid_timeout(value):
    with pytest.raises(ValueError):
        Config(None, 0, None, False, tool_request_timeout_seconds=value)


@pytest.mark.parametrize("budget", [0, 1, 3, 10])
@pytest.mark.parametrize("timeout", [0.5, 1, 600.0])
def test_explicit_valid_recovery_limits_are_accepted(budget, timeout):
    config = Config(None, 0, None, False, empty_continuations=budget, tool_request_timeout_seconds=timeout)
    assert config.empty_continuations == budget
    assert config.tool_request_timeout_seconds == timeout
