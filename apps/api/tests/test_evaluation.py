import asyncio
import csv
import json
from pathlib import Path

from app.evaluation import analyze_blinded_ratings, run_paired_evaluation, write_evaluation_files


def sample_case(case_id: str = "case-1") -> dict:
    return {
        "id": case_id,
        "history": [
            {"role": "user", "content": "We are discussing ATP in cellular respiration."},
            {"role": "assistant", "content": "ATP carries energy for cellular reactions."},
            {"role": "user", "content": "Tell me about an unrelated topic " * 20},
            {"role": "assistant", "content": "Unrelated background " * 20},
            {"role": "user", "content": "I have a question about energy transfer."},
            {"role": "assistant", "content": "ATP hydrolysis can power cellular work."},
        ],
        "parent_index": 5,
        "anchor_text": "ATP hydrolysis",
        "question": "How does that transfer energy?",
        "reference_answer": "ATP hydrolysis releases energy that can drive cellular work.",
    }


def test_paired_runner_uses_two_strategies_and_reports_actual_usage():
    async def fake_completion(prompt):
        prompt_text = " ".join(message["content"] for message in prompt.messages)
        return {
            "answer": "ATP hydrolysis releases energy.",
            "input_tokens": len(prompt_text.split()),
            "output_tokens": 5,
            "latency_ms": 12,
        }

    rows = asyncio.run(run_paired_evaluation([sample_case()], completion=fake_completion, token_budget=1000))
    by_strategy = {row["strategy"]: row for row in rows}

    assert set(by_strategy) == {"full", "relevant"}
    assert by_strategy["relevant"]["input_tokens"] < by_strategy["full"]["input_tokens"]
    assert by_strategy["relevant"]["answer"] == by_strategy["full"]["answer"]
    assert all(row["latency_ms"] == 12 for row in rows)


def test_blinded_rating_sheet_and_paired_analysis(tmp_path: Path):
    rows = []
    for case_id in ("one", "two", "three"):
        for strategy, tokens in (("full", 100), ("relevant", 70)):
            rows.append({
                "case_id": case_id,
                "strategy": strategy,
                "provider": "mock-test",
                "model": "fixture",
                "prompt_version": "test",
                "temperature": 0.0,
                "context_token_budget": 100,
                "context_tokens_estimate": tokens,
                "prompt_chars": tokens * 4,
                "input_tokens": tokens,
                "output_tokens": 10,
                "latency_ms": 20,
                "answer": f"Answer for {case_id} using {strategy}",
                "reference_answer": f"Reference for {case_id}",
                "question": f"Question {case_id}",
            })
    write_evaluation_files(rows, tmp_path, blind_seed=4)

    ratings_path = tmp_path / "ratings_blinded.csv"
    with ratings_path.open(newline="", encoding="utf-8-sig") as handle:
        records = list(csv.DictReader(handle))
    for record in records:
        score = 5 if "relevant" in record["answer"] else 4
        record["rater_id"] = "rater-1"
        record.update({field: str(score) for field in ("relevance", "correctness", "faithfulness", "resolution")})
    second_rater_records = [dict(record, rater_id="rater-2") for record in records]
    records.extend(second_rater_records)
    with ratings_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)

    summary = analyze_blinded_ratings(tmp_path / "results.json", ratings_path, tmp_path / "blind_key.json")
    assert summary["paired_cases"] == 3
    assert summary["mean_input_token_savings_percent"] == 30
    assert summary["rated_pairs"] == 3
    assert summary["mean_quality_difference_relevant_minus_full"] == 1
    assert set(summary["raters"]) == {"rater-1", "rater-2"}
    assert summary["quadratic_weighted_kappa_by_dimension"] == {
        "relevance": 1.0, "correctness": 1.0, "faithfulness": 1.0, "resolution": 1.0,
    }
    assert json.loads((tmp_path / "blind_key.json").read_text(encoding="utf-8"))
