import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation import analyze_blinded_ratings, paired_summary, run_paired_evaluation, write_evaluation_files


def run_command(args: argparse.Namespace) -> None:
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    strategies = ["full", "relevant"]
    if args.include_sequential:
        strategies.insert(0, "sequential")
    if args.include_semantic:
        strategies.append("semantic")
    rows = asyncio.run(run_paired_evaluation(
        cases,
        strategies=tuple(strategies),
        token_budget=args.context_budget,
        order_seed=args.seed,
    ))
    output_dir = args.output or Path("evaluation/runs") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_evaluation_files(rows, output_dir, blind_seed=args.seed)
    summary = paired_summary(rows)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **summary}, indent=2))
    if summary["provider"] == "mock":
        print("WARNING: mock responses are for pipeline smoke testing only; do not use them to claim answer quality.")
    print("Next: have reviewers score ratings_blinded.csv without opening blind_key.json, then run this command with --analyze.")


def analyze_command(args: argparse.Namespace) -> None:
    summary = analyze_blinded_ratings(
        args.output / "results.json",
        args.output / "ratings_blinded.csv",
        args.output / "blind_key.json",
    )
    (args.output / "analysis.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    def fmt(value: float | int | None, *, suffix: str = "", digits: int = 2) -> str:
        if value is None:
            return "not estimable"
        return f"{value:.{digits}f}{suffix}"

    def fmt_ci(bounds: list[float] | None, *, suffix: str = "%") -> str:
        if bounds is None:
            return "not estimable"
        return f"[{fmt(bounds[0], suffix=suffix)}, {fmt(bounds[1], suffix=suffix)}]"

    def fmt_p(value: float | None) -> str:
        if value is None:
            return "not estimable (too few nonzero paired differences)"
        return f"{value:.5g}"

    lines = [
        "# AskPanel Context Evaluation",
        "",
        f"- Provider/model: {summary['provider']} / {summary['model']}",
        f"- Paired cases: {summary['paired_cases']}",
        f"- Mean input tokens, full: {summary['mean_input_tokens_full']}",
        f"- Mean input tokens, relevant: {summary['mean_input_tokens_relevant']}",
        f"- Mean input-token savings: {summary['mean_input_token_savings_percent']}",
        f"- 95% bootstrap CI for input-token savings: {summary['input_token_savings_95pct_bootstrap_ci']}",
        f"- Paired Wilcoxon p-value for input-token difference: {summary['input_token_savings_wilcoxon_p_value']}",
        f"- Rated pairs: {summary['rated_pairs']}",
        f"- Mean rating difference (relevant - full): {summary['mean_quality_difference_relevant_minus_full']}",
        f"- 95% bootstrap CI for quality difference: {summary['quality_difference_95pct_bootstrap_ci']}",
        f"- Paired Wilcoxon p-value for quality difference: {summary['quality_wilcoxon_p_value']}",
        f"- Quadratic weighted kappa by dimension: {summary['quadratic_weighted_kappa_by_dimension']}",
        "",
        "## All strategy means",
        "",
    ]
    for strategy, values in summary["strategy_metrics"].items():
        lines.append(
            f"- {strategy}: chat input tokens {fmt(values['mean_input_tokens'])}; embedding input tokens "
            f"{fmt(values['mean_embedding_input_tokens'])}; total input tokens {fmt(values['mean_total_input_tokens'])}; "
            f"output tokens {fmt(values['mean_output_tokens'])}; chat latency ms {fmt(values['mean_latency_ms'])}"
        )
    lines.extend([
        "",
        "## Pairwise token comparisons",
        "",
        "Positive savings mean the comparison arm used fewer total input tokens (chat plus embeddings) than the baseline.",
        "",
    ])
    for comparison in summary["pairwise_comparisons"]:
        lines.append(
            f"- {comparison['comparison']} vs {comparison['baseline']}: mean total input "
            f"{fmt(comparison['mean_total_input_tokens_comparison'])} vs "
            f"{fmt(comparison['mean_total_input_tokens_baseline'])} tokens; mean saved "
            f"{fmt(comparison['mean_total_input_tokens_saved'])} tokens "
            f"({fmt(comparison['mean_input_token_savings_percent'], suffix='%')}; 95% bootstrap CI "
            f"{fmt_ci(comparison['input_token_savings_95pct_bootstrap_ci'])}); paired Wilcoxon p="
            f"{fmt_p(comparison['input_token_savings_wilcoxon_p_value'])}; "
            f"chat latency {fmt(comparison['mean_latency_ms_comparison'])} vs "
            f"{fmt(comparison['mean_latency_ms_baseline'])} ms"
        )
    lines.extend(["", "## Pairwise rating comparisons", ""])
    for comparison in summary["quality_pairwise_comparisons"]:
        lines.append(
            f"- {comparison['comparison']} minus {comparison['baseline']}: mean rating difference "
            f"{fmt(comparison['mean_quality_difference_comparison_minus_baseline'])}; rated pairs "
            f"{comparison['rated_pairs']}; 95% bootstrap CI "
            f"{fmt_ci(comparison['quality_difference_95pct_bootstrap_ci'], suffix='')}; Wilcoxon p="
            f"{fmt_p(comparison['quality_wilcoxon_p_value'])}"
        )
    lines.extend([
        "",
        "UX measures such as task completion time, interaction effort, and user preference are not measured by this prompt-level evaluation.",
        "Interpret scores with the sample size and uncertainty. This report is not a product-wide benchmark.",
    ])
    (args.output / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description="Paired AskPanel evaluation across chat and branch context strategies")
    parser.add_argument("--cases", type=Path, default=Path("evaluation/cases.json"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--context-budget", type=int, default=1800)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--include-sequential",
        action="store_true",
        help="Add a normal single-chat follow-up arm to the branch context comparison",
    )
    parser.add_argument(
        "--include-semantic",
        action="store_true",
        help="Add a branch arm ranked with Nebius/OpenAI-compatible text embeddings",
    )
    parser.add_argument("--analyze", action="store_true", help="Analyze completed blinded ratings in --output")
    args = parser.parse_args()
    if args.analyze:
        if args.output is None:
            parser.error("--analyze requires --output pointing to an evaluation run directory")
        analyze_command(args)
    else:
        run_command(args)


if __name__ == "__main__":
    main()
