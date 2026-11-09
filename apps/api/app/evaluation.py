import asyncio
import csv
import json
import random
import statistics
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.config import settings
from app.services.context import (
    Prompt,
    build_main_context,
    build_semantic_thread_context,
    build_thread_context,
    estimate_tokens,
)
from app.services.llm.provider import stream_completion

Completion = Callable[[Prompt], Any]
RATING_FIELDS = ("relevance", "correctness", "faithfulness", "resolution")


async def case_to_prompt(case: dict[str, Any], strategy: str, token_budget: int) -> tuple[Prompt, int, int]:
    history = case["history"]
    parent_index = int(case.get("parent_index", len(history) - 1))
    parent = history[parent_index]
    if parent.get("role") != "assistant":
        raise ValueError(f"Case {case.get('id')} parent_index must point to an assistant message")
    messages = [
        SimpleNamespace(
            id=f"{case['id']}-m{index + 1}", seq=index + 1,
            role=item["role"], content=item["content"], active=True,
        )
        for index, item in enumerate(history[: parent_index + 1])
    ]
    conversation = SimpleNamespace(condition="local", system_context=case.get("system_context"))
    if strategy == "sequential":
        next_message = SimpleNamespace(
            id=f"{case['id']}-sequential-question",
            seq=messages[-1].seq + 1,
            role="user",
            content=case["question"],
            active=True,
        )
        sequential_history = [*messages, next_message]
        prompt = build_main_context(conversation, sequential_history, next_message.seq)
        estimate = estimate_tokens(prompt.system) + sum(
            estimate_tokens(item["content"]) for item in prompt.messages
        )
        return prompt, estimate, 0
    if strategy not in {"full", "relevant", "semantic"}:
        raise ValueError(f"Unknown evaluation strategy: {strategy}")
    parent_message = messages[-1]
    anchor = case["anchor_text"]
    start = parent["content"].find(anchor)
    if start < 0:
        raise ValueError(f"Case {case['id']} anchor_text is not present in its parent assistant message")
    thread = SimpleNamespace(
        parent_message_id=parent_message.id,
        anchor_text=anchor,
        start_offset=start,
        end_offset=start + len(anchor),
        prefix=parent["content"][max(0, start - 40):start],
        suffix=parent["content"][start + len(anchor):start + len(anchor) + 40],
    )
    branch_messages = [SimpleNamespace(seq=1, role="user", content=case["question"])]
    if strategy == "semantic":
        prompt, embedding_input_tokens = await build_semantic_thread_context(
            thread,
            conversation,
            messages,
            branch_messages,
            context_token_budget=token_budget,
        )
    else:
        prompt = build_thread_context(
            thread,
            conversation,
            messages,
            branch_messages,
            context_strategy=strategy,
            context_token_budget=token_budget,
        )
        embedding_input_tokens = 0
    context_tokens_estimate = estimate_tokens(prompt.system) + sum(
        estimate_tokens(item["content"]) for item in prompt.messages[:-1]
    )
    return prompt, context_tokens_estimate, embedding_input_tokens


