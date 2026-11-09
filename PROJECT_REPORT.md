# AskPanel: Project and Evaluation Report

**Prepared:** 6 November 2026  
**Project:** HCI_LLM / AskPanel  
**Purpose:** Explain the product, implementation, AI-engineering workflow, current evaluation, and what the results mean in both plain language and technical terms.

## 1. Executive summary

AskPanel is a local chat application built to explore a specific human-computer interaction idea: when a user wants to ask about one part of an AI answer, let them open a separate, anchored conversation about that passage. The main conversation stays intact, while the side conversation receives the selected passage and relevant prior context.

The engineering question is whether this branching interaction can make clarifications easier to manage while keeping answer quality and model usage reasonable. The evaluation compares four ways of asking the same clarification for each of six fixed cases:

1. **Sequential:** ask the doubt as the next message in the normal chat.
2. **Full branch:** ask in a side branch using all main-chat history up to the selected answer.
3. **Lexical-relevant branch:** ask in a side branch using the selected answer and earlier turns chosen by word overlap.
4. **Semantic-relevant branch:** ask in a side branch using the selected answer and earlier turns ranked by embedding cosine similarity.

The latest run (`20261106T124826Z`) used Nebius with DeepSeek V4.1 Flash 1017 and Qwen3 embeddings for semantic selection. It reported six matched cases and 24 answers. Sequential chat had the fewest input tokens in this one-follow-up setup, while relevant branch answers had the fewest output tokens. The branch variants' answers had very similar AI-assigned quality scores, but the sample is small and synthetic, and the ratings are from one AI reviewer—not independent human reviewers. The recorded latency excludes the semantic embedding request, so it is not end-to-end latency for semantic retrieval.

## 2. What the project is about

### Plain-language explanation

A normal chatbot typically includes earlier conversation messages when the user asks another question. That makes follow-ups feel continuous, but the history can become long and unrelated information can distract the model. In this app, main-chat prompts include the active main transcript.

AskPanel offers another interaction: select a passage in an answer, press **Ask**, and discuss that passage in a side conversation. The side conversation is linked to the selected text. Its replies stay in the side conversation instead of being added to the main chat.

The research idea is that this could make it easier to ask focused follow-up questions without derailing the main conversation. The engineering challenge is to give the model enough context to answer correctly while avoiding unnecessary context and extra interaction effort.

### AI-engineering explanation

The system is an LLM-backed web application with:

- A server-side provider integration. The browser never needs the Nebius key.
- Streaming generation from the API to the browser using server-sent events (SSE).
- Persistent main conversations, messages, anchored threads, and per-call usage records.
- Prompt assembly that distinguishes main-chat generation from branch generation.
- Two branch context selectors: lexical word overlap and semantic embedding similarity. Both can omit earlier turns, always retain the parent answer, and apply an approximate context budget.
- A paired evaluation pipeline that records provider-reported chat token counts, embedding input tokens, chat-completion latency, and blinded answer-rating sheets.

The primary experiment is about **prompt strategy and interaction mode**, not model training. It does not fine-tune a model. The semantic branch arm does use an embedding model for retrieval; it is separate from the chat model that writes the answer.

## 3. How one chat request flows through the system

1. The Next.js interface sends a user message to the FastAPI service.
2. The API checks the browser's local device identity and session cookie.
3. The API saves the user message and a placeholder assistant message.
4. The generation service assembles a prompt:
   - A main-chat request uses the active main-chat messages.
   - A branch request uses main-chat context up to the selected parent answer, branch framing, and the branch's own message history.
5. The provider adapter sends that prompt to Nebius Token Factory and reads the streamed response.
6. The API streams text deltas to the browser, then persists the completed answer, input/output token counts, time-to-first-token, total latency, provider, model, and prompt metadata.
7. The browser updates the conversation and records interaction events for later analysis.

## 4. Tech stack and why it is used

| Area | Technology | Purpose |
|---|---|---|
| Web UI | Next.js 14, React 18, TypeScript | Browser app, UI rendering, local development, and type checking |
| Styling | Tailwind CSS and app CSS | Layout, responsive styling, and chat/modal appearance |
| UI components | Radix UI, Lucide icons | Accessible dialog primitives and interface icons |
| Markdown | React Markdown, remark-gfm, rehype-highlight, rehype-sanitize | Render model answers with Markdown, tables, and highlighted code while sanitizing content |
| Client state | Zustand; TanStack Query is also installed | Shared UI state and client-side state/data utilities |
| API | Python 3.11/3.12, FastAPI, Pydantic | HTTP API, request validation, and async request handling |
| Persistence | SQLAlchemy async, SQLite by default, PostgreSQL optionally | Store conversations, messages, threads, events, provider calls, and study records |
| Database changes | Alembic | Version and apply schema migrations |
| Streaming | SSE Starlette plus browser fetch stream reader | Deliver model text incrementally instead of waiting for the whole answer |
| Provider calls | httpx with an OpenAI-compatible chat-completions request; Anthropic SDK path also exists | Call Nebius/OpenAI-compatible providers or Anthropic from the server |
| Current model | Nebius Token Factory, DeepSeek V4.1 Flash 1017 | Produce chat and evaluation responses; configured in the ignored API environment file |
| Evaluation/statistics | Python, SciPy, pandas/statsmodels/pingouin dependencies, CSV/JSON | Store benchmark outputs, compare paired measurements, and prepare human/AI rating analysis |
| Local services | PowerShell, Node/npm, Python virtual environment; Docker Compose for optional PostgreSQL | Run the web app, API, and optional database locally |

