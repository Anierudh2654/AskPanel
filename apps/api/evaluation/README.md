# Paired Context Evaluation

This benchmark can compare four arms on the same scripted questions: `sequential` (ordinary follow-up in the main chat), `full` (branch with all prior history), `relevant` (branch with lexical selection), and `semantic` (branch with embedding-based semantic selection). The model temperature is set to 0 for the run. Run it with synthetic, reviewed benchmark cases; do not put private chat transcripts in the checked-in cases file.

## Run

Activate the API virtual environment and set a real provider in `apps/api/.env` first. Nebius requires `NEBIUS_API_KEY` and a valid `LLM_MODEL`. Then, from `apps/api`:

```powershell
python ..\..\scripts\evaluate_context.py --context-budget 1800 --seed 17
```

To run all four arms, including the ordinary single-chat follow-up baseline and semantic retrieval, run:

```powershell
python ..\..\scripts\evaluate_context.py --include-sequential --include-semantic --context-budget 1800 --seed 17
```

The four-arm run makes 24 chat-completion calls for the current six cases (four per case), plus one embeddings request per case for semantic retrieval. It therefore incurs additional provider usage. The semantic arm's input-token total includes both embedding input and chat-completion input; the report also shows them separately. The summary includes per-strategy token/latency means and paired comparisons for every strategy pair. This fixture measures one clarification per case; it does not yet measure cumulative token growth over a multi-doubt chat or real-user interaction effort/preferences.

Semantic retrieval uses `EMBEDDING_MODEL` (default `Qwen/Qwen3-Embedding-8B`), `EMBEDDING_DIMENSIONS` (default `1024`), and `SEMANTIC_SIMILARITY_THRESHOLD` (default `0.25`). The configured Nebius or OpenAI-compatible provider key must have access to the embeddings endpoint/model. The selector always retains the parent turn, then ranks earlier complete turns by cosine similarity between their embeddings and the selected passage plus the branch question, subject to the approximate context token budget.

The command writes a timestamped directory under `apps/api/evaluation/runs/` containing:

- `results.json`: paired responses with strategy, provider/model, actual provider-reported input/output tokens, latency, estimated context tokens, and reference answers.
- `ratings_blinded.csv`: answers labeled X/Y/Z/W for the four-arm run (labels are randomized per case), with columns for relevance, correctness, faithfulness, and resolution ratings (all 1-5).
- `blind_key.json`: maps blinded labels to strategies. Keep this away from raters until they finish scoring.
- `summary.json`: paired token and latency summaries, explicitly stating that quality is not scored yet.

A mock provider run only verifies the evaluator pipeline; its answers cannot be used to assess answer quality or make an AI performance claim.

## Human ratings and analysis

Give a copy of the blinded sheet to each of two reviewers. Each reviewer should add a distinct `rater_id` (for example `R1`/`R2`), score every answer independently without opening `blind_key.json`, then return their completed rows. Append both sets of rows into `ratings_blinded.csv`; keep one row per rater per blinded answer. Use 1 for poor/incorrect and 5 for excellent/correct on each rubric dimension; note unsupported claims in `notes`. Save the combined CSV, then from `apps/api` run:

```powershell
python ..\..\scripts\evaluate_context.py --analyze --output evaluation\runs\<run-folder>
```

The analysis reports per-strategy token/latency means, every directed pairwise input-token comparison with its bootstrap interval and Wilcoxon test, every directed pairwise rating difference with its bootstrap interval and Wilcoxon test, and quadratic weighted Cohen's kappa per dimension when two raters have overlapping scores. A Wilcoxon p-value is reported as not estimable when there are too few nonzero paired differences. The Markdown console/file output and `analysis.json` include these pairwise metrics. This prompt-level benchmark does not measure actual UX outcomes such as task completion time, interaction effort, or user preference. Ratings are ordinal and this small fixture set is exploratory, not evidence of general superiority. For a portfolio-quality result, expand the task set, report agreement and exclusions, preregister a quality non-inferiority margin, and test additional seeds/model families.

## Current selector behavior

`relevant` groups the main transcript into turns, always preserves the parent assistant turn, scores earlier turns by lexical overlap with the anchor and latest branch question, skips zero-overlap turns, and adds matching turns under `CONTEXT_TOKEN_BUDGET` using a rough 4-characters-per-token estimate. `semantic` uses embedding cosine similarity to rank earlier complete turns and includes the parent turn. Both selectors use the same approximate context token budget; provider-reported token usage is authoritative for the completion prompt, with semantic embedding input counted separately. `full` supplies all prior history. These are retrieval heuristics, not trained rankers.