async def complete_prompt(prompt: Prompt) -> dict[str, Any]:
    answer_parts: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    started = time.perf_counter()
    async for item in stream_completion(prompt):
        if item["type"] == "delta":
            answer_parts.append(item["text"])
        elif item["type"] == "usage":
            usage.update({key: int(item.get(key, 0) or 0) for key in usage})
    return {
        "answer": "".join(answer_parts),
        **usage,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


async def run_paired_evaluation(
    cases: Iterable[dict[str, Any]],
    *,
    strategies: tuple[str, ...] = ("full", "relevant"),
    token_budget: int = 1800,
    completion: Completion = complete_prompt,
    order_seed: int = 0,
) -> list[dict[str, Any]]:
    if len(strategies) < 2 or len(set(strategies)) != len(strategies):
        raise ValueError("Paired evaluation needs at least two distinct strategies")
    original_temperature = settings.llm_temperature
    settings.llm_temperature = 0.0
    rows: list[dict[str, Any]] = []
    order_rng = random.Random(order_seed)
    try:
        for case in cases:
            order = list(strategies)
            order_rng.shuffle(order)
            for strategy in order:
                prompt, estimated_context_tokens, embedding_input_tokens = await case_to_prompt(case, strategy, token_budget)
                started = time.perf_counter()
                response = await completion(prompt)
                elapsed = round((time.perf_counter() - started) * 1000)
                rows.append({
                    "case_id": case["id"],
                    "strategy": strategy,
                    "provider": settings.llm_provider,
                    "model": settings.llm_model,
                    "prompt_version": settings.prompt_version,
                    "temperature": 0.0,
                    "context_token_budget": token_budget,
                    "context_tokens_estimate": estimated_context_tokens,
                    "prompt_chars": len(prompt.system) + sum(len(item["content"]) for item in prompt.messages),
                    "input_tokens": int(response.get("input_tokens", 0)),
                    "embedding_input_tokens": embedding_input_tokens,
                    "total_input_tokens": int(response.get("input_tokens", 0)) + embedding_input_tokens,
                    "output_tokens": int(response.get("output_tokens", 0)),
                    "latency_ms": int(response.get("latency_ms", elapsed)),
                    "answer": response["answer"],
                    "reference_answer": case["reference_answer"],
                    "question": case["question"],
                })
    finally:
        settings.llm_temperature = original_temperature
    return rows


def write_evaluation_files(rows: list[dict[str, Any]], output_dir: Path, *, blind_seed: int = 0) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    rng = random.Random(blind_seed)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["case_id"], []).append(row)
    ratings_path = output_dir / "ratings_blinded.csv"
    key: dict[str, dict[str, str]] = {}
    with ratings_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["pair_id", "sample_id", "rater_id", "question", "reference_answer", "answer", *RATING_FIELDS, "notes"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case_id, pair in sorted(grouped.items()):
            if len(pair) not in {2, 3, 4}:
                raise ValueError(f"Expected two, three, or four strategy outputs for case {case_id}")
            blinded = ["X", "Y", "Z", "W"][:len(pair)]
            rng.shuffle(blinded)
            key[case_id] = {}
            for sample_id, row in zip(blinded, pair, strict=True):
                key[case_id][sample_id] = row["strategy"]
                writer.writerow({
                    "pair_id": case_id,
                    "sample_id": sample_id,
                    "rater_id": "",
                    "question": row["question"],
                    "reference_answer": row["reference_answer"],
                    "answer": row["answer"],
                    **{field: "" for field in RATING_FIELDS},
                    "notes": "",
                })
    (output_dir / "blind_key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")


def paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def total_input_tokens(row: dict[str, Any]) -> int:
        return int(row.get("total_input_tokens", row.get("input_tokens", 0)))

    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], {})[row["strategy"]] = row
    strategies = sorted({row["strategy"] for row in rows})
    pairs = [pair for pair in by_case.values() if all(strategy in pair for strategy in strategies)]
    pairwise_comparisons = []
    for baseline in strategies:
        for comparison in strategies:
            if baseline == comparison:
                continue
            matched = [pair for pair in pairs if baseline in pair and comparison in pair]
            token_differences = [
                total_input_tokens(pair[baseline]) - total_input_tokens(pair[comparison])
                for pair in matched
            ]
            savings = [
                100.0 * difference / total_input_tokens(pair[baseline])
                for pair, difference in zip(matched, token_differences, strict=True)
                if total_input_tokens(pair[baseline]) > 0
            ]
            pairwise_comparisons.append({
                "baseline": baseline,
                "comparison": comparison,
                "paired_cases": len(matched),
                "mean_total_input_tokens_baseline": statistics.mean([total_input_tokens(pair[baseline]) for pair in matched]) if matched else None,
                "mean_total_input_tokens_comparison": statistics.mean([total_input_tokens(pair[comparison]) for pair in matched]) if matched else None,
                "mean_total_input_tokens_saved": statistics.mean(token_differences) if token_differences else None,
                "mean_input_token_savings_percent": statistics.mean(savings) if savings else None,
                "input_token_savings_95pct_bootstrap_ci": bootstrap_mean_ci(savings),
                "input_token_savings_wilcoxon_p_value": paired_wilcoxon(token_differences),
                "mean_latency_ms_baseline": statistics.mean([pair[baseline]["latency_ms"] for pair in matched]) if matched else None,
                "mean_latency_ms_comparison": statistics.mean([pair[comparison]["latency_ms"] for pair in matched]) if matched else None,
            })

    full_relevant = next((item for item in pairwise_comparisons if item["baseline"] == "full" and item["comparison"] == "relevant"), None)
    token_savings = [
        100.0 * (total_input_tokens(pair["full"]) - total_input_tokens(pair["relevant"])) / total_input_tokens(pair["full"])
        for pair in by_case.values()
        if "full" in pair and "relevant" in pair and total_input_tokens(pair["full"]) > 0
    ]
    token_differences = [
        total_input_tokens(pair["full"]) - total_input_tokens(pair["relevant"])
        for pair in by_case.values() if "full" in pair and "relevant" in pair
    ]
    token_test = paired_wilcoxon(token_differences)
    return {
        "paired_cases": len(pairs),
        "provider": rows[0]["provider"] if rows else None,
        "model": rows[0]["model"] if rows else None,
        "mean_input_tokens_full": full_relevant["mean_total_input_tokens_baseline"] if full_relevant else None,
        "mean_input_tokens_relevant": full_relevant["mean_total_input_tokens_comparison"] if full_relevant else None,
        "mean_input_token_savings_percent": statistics.mean(token_savings) if token_savings else None,
        "mean_input_tokens_saved": statistics.mean(token_differences) if token_differences else None,
        "input_token_savings_95pct_bootstrap_ci": bootstrap_mean_ci(token_savings),
        "input_token_savings_wilcoxon_p_value": token_test,
        "mean_latency_ms_full": full_relevant["mean_latency_ms_baseline"] if full_relevant else None,
        "mean_latency_ms_relevant": full_relevant["mean_latency_ms_comparison"] if full_relevant else None,
        "strategy_metrics": {
            strategy: {
                "mean_input_tokens": statistics.mean([pair[strategy]["input_tokens"] for pair in pairs]) if pairs else None,
                "mean_embedding_input_tokens": statistics.mean([pair[strategy].get("embedding_input_tokens", 0) for pair in pairs]) if pairs else None,
                "mean_total_input_tokens": statistics.mean([total_input_tokens(pair[strategy]) for pair in pairs]) if pairs else None,
                "mean_output_tokens": statistics.mean([pair[strategy]["output_tokens"] for pair in pairs]) if pairs else None,
                "mean_latency_ms": statistics.mean([pair[strategy]["latency_ms"] for pair in pairs]) if pairs else None,
            }
            for strategy in strategies
        },
        "pairwise_comparisons": pairwise_comparisons,
        "quality_status": "not scored; complete blinded manual ratings before making quality claims",
    }