The API key belongs in the git-ignored file apps/api/.env. It must not be placed in the browser environment file. The app's Nebius path uses httpx directly; the separate OpenAI Python SDK snippet is not required to run AskPanel.

## 5. Important source files and functions

This map covers the project-owned application, evaluation, and operational files. It does not enumerate every dependency inside node_modules or the Python environment.

### Backend: API, configuration, and storage

| File | Main functions/classes | Why it exists |
|---|---|---|
| apps/api/app/main.py | FastAPI app and lifespan; request models; issue_token; current_user; user_payload; message_payload; owned_conversation; stream_response; health; participant_login; device_login; admin_login; logout; auth_me; list_conversations; create_conversation; get_conversation; rename_conversation; delete_conversation; send_message; edit_message; create_thread; thread_reply; get_thread; post_thread_message; receive_events | Central HTTP API. Handles authentication, chat CRUD, main-message generation, branch creation/replies, and event ingestion. The lifespan initializes database tables for local startup. |
| apps/api/app/config.py | Settings; get_settings | Loads typed configuration from apps/api/.env and process environment, including database, provider, chat model, embedding model/dimensions, similarity threshold, token limits, and context strategy. Environment variables take precedence over file values. |
| apps/api/app/db.py | Base; engine; SessionLocal; get_session | Creates the async SQLAlchemy engine and yields database sessions to API handlers and generation tasks. |
| apps/api/app/models.py | new_id; User, Conversation, Message, MessageRevision, Thread, ThreadMessage, LLMCall, Event, Task, Participant, StudySession, QuizResponse, Survey, Rubric, EvalRun, EvalScore, Job | Defines persistent database entities. LLMCall is the key observability record: it stores provider/model, token counts, timings, error, and assembled prompt/response. Event stores interface interactions. |
| apps/api/app/admin.py | require_admin; list_tasks; upsert_task; assign_study; overview; participants; import_participants; sessions; llm_calls; compare; export_data; run_eval; eval_runs | Older/admin research endpoints for task setup, participants, sessions, call inspection, comparisons, exports, and evaluation run records. There is no finished admin analytics UI in the current simplified app. |
| apps/api/app/study.py | participant_state; current_session; record_consent; start_session; create_study_conversation; end_chat; submit_quiz; submit_survey; finish_session; _owned_study; score_survey | Study-participant, session, consent, quiz, and survey routes retained in the API. They are not part of the current everyday chat flow. |
| apps/api/alembic/versions/0001_initial.py | Initial schema migration | Creates the first versioned database schema. |

### Backend: prompt construction and model generation

| File | Main functions/classes | Why it exists |
|---|---|---|
| apps/api/app/services/prompts.py | BASE_SYSTEM_V1; THREAD_FRAMING_V1; base_system | Supplies the general assistant instruction and the extra branch instruction telling the model to answer about the selected passage concisely. |
| apps/api/app/services/context.py | Prompt; _get; estimate_tokens; _terms; _turn_groups; select_relevant_messages; select_semantic_messages; build_main_context; build_thread_context; build_semantic_thread_context | Builds prompts. Lexical selection ranks prior turns using shared words. Semantic selection ranks them by cosine similarity from embeddings. Both preserve the selected parent answer, add earlier complete turns under an approximate token budget, and then append branch history. |
| apps/api/app/services/generation.py | generate | Coordinates a complete generation: reads history, assembles main or branch prompt, streams provider output, persists message status and LLMCall telemetry, and records failures. Provider errors are also written to the backend log. |
| apps/api/app/services/llm/provider.py | stream_completion | Implements mock responses, OpenAI-compatible streaming for Nebius/OpenAI, and Anthropic streaming. The Nebius path uses Bearer authentication, chat-completions SSE, and provider-reported token usage. HTTP error response detail is preserved for diagnostics. |
| apps/api/app/services/llm/embeddings.py | embed_texts | Calls the configured Nebius or OpenAI-compatible `/embeddings` endpoint for query and candidate-turn vectors; checks vector count and returns provider-reported embedding input-token usage. It is called only by the semantic branch strategy. |
| apps/api/app/services/llm/pricing.yaml | Provider/model price entries | Intended place for configured price-per-token data. Current values are placeholders, so the latest report gives token counts, not a trustworthy dollar cost. |

### Backend: measurement and evaluation

