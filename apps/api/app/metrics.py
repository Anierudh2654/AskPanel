from datetime import datetime
from statistics import mean
from typing import Any


def compute_session_metrics(started_at: datetime, ended_at: datetime, events: list[dict[str, Any]], messages: list[dict[str, Any]], calls: list[dict[str, Any]], condition: str) -> dict[str, Any]:
    idle_seconds = 0.0
    blurred_seconds = 0.0
    open_idle: datetime | None = None
    open_blur: datetime | None = None
    scroll_distance = 0
    clarifications = 0
    feature_times: list[float] = []
    def event_time(item: dict[str, Any]) -> datetime:
        value = item.get("client_ts") or item.get("server_ts") or started_at
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value

    for event in sorted(events, key=event_time):
        kind = event.get("type")
        timestamp = event.get("client_ts") or event.get("server_ts")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not isinstance(timestamp, datetime):
            continue
        if kind == "idle_start": open_idle = timestamp
        elif kind == "idle_end" and open_idle:
            idle_seconds += max(0.0, (timestamp - open_idle).total_seconds()); open_idle = None
        elif kind == "tab_blur": open_blur = timestamp
        elif kind == "tab_focus" and open_blur:
            blurred_seconds += max(0.0, (timestamp - open_blur).total_seconds()); open_blur = None
        elif kind == "scroll": scroll_distance += abs(int(event.get("payload", {}).get("dy", 0)))
        elif kind == "selection_made": feature_times.append((timestamp - started_at).total_seconds())
        elif kind in {"thread_create", "thread_message_send"} and condition == "B": clarifications += 1
    raw_time = max(0.0, (ended_at - started_at).total_seconds())
    active_calls = [call for call in calls if call.get("purpose") in {"main", "thread"}]
    input_tokens = sum(int(call.get("input_tokens", 0)) for call in active_calls)
    output_tokens = sum(int(call.get("output_tokens", 0)) for call in active_calls)
    clarification_calls = [call for call in active_calls if call.get("purpose") == "thread"]
    result = {
        "task_time_s": max(0.0, raw_time - idle_seconds - blurred_seconds),
        "task_time_raw_s": raw_time,
        "turns_main": sum(1 for message in messages if message.get("role") == "user" and message.get("active", True)),
        "tokens_in": input_tokens,
        "tokens_out": output_tokens,
        "tokens_total": input_tokens + output_tokens,
        "cost_usd": round(sum(float(call.get("cost_usd", 0)) for call in active_calls), 6),
        "tokens_per_clarification": round(sum(int(call.get("input_tokens", 0)) + int(call.get("output_tokens", 0)) for call in clarification_calls) / clarifications, 2) if clarifications else None,
        "main_chat_length_msgs": sum(1 for message in messages if message.get("active", True)),
        "scroll_distance_px": scroll_distance,
        "ttft_ms_mean": round(mean([call["ttft_ms"] for call in active_calls if call.get("ttft_ms") is not None]), 2) if any(call.get("ttft_ms") is not None for call in active_calls) else None,
        "latency_ms_mean": round(mean([call["latency_ms"] for call in active_calls if call.get("latency_ms") is not None]), 2) if any(call.get("latency_ms") is not None for call in active_calls) else None,
        "feature_discovery_s": min(feature_times) if feature_times else None,
        "n_clarifications": clarifications,
    }
    return result