def analyze_blinded_ratings(results_path: Path, ratings_path: Path, key_path: Path) -> dict[str, Any]:
    rows = json.loads(results_path.read_text(encoding="utf-8"))
    key = json.loads(key_path.read_text(encoding="utf-8"))
    strategies = sorted({row["strategy"] for row in rows})
    scores: dict[str, dict[str, list[float]]] = {strategy: {} for strategy in strategies}
    raw_ratings: dict[tuple[str, str, str], dict[str, float]] = {}
    raters: set[str] = set()
    with ratings_path.open(newline="", encoding="utf-8-sig") as handle:
        for rating in csv.DictReader(handle):
            strategy = key[rating["pair_id"]][rating["sample_id"]]
            rater_id = rating.get("rater_id", "").strip()
            if rater_id:
                raters.add(rater_id)
            for field in RATING_FIELDS:
                value = rating.get(field, "").strip()
                if value:
                    numeric = float(value)
                    scores[strategy].setdefault(rating["pair_id"], []).append(numeric)
                    if rater_id:
                        raw_ratings.setdefault((rating["pair_id"], rating["sample_id"], rater_id), {})[field] = numeric
    quality_comparisons = []
    for baseline in strategies:
        for comparison in strategies:
            if baseline == comparison:
                continue
            common_pairs = sorted(set(scores[baseline]) & set(scores[comparison]))
            differences = [
                statistics.mean(scores[comparison][pair_id]) - statistics.mean(scores[baseline][pair_id])
                for pair_id in common_pairs
            ]
            quality_comparisons.append({
                "baseline": baseline,
                "comparison": comparison,
                "rated_pairs": len(differences),
                "mean_quality_difference_comparison_minus_baseline": statistics.mean(differences) if differences else None,
                "quality_difference_95pct_bootstrap_ci": bootstrap_mean_ci(differences),
                "quality_wilcoxon_p_value": paired_wilcoxon(differences),
            })
    relevant_vs_full = next((
        item for item in quality_comparisons
        if item["baseline"] == "full" and item["comparison"] == "relevant"
    ), None)
    summary = paired_summary(rows)
    summary["rated_pairs"] = relevant_vs_full["rated_pairs"] if relevant_vs_full else 0
    summary["mean_quality_difference_relevant_minus_full"] = relevant_vs_full["mean_quality_difference_comparison_minus_baseline"] if relevant_vs_full else None
    summary["quality_difference_95pct_bootstrap_ci"] = relevant_vs_full["quality_difference_95pct_bootstrap_ci"] if relevant_vs_full else None
    summary["quality_wilcoxon_p_value"] = relevant_vs_full["quality_wilcoxon_p_value"] if relevant_vs_full else None
    summary["quality_pairwise_comparisons"] = quality_comparisons
    ordered_raters = sorted(raters)
    agreement: dict[str, float | None] = {}
    if len(ordered_raters) >= 2:
        first_rater, second_rater = ordered_raters[:2]
        for field in RATING_FIELDS:
            first_scores: list[float] = []
            second_scores: list[float] = []
            for pair_id, blind_samples in key.items():
                for sample_id in blind_samples:
                    first = raw_ratings.get((pair_id, sample_id, first_rater), {}).get(field)
                    second = raw_ratings.get((pair_id, sample_id, second_rater), {}).get(field)
                    if first is not None and second is not None:
                        first_scores.append(first)
                        second_scores.append(second)
            agreement[field] = quadratic_weighted_kappa(first_scores, second_scores)
    summary["raters"] = ordered_raters
    summary["quadratic_weighted_kappa_by_dimension"] = agreement
    return summary