| File | Main functions/classes | Why it exists |
|---|---|---|
| apps/api/app/metrics.py | compute_session_metrics | Aggregates available session events and LLM calls into duration, idle/blur time, turns, tokens, cost field, clarification counts, scroll distance, feature-discovery time, TTFT, and latency. Runtime aggregation currently sums `LLMCall.input_tokens`; it does not add semantic embedding tokens stored in `LLMCall.params`, so runtime session totals can undercount semantic usage. The evaluator itself does add those tokens. The study workflow is only partially wired into the current UI. |
| apps/api/app/evaluation.py | case_to_prompt; complete_prompt; run_paired_evaluation; write_evaluation_files; paired_summary; analyze_blinded_ratings; bootstrap_mean_ci; paired_wilcoxon; quadratic_weighted_kappa | Builds the four matched prompt variants, randomizes call order within each case, writes result and blinded-rating artifacts, reports per-strategy and pairwise token/latency summaries, and analyzes ratings. It includes semantic embedding tokens in total input tokens; the recorded latency currently measures the chat completion only. |
| apps/api/evaluation/cases.json | Six synthetic benchmark cases | Defines identical benchmark inputs for every arm: conversation history, parent answer index, selected anchor text, one follow-up question, and a reference answer. Some unrelated turns are after the parent cutoff and are therefore not visible to the selectors; see the benchmark limitation below. |
| apps/api/evaluation/README.md | Evaluation workflow and interpretation notes | Documents how to run, blind, score, and analyze a benchmark; warns that a prompt benchmark is not a UX study. |

### Web interface

| File | Main functions/classes | Why it exists |
|---|---|---|
| apps/web/app/page.tsx | Home | Mounts the main chat workspace. |
| apps/web/app/layout.tsx | RootLayout | Defines page metadata, fonts, and the root document layout. |
| apps/web/app/globals.css | Application styles | Styles chat layout, transcripts, composer, anchored text, modal branch, mobile sheet, and responsive behavior. |
| apps/web/components/ChatWorkspace.tsx | api; rehypeAnchors; Markdown; deviceId; loginDevice; ChatWorkspace; internal loadConversation/loadHistory/createConversation/streamMain/sendMain/openThread/clickAnchor/sendThread/saveEdit/askFromButton handlers and effects | Main browser experience. It manages device login, chat history, SSE updates, text selection and anchor offsets, branch modal, message editing, event logging, and transcript rendering. The current scroll-follow behavior starts at the bottom, follows new branch output while the reader is near the bottom, and pauses when they scroll up. |
| apps/web/lib/sse.ts | postSSE | Posts API requests, reads the response stream, parses SSE frames, and invokes callbacks for event names/data. |
| apps/web/stores/ui.ts | TextSelection; UIState; useUIStore | Shared client-side UI state such as sidebar, current selection, toast, and selection popover. |
| apps/web/.env.example | NEXT_PUBLIC_API_URL | Frontend API URL. It contains no provider key. |

### Scripts and run support

| File | Main functions | Why it exists |
|---|---|---|
| apps/api/pyproject.toml | Package metadata; runtime/dev dependency groups; pytest and Ruff settings | Defines supported Python versions, backend libraries, optional test dependencies, and test discovery/configuration. |
| apps/web/package.json | `dev`; `build`; `start`; `lint` npm scripts | Declares frontend libraries and the commands used to run, build, serve, and lint the Next.js app. `package-lock.json` pins the installed dependency graph. |
| apps/api/alembic/env.py | `run_migrations_offline`; `do_run_migrations`; `run_migrations_online` | Connects Alembic to the app's configured database and SQLAlchemy metadata so schema migrations can be applied. |
| apps/api/alembic/script.py.mako | Alembic revision template | Template Alembic uses when generating a new migration revision. |
| scripts/evaluate_context.py | run_command; analyze_command; main | CLI entry point. `--include-sequential` adds the main-chat arm; `--include-semantic` adds the embedding-retrieval arm. With both flags it runs all four strategies, then `--analyze` reads a completed ratings sheet and writes `analysis.json` and `analysis.md`. |
| scripts/export_data.py | export | Writes sessions, events, or LLM-call fields to CSV. It excludes assembled prompts and response text from its default export. |
| scripts/seed_study.py | seed | Loads researcher-authored study tasks from scripts/tasks into the database. This belongs to the retained study workflow. |
| scripts/simulate_users.py | parse_events; simulate | Synthetic API smoke flow for admin/participant sessions and A/B/C paths. It requires ADMIN_PASSWORD and exercises older study endpoints. |
| scripts/tasks/README.md | Task JSON schema | Explains how to create study task files for seeding. |
| docker-compose.yml | postgres service | Optional PostgreSQL 16 service. SQLite is the default local database and does not require Docker. |
| .gitignore | Ignore rules | Keeps .env files, database files, caches, build output, and evaluation run artifacts out of normal source tracking. |

The repository also contains generated/static assets such as `apps/web/app/favicon.ico` and the Geist font files; these are served or loaded by the UI and do not contain application logic. `apps/api/.env` and `apps/web/.env.local` are local configuration files, while `.env.example` files document safe non-secret settings. Never include the API key in the frontend environment or commit it.

### Tests

The backend test suite is in apps/api/tests:

- test_api.py: device-backed chat/API generation and persistence flow.
- test_context.py: main/branch isolation, parent cutoff, quote handling, relevance selection, and context budget.
- test_device_auth.py: stable device identity login.
- test_evaluation.py: paired runner, blinded outputs, and rating analysis.
- test_llm_provider.py: provider request formatting, streaming, token usage, and missing-key handling.
- test_metrics.py: event/session metric calculations.
- test_study.py: study consent/session flow.
- test_thread_branch.py: branch answers remain isolated from the main transcript.

## 6. How to run the app

Run the API and web app in separate PowerShell terminals. The Nebius key stays in apps/api/.env.

