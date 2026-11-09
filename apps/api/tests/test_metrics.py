from datetime import datetime, timedelta, timezone

from app.main import app  # noqa: F401
from app.metrics import compute_session_metrics
from app.study import score_survey


def test_sus_scoring_is_independent_of_json_key_order():
    responses = {f"q{index}": 3 for index in range(10, 0, -1)}
    assert score_survey("sus", responses) == {"sus": 50.0}


def test_metrics_normalize_timestamp_strings_and_exclude_idle_blur():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=10)
    events = [
        {"type": "idle_start", "client_ts": (start + timedelta(seconds=2)).isoformat()},
        {"type": "idle_end", "client_ts": start + timedelta(seconds=4)},
        {"type": "tab_blur", "client_ts": (start + timedelta(seconds=5)).isoformat()},
        {"type": "tab_focus", "client_ts": start + timedelta(seconds=6)},
        {"type": "scroll", "client_ts": start + timedelta(seconds=7), "payload": {"dy": -30}},
        {"type": "thread_create", "client_ts": (start + timedelta(seconds=8)).isoformat()},
    ]
    result = compute_session_metrics(
        start, end, events,
        [{"role": "user", "active": True}],
        [{"purpose": "thread", "input_tokens": 60, "output_tokens": 40, "ttft_ms": 50, "latency_ms": 150, "cost_usd": 0.01}],
        "B",
    )
    assert result["task_time_s"] == 7
    assert result["scroll_distance_px"] == 30
    assert result["n_clarifications"] == 1
    assert result["tokens_per_clarification"] == 100