def bootstrap_mean_ci(values: list[float], *, resamples: int = 10000, seed: int = 19) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    means = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(resamples)]
    means.sort()
    return [means[int(0.025 * resamples)], means[min(resamples - 1, int(0.975 * resamples))]]


def paired_wilcoxon(differences: list[float]) -> float | None:
    nonzero = [value for value in differences if value != 0]
    if len(nonzero) < 2:
        return None
    from scipy.stats import wilcoxon

    return float(wilcoxon(nonzero, alternative="two-sided").pvalue)


def quadratic_weighted_kappa(first: list[float], second: list[float], *, minimum: int = 1, maximum: int = 5) -> float | None:
    if len(first) != len(second) or len(first) < 2:
        return None
    categories = list(range(minimum, maximum + 1))
    count = len(first)
    observed = {(left, right): 0 for left in categories for right in categories}
    histogram_first = {category: 0 for category in categories}
    histogram_second = {category: 0 for category in categories}
    for left_value, right_value in zip(first, second, strict=True):
        left, right = int(left_value), int(right_value)
        if left not in histogram_first or right not in histogram_second:
            continue
        observed[left, right] += 1
        histogram_first[left] += 1
        histogram_second[right] += 1
    if sum(histogram_first.values()) != count:
        return None
    disagreement_observed = 0.0
    disagreement_expected = 0.0
    denominator = float((maximum - minimum) ** 2)
    for left in categories:
        for right in categories:
            weight = ((left - right) ** 2) / denominator
            disagreement_observed += weight * observed[left, right] / count
            expected = histogram_first[left] * histogram_second[right] / (count * count)
            disagreement_expected += weight * expected
    if disagreement_expected == 0:
        return 1.0 if disagreement_observed == 0 else None
    return 1.0 - disagreement_observed / disagreement_expected