### Terminal A: backend

From the repository root:

    cd apps/api
    ..\..\.venv\Scripts\Activate.ps1
    python -m pip install -e ".[dev]"
    python -m alembic upgrade head
    uvicorn app.main:app --reload --port 8000

The API health endpoint is http://localhost:8000/health. Interactive API documentation is at http://localhost:8000/docs.

### Terminal B: frontend

From the repository root:

    cd apps/web
    if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
    npm install
    npm run dev

Open http://localhost:3000. Send a normal message to verify the full browser → API → Nebius → browser path. Select text in a completed answer, choose **Ask**, and send a branch follow-up to check the anchored side conversation.

For a mock-only development run without a real model, set LLM_PROVIDER=mock in apps/api/.env and restart the API. Mock replies test plumbing only and must not be used for answer-quality claims.

## 7. AI-engineering and evaluation workflow

### What the six benchmark cases contain and why

The benchmark fixture is `apps/api/evaluation/cases.json`. Each JSON object has:

- `id`: stable case identifier used to pair results and ratings.
- `history`: synthetic user/assistant turns in the fixture. Some cases contain an unrelated topic, but only messages at or before `parent_index` are actually supplied to the model.
- `parent_index`: zero-based position of the assistant answer that is the branch parent. It makes the selected answer and cutoff explicit, even when the fixture contains later distractor turns.
- `anchor_text`: a substring of the parent answer that represents the passage selected by the user.
- `question`: the same follow-up question sent in each experimental arm.
- `reference_answer`: a short target answer used as reviewer guidance, not as a generated response or an automatic correctness score.

The six topics are ATP and cellular respiration, unhashable Python dictionaries, HTTP `Cache-Control: private`, L1 feature selection, database index maintenance, and probability without replacement. Together they cover science, programming, web protocols, statistics, databases, and probability. They were authored to provide a controlled question, selected answer passage, and short reference answer in several subject areas.

**Important fixture limitation:** some cases contain an unrelated turn *after* the selected parent answer (for example photosynthesis, HTTP `max-age`, or independent coin flips). `parent_index` intentionally truncates the prompt at the selected parent, so those later turns are not available to any strategy and are not distractors in this benchmark. The turns before the selected parent are mostly on-topic as well. As a result, the current six cases do not provide a strong test of whether lexical or semantic retrieval can find relevant turns among genuinely competing prior topics. That likely helps explain why semantic selection kept about as many chat-input tokens as full history. A stronger retrieval benchmark should place several on-topic and off-topic turns **before** the parent answer, then ask about a specific concept from that history.

### Four compared prompt strategies

- **sequential:** append the doubt as the next user turn in the main chat after the parent answer. All prior turns through the parent remain in history. There is no branch-specific framing.
- **full:** create a branch, include all active main-chat history through the parent answer, add selected-passage framing, and append the branch question.
- **relevant:** same branch framing, always include the parent answer, then rank earlier complete turns by lexical word overlap with the selected passage and branch question. Zero-overlap turns are skipped.
- **semantic:** same branch framing and parent retention, but rank earlier complete turns by cosine similarity between their embedding and an embedding of the selected passage plus branch question. Only turns over the configured similarity threshold are eligible.

Both selectors use the same approximate context token budget (1800 for this run). Token estimates guide selection; provider-reported chat token usage is used in the results. Semantic embedding input tokens are added to chat input tokens for the semantic arm's total-input comparison. The default embedding settings are `Qwen/Qwen3-Embedding-8B`, 1024 dimensions, and a 0.25 minimum cosine similarity; they can be overridden with `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, and `SEMANTIC_SIMILARITY_THRESHOLD`.

This is a **prompt-level paired comparison**, not a human interaction experiment. Each case is sent through all four strategies, and arm order is shuffled with the run seed to reduce order effects. The same chat provider/model and temperature are used across arms. However, sequential prompts do not get the branch instruction to focus on the selected passage and answer concisely. That makes this a comparison of the current product modes, not a clean isolation of the UI branching mechanism alone.

### Generate and analyze a four-arm run

From `apps/api`, with the virtual environment active and a real chat provider plus embedding-capable API key configured:

    python ..\..\scripts\evaluate_context.py --include-sequential --include-semantic --context-budget 1800 --seed 17

For the current six cases this sends 24 chat-completion requests (four per case) and six embedding requests (one per case), so the run uses additional provider tokens and may incur usage charges. The output folder is printed as `output_dir`. It contains:

- `results.json`: one row per case/strategy with prompt strategy, provider/model, provider-reported completion input/output tokens, embedding input tokens, their total, chat-completion latency, estimates, and answer text.
- `summary.json`: per-strategy means and pairwise token/latency summaries. Semantic input-token comparisons include embedding tokens.
- `ratings_blinded.csv`: one X/Y/Z/W row per strategy answer and case, with randomized labels and blank 1–5 rating fields.
- `blind_key.json`: mapping from blinded labels to strategies. Keep it away from reviewers until they finish scoring.

Two independent human reviewers can score every answer, using distinct rater IDs, integer scores 1–5 for relevance, correctness, faithfulness, and resolution, and notes for errors or unsupported claims. Preserve `pair_id` and `sample_id` when combining ratings. AI ratings can pilot the workflow, but label the rater as AI; they are not human evidence and cannot establish human agreement.

After ratings are complete, from `apps/api` run the analysis command with the actual generated folder name (do not type angle-bracket placeholders):

    python ..\..\scripts\evaluate_context.py --analyze --output evaluation\runs\20261106T124826Z

Analysis writes `analysis.md` and `analysis.json`. The code maps blinded IDs internally, calculates paired differences and bootstrap intervals, runs paired Wilcoxon tests, and computes quadratic weighted kappa only when at least two raters have overlapping dimension scores. The report includes an all-pair comparison for each strategy pair.

**Latency limitation:** `latency_ms` starts when the chat completion begins, after `case_to_prompt` has constructed the prompt. The semantic embedding request is awaited during that prompt construction, before the timer starts. Therefore its retrieval time is excluded from the printed latency. The run's latency table is chat-generation latency, not end-to-end semantic response latency. The current results cannot support a latency claim about semantic search. A fair end-to-end comparison needs separate embedding latency and combined embedding-plus-generation latency fields.

### Other checks and exports

Backend tests:

    python -m pytest

Frontend production build:

    cd ..\web
    npm run build

Export recorded model calls from apps/api:

    python ..\..\scripts\export_data.py llm_calls

The export includes provider/model, token, timing, and other call fields but omits raw assembled prompts and responses. The local database itself does store assembled prompts and responses, so treat askpanel.db as conversation data.

## 8. Metrics, formulas, and how to read them

This section defines the measurements before presenting the results. The evaluation is **paired**: every case is run through each strategy, and comparisons are made for the same case. A case is the unit being resampled and compared; the six generated answers in a case are not six independent observations.

### Token usage

For case `i` and strategy `s`, define:

- `C[i,s]`: input tokens reported by the chat-completion provider.
- `E[i,s]`: input tokens reported by the embeddings provider; zero for nonsemantic strategies.
- `I[i,s]`: total measured input across calls.
- `O[i,s]`: output tokens reported by the chat-completion provider.
- `T[i,s]`: raw total input plus output tokens.

The evaluator calculates:

```text
I[i,s] = C[i,s] + E[i,s]
T[i,s] = I[i,s] + O[i,s]
```

For the semantic arm, `E` accounts for the query and candidate-turn embedding request. It is added to the input-token comparison so retrieval overhead is visible. The per-strategy table reports chat input, embedding input, total input, output, and their sum separately. `T` is a **raw token count**, not a cost estimate: embedding and chat models may use different tokenizers and have different prices. No reliable dollar cost is calculated because the configured price table is not complete.

For any per-strategy metric `X`, the reported mean across the six cases is `mean(X) = sum(X[i]) / 6`. Thus the mean total input for a strategy averages each case's chat-plus-embedding input, and the mean latency averages the six measured chat-completion durations.

### Paired token savings

For a baseline strategy `B` and comparison strategy `M`, the case-level absolute and percentage savings are:

```text
S[i] = I[i,B] - I[i,M]
P[i] = 100 × (I[i,B] - I[i,M]) / I[i,B]
```

The report gives the mean of the six case-level `S[i]` values and the mean of the six case-level `P[i]` values. **Positive** savings mean the comparison uses fewer input tokens than the baseline; **negative** savings mean it uses more. Percent savings are averaged case by case, so they need not equal a percentage calculated from the two strategy-wide mean token counts. Reverse-direction comparisons have different percentage denominators and are therefore both reported.

### Bootstrap confidence intervals

The 95% bootstrap interval describes uncertainty in the mean paired value (percentage token savings or quality difference) under resampling of the observed cases. The evaluator:

1. Samples six case-level observations **with replacement**.
2. Calculates their mean.
3. Repeats that process 10,000 times with a fixed seed.
4. Reports the 2.5th and 97.5th percentile estimates as the interval endpoints.

It is a resampling interval for this small fixture, not a guarantee that 95% of future results fall inside it. With only six synthetic cases, the endpoints can move substantially if the case set changes. Bootstrap intervals and the Wilcoxon test below use different procedures, so they can give different signals—as they did for relevant-versus-full token savings.

### Wilcoxon paired test and p-value

The paired Wilcoxon signed-rank test uses the case-by-case differences between two strategies. It ranks the absolute nonzero differences and checks whether positive and negative ranks are balanced, without assuming normally distributed differences. The implementation uses a two-sided test. It removes exact zero differences; if fewer than two nonzero differences remain, it reports `None` (shown as “not estimable”), rather than manufacturing a p-value.

A p-value is the probability, **assuming the test's null model**, of seeing results at least as incompatible with that null as the observed data. It is not the probability that the null hypothesis is true, and a large p-value does not establish that two strategies are equal. With six cases the test has low resolution. The many pairwise comparisons here are exploratory and their p-values are not adjusted for multiple testing.

### Human/AI rating measures

Reviewers score each answer from 1 to 5 on four dimensions:

- **Relevance:** does it answer the selected passage's follow-up question?
- **Correctness:** are its factual or technical claims accurate?
- **Faithfulness:** does it stay grounded in the case context and avoid unsupported additions?
- **Resolution:** does it explain enough to resolve the stated doubt?

These are ordinal rubric scores. For a case/strategy, the analysis averages all available dimension scores (and submitted raters' scores, if there are multiple). It then calculates a paired case-level difference:

```text
D[i] = Q[i,M] - Q[i,B]
```

where `Q` is that case/strategy's mean rating. A positive value favors the comparison arm; a negative value favors the baseline. The reported quality difference is the mean of the case-level differences. The same bootstrap and Wilcoxon procedures described above are applied to those paired differences. The current run has one rater (`AI-R1`), so these numbers are exploratory AI judgments, not human validation.

### Rater agreement: quadratic weighted kappa

Quadratic weighted kappa (`kappa_w`) measures agreement between two raters while penalizing larger score disagreements more heavily than one-point disagreements. For scores `a` and `b` on the 1–5 scale, the disagreement weight is:

```text
w[a,b] = (a - b)^2 / (5 - 1)^2
```

Let `D_observed` be the observed weighted disagreement and `D_expected` the disagreement expected from the two raters' score distributions. Then:

```text
kappa_w = 1 - D_observed / D_expected
```

Values near 1 indicate stronger agreement beyond chance, values around 0 indicate agreement close to chance, and negative values indicate worse-than-chance agreement. The current analyzer compares the first two rater IDs in sorted order and uses only overlapping scores. Kappa is not calculated with one rater or insufficient overlapping scores; `{}` in this run means no rater pair was available, not zero agreement.

### Latency and retrieval similarity

`latency_ms` in this evaluator is elapsed time for the streaming **chat-completion** call, averaged across the six cases. Prompt assembly and the semantic embeddings request happen before this timer starts. Thus it does not measure full user wait time for semantic retrieval. Time-to-first-token (TTFT), when recorded in normal app telemetry, is the time from generation start until the first streamed text; it is distinct from full completion latency. Neither user task completion time nor interaction effort is measured by this benchmark.

Semantic context selection uses cosine similarity between a vector for the selected passage plus the branch question and a vector for each eligible earlier conversation turn:

```text
cosine(q, t) = dot(q, t) / (norm(q) × norm(t))
```

The parent answer is always retained. Earlier turns are ranked by the similarity score; this run uses a minimum threshold of 0.25 and a rough context token budget. Similarity is a ranking score, not a probability that a turn is relevant or a measure of answer quality.

## 9. Results from the latest four-arm run

**Run folder:** `apps/api/evaluation/runs/20261106T124826Z`  
**Cases:** 6 synthetic paired scenarios; 24 answers total  
**Provider/chat model:** Nebius / `deepseek-ai/DeepSeek-V4.1-Flash-1017`  
**Embedding model:** `Qwen/Qwen3-Embedding-8B` (reported run configuration)  
**Temperature:** 0 for evaluation calls  
**Ratings:** one AI reviewer, `AI-R1`; no independent human rater

| Strategy | Mean chat input | Mean embedding input | Mean total input | Mean output | Mean input + output* | Mean chat latency** |
|---|---:|---:|---:|---:|---:|---:|
| Sequential main chat | 146.5 | 0 | 146.5 | 674.7 | 821.2 | 5.09 s |
| Full-history branch | 208.8 | 0 | 208.8 | 273.8 | 482.7 | 2.49 s |
| Lexical-relevant branch | 191.5 | 0 | 191.5 | 337.5 | 529.0 | 3.04 s |
| Semantic-relevant branch | 208.8 | 60.5 | 269.3 | 332.0 | 601.3 | 2.58 s |

\* The sum is only a raw-token count, not a dollar-cost estimate; input and output rates differ, and embedding pricing may differ from chat pricing.  
\** Latency excludes prompt construction and semantic embedding retrieval. It is chat-completion latency only.

The semantic selector used an average of 60.5 embedding input tokens per case, in addition to an average of 208.8 chat input tokens. In this run, it did not reduce the chat prompt relative to full history on average, so its total input was highest among branch strategies. This means semantic ranking incurred overhead without reducing the selected-context prompt enough on this six-case fixture. Given the fixture limitation described above, this run does not show whether semantic retrieval can save context in a conversation with real competing topics. It does show that embedding token overhead must be counted and that retrieval should be evaluated on realistic candidate histories.

### Input-token comparisons

Pairwise percentage savings use the named baseline's total input (chat plus embedding where applicable). Positive means the comparison strategy used fewer input tokens; negative means it used more.

| Comparison (comparison vs baseline) | Mean total input saved | Mean savings vs baseline | 95% bootstrap CI for savings | Paired Wilcoxon p |
|---|---:|---:|---:|---:|
| Relevant vs full | 17.3 tokens | 8.51% | 2.12% to 16.42% | 0.25 |
| Semantic vs full | -60.5 tokens | -28.65% | -32.61% to -24.70% | 0.03125 |
| Sequential vs full | 62.3 tokens | 30.04% | 27.76% to 32.41% | 0.03125 |
| Full vs relevant | -17.3 tokens | -10.47% | -20.73% to -2.42% | 0.25 |
| Semantic vs relevant | -77.8 tokens | -41.92% | -54.45% to -31.97% | 0.03125 |
| Sequential vs relevant | 45.0 tokens | 22.87% | 16.31% to 28.00% | 0.03125 |
| Full vs semantic | 60.5 tokens | 22.15% | 19.73% to 24.53% | 0.03125 |
| Relevant vs semantic | 77.8 tokens | 28.89% | 24.14% to 34.38% | 0.03125 |
| Sequential vs semantic | 122.8 tokens | 45.61% | 44.40% to 46.65% | 0.03125 |
| Full vs sequential | -62.3 tokens | -43.20% | -48.06% to -38.54% | 0.03125 |
| Relevant vs sequential | -45.0 tokens | -30.75% | -39.05% to -20.51% | 0.03125 |
| Semantic vs sequential | -122.8 tokens | -83.98% | -87.49% to -79.93% | 0.03125 |

The earlier console did not show these other confidence intervals because `paired_summary` stored them in `analysis.json`, while `analyze_command` only printed the mean savings and Wilcoxon p-value for each pair. The updated CLI prints every pairwise interval. For relevant versus full, the bootstrap interval is above zero while the Wilcoxon p-value is 0.25. These methods disagree on this tiny sample; the interval is not proof of a reliable effect. The sequential arm used the fewest **input** tokens because it avoids branch framing and uses a single follow-up turn. Semantic used the most branch input tokens once embedding cost is included. With six matched cases, each statistical result is exploratory; p = 0.03125 does not establish a product-wide effect. The pairwise p-values are exploratory and are not adjusted for the multiple comparisons.

### Output tokens and latency

Sequential outputs averaged 674.7 tokens, around twice the branch outputs (273.8–337.5). Since sequential has no instruction to focus on the selected passage and answer concisely, this difference may be caused by prompt framing as well as interaction mode. Branch outputs cannot be assumed to be equally detailed or equally complete just because their ratings are close.

The displayed latency means are about 2.49 s (full), 3.04 s (relevant), 2.58 s (semantic), and 5.09 s (sequential), but these are chat-completion times only. The semantic embedding call occurs before this timing begins. Therefore 2.58 s must not be interpreted as the total time users wait for semantic retrieval plus an answer, and the current run does not establish which strategy has the best end-to-end speed.

### AI-assigned answer ratings

The AI reviewer entered scores in the 1–5 dimensions relevance, correctness, faithfulness, and resolution. The overall per-dimension averages across the 24 answers were relevance **5.00**, correctness **4.71**, faithfulness **4.71**, and resolution **4.83**. The generated analysis then averaged the dimensions within each answer/case before comparing strategies.

| Comparison (comparison minus baseline) | Mean rating difference | 95% bootstrap CI | Paired Wilcoxon p |
|---|---:|---:|---:|
| Relevant minus full | -0.042 | -0.375 to 0.250 | 1.0 |
| Semantic minus full | -0.042 | -0.375 to 0.250 | 1.0 |
| Sequential minus full | 0.000 | -0.333 to 0.333 | 1.0 |
| Full minus relevant | 0.042 | -0.250 to 0.375 | 1.0 |
| Semantic minus relevant | 0.000 | 0.000 to 0.000 | Not estimable |
| Sequential minus relevant | 0.042 | -0.125 to 0.250 | 1.0 |
| Full minus semantic | 0.042 | -0.250 to 0.375 | 1.0 |
| Relevant minus semantic | 0.000 | 0.000 to 0.000 | Not estimable |
| Sequential minus semantic | 0.042 | -0.125 to 0.250 | 1.0 |
| Full minus sequential | 0.000 | -0.333 to 0.333 | 1.0 |
| Relevant minus sequential | -0.042 | -0.250 to 0.125 | 1.0 |
| Semantic minus sequential | -0.042 | -0.250 to 0.125 | 1.0 |

Each comparison has 6 rated cases. A p-value is not estimable for semantic versus relevant because every paired difference is exactly zero, so the test implementation has fewer than two nonzero differences. Quadratic weighted kappa is `{}` because there was only one rater.

These very small mean differences and broad interval do not demonstrate that the strategies are equivalent or that quality is preserved. A `None` p-value means the test could not be run under its nonzero-difference rule, not that the arms are proven equal. A single AI reviewer can provide a pilot signal, but cannot provide independent human quality validation or inter-rater reliability.

## 10. What we can conclude—and what we cannot

### Supported by this run

- For the single clarification per synthetic case, sequential had the fewest input tokens (146.5 mean) and semantic had the most total branch input tokens (269.3 mean including embeddings).
- Semantic retrieval's embedding overhead averaged 60.5 input tokens per case, and the selected chat context averaged the same 208.8 tokens as full history. It did not yield an input-token saving in this run.
- Lexical relevant selection averaged 191.5 chat input tokens versus 208.8 for full history, an 8.5% saving; this six-case paired result was not significant under the reported Wilcoxon test (p = 0.25).
- Branch prompts produced shorter answers than the sequential prompt on average. This comparison is confounded by different branch-specific concision instructions.
- The AI reviewer assigned high, similar scores across arms. The reported quality differences are small, but uncertainty is wide and one AI rater cannot establish human quality preservation.
- The printed latency values compare chat completion time only. They omit semantic embedding time, so they cannot establish semantic end-to-end latency.

### Not established

- Whether people find branch UX easier, more discoverable, or more satisfying.
- Whether branching improves task completion or reduces cognitive interruption.
- Whether sequential-chat token use grows more over many doubts. This benchmark includes only one doubt per case, not a multi-turn clarification sequence.
- Whether branch output quality is non-inferior under a preregistered margin or across broader topics/models; the current one-reviewer AI scores do not establish this.
- Whether semantic retrieval improves relevance, quality, or total response time. It currently adds input-token overhead, and its retrieval latency is not measured in the printed latency.
- Dollar-cost savings; configured model prices are missing.
- General behavior beyond these six synthetic cases.

To answer “did branching improve UX without compromising tokens?” rigorously, run two follow-ups:

1. Improve the evaluator instrumentation to report separate embedding latency, chat generation latency, and end-to-end semantic latency. Revisit the semantic threshold/budget and expand varied cases to see when retrieval actually excludes irrelevant turns. Add multiple candidate histories where semantic similarity can plausibly outperform lexical overlap.
2. Expand the paired benchmark with multi-doubt sequences so each new sequential question sees accumulated conversation and each branch sees its own growing branch history. Record cumulative input/output tokens per resolved task, answer quality, and end-to-end latency.
3. Run a counterbalanced user study. Give participants equivalent tasks in sequential and branch modes, then measure completion time, success/correctness, number of turns, interaction effort, and preference. Keep system instructions comparable or explicitly treat branch concision instructions as part of the product variant.

## 11. Current project status

AskPanel is a functioning local prototype: normal chat, persistent conversation history, server-side Nebius generation, anchored isolated side threads, streaming output, and token/timing capture are in place. The backend test run pasted by the user reported **17 passed, 1 Starlette/httpx deprecation warning**. That test run predates the later scroll-follow and four-arm semantic evaluator changes, so it does not verify those changes. The frontend production build has not been reported.

### Work completed during setup and evaluation

- Configured the ignored API environment for Nebius Token Factory and the DeepSeek V4.1 Flash 1017 model; the API key was kept out of source files and the browser environment.
- Updated the README with Windows PowerShell startup instructions and corrected the virtual-environment activation path.
- Diagnosed the Next.js EINVAL startup failure as a generated-cache/reparse-point issue under the OneDrive workspace; clearing the generated .next folder allowed frontend startup.
- Improved provider error diagnostics so the API log and stored call error include Nebius's response detail.
- Changed the branch modal scroll behavior to open at the bottom, follow new output while the reader is near the bottom, and stop following when the reader scrolls up.
- Extended the benchmark to four arms: sequential main chat, full-history branch, lexical-relevant branch, and semantic-relevant branch. Semantic embeddings and their token usage are included in total-input comparisons. The analysis currently excludes embedding retrieval time from latency.
- Identified a benchmark-fixture limitation: off-topic turns that occur after the selected parent are cut off before prompt assembly. The present cases therefore do not strongly test retrieval among competing prior topics. The run shows semantic overhead on these cases, not a general conclusion about embedding retrieval.
- The evaluator includes embedding input tokens in its comparison, but the application's separate session-analytics aggregation still sums only chat `LLMCall.input_tokens`; runtime analytics can undercount semantic usage until that code is updated.
- Scored the latest 24 blinded answers in run `20261106T124826Z` as `AI-R1` without opening the blind key. The analysis command decoded strategy labels internally. These are AI assessments, not human ratings.
- The user ran the backend suite and reported 17 passing tests in an earlier state. No test run after the latest semantic evaluation changes or frontend production build has been reported.

The project README identifies further work before a research pilot: complete participant/quiz/survey flow, richer evaluation and statistical analysis, an LLM judge workflow, admin reporting, broader UI acceptance tests, rate/spend controls, privacy deletion and backup, and accessibility/deployment hardening. Do not describe this prototype as a validated product or research finding.

## 12. Short glossary

- **Prompt:** The instructions and conversation messages sent to the model for one answer.
- **Context:** The prior messages and selected material the model can see for the current answer.
- **Branch/thread:** A separate follow-up conversation attached to a selected passage.
- **Input tokens:** Model-readable tokens sent into a request. The semantic evaluator total adds embedding-input tokens to chat-input tokens; runtime session aggregation currently omits the embedding part.
- **Embedding/retrieval:** An embedding converts text into a numeric vector. Cosine similarity ranks candidate prior turns by proximity to the query vector; here it selects context, while a separate chat model writes the answer.
- **Output tokens:** Tokens generated in the model answer; longer answers use more.
- **Latency:** Time measured for the chat-completion request in this evaluator. It excludes prompt construction and the semantic embedding request, so it is not end-to-end user wait time for semantic retrieval.
- **Paired comparison:** Each strategy is tested on the same case, so differences are compared case by case.
- **Bootstrap interval:** A resampling-based range showing uncertainty in the average estimated difference.
- **Wilcoxon signed-rank test:** A paired test that does not assume normally distributed differences; with six pairs it has limited resolution and power.
- **Blinding:** Reviewers see randomly assigned X/Y/Z/W labels instead of strategy names to reduce expectation bias. Answer wording can still reveal clues, and one AI reviewer is not independent human validation.
- **Kappa:** A measure of agreement between reviewers beyond chance; it cannot be calculated with only one reviewer.
- **UX:** User experience: ease, clarity, effort, confidence, and satisfaction while completing a task.
