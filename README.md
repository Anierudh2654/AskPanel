# AskPanel

AskPanel is a local chat workspace with conversation history and anchored AskPanel branches. Each browser profile gets its own persistent local identity. Selecting assistant text opens a separate modal conversation that receives a context branch from the main chat; branch replies do not alter main-chat history. Closing it leaves a clickable anchor over the selected text. Per-call prompt/token records remain stored for future comparisons.

## Local Setup

Prerequisites: Node.js 20+, Python 3.11 or 3.12, and (for PostgreSQL) Docker Desktop with its Linux engine running. The API also supports a SQLite development fallback.

### Quick local run (Windows PowerShell)

The backend reads its provider settings from the git-ignored `apps/api/.env`. The frontend never receives the Nebius key. The chat API already sends requests to Nebius using the OpenAI-compatible HTTP interface; you do not need to run a separate Python snippet or install the OpenAI SDK.

#### A — Backend and Nebius chat API

In Terminal A, from the repository root:

```powershell
cd apps/api
..\..\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Before starting, make sure `apps/api/.env` has `LLM_PROVIDER=nebius`, your secret in `NEBIUS_API_KEY`, a valid Nebius model ID in `LLM_MODEL`, and the endpoint in `NEBIUS_BASE_URL`. The configured Token Factory endpoint is `https://api.tokenfactory.nebius.com/v1/` and the model is `deepseek-ai/DeepSeek-V4.1-Flash-1017`. Keep this key out of `apps/web/.env.local`, source control, and chat. Restart the API after changing `.env`.

The API is ready when http://localhost:8000/health responds successfully. Open http://localhost:8000/docs for the interactive API docs.

#### B — Frontend

In Terminal B, from the repository root:

```powershell
cd apps/web
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
npm install
npm run dev
```

Open http://localhost:3000. Start a chat and send a message. A streamed answer confirms the whole frontend → API → Nebius path is working. If generation fails, check Terminal A for the provider error and verify the model ID, token permissions/balance, and endpoint.

#### C — Analytics and evaluation

The app records per-call provider, model, token usage, timing, and prompt version in the local database. Use the paired context evaluation to compare full vs relevant branch context; add `--include-sequential` to include a normal single-chat follow-up baseline. Or export stored data after using the chat:

```powershell
cd apps/api
..\..\.venv\Scripts\Activate.ps1
python ..\..\scripts\evaluate_context.py --context-budget 1800 --seed 17
```

This makes real model calls for each evaluation case and writes results under `apps/api/evaluation/runs/`. Have reviewers score `ratings_blinded.csv` without looking at `blind_key.json`, then analyze a completed run:

```powershell
python ..\..\scripts\evaluate_context.py --analyze --output evaluation\runs\<run-folder>
```

For a CSV export of recorded app activity, run one of these from `apps/api`:

```powershell
python ..\..\scripts\export_data.py llm_calls
python ..\..\scripts\export_data.py events
python ..\..\scripts\export_data.py sessions
```

`llm_calls` exports usage and timing fields but excludes raw assembled prompts and response text. The evaluator and exports require the API database to have been initialized with `python -m alembic upgrade head`.

### Optional PostgreSQL setup

1. Start Docker Desktop, then run `docker compose up -d postgres` from the workspace root.
2. Set `DATABASE_URL` in `apps/api/.env` to `postgresql+asyncpg://askpanel:askpanel@localhost:5432/askpanel`, then apply migrations. Nebius endpoint, model, and key are configured as above. Restart the API after changing `.env`.
3. Other backend choices are `LLM_PROVIDER=openai` with `OPENAI_API_KEY` and optional `OPENAI_BASE_URL`, or `LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`. For all providers, `LLM_MODEL` must be a model ID enabled for that provider. `JUDGE_API_KEY` and `JUDGE_MODEL` are separate placeholders for the judge pipeline, which is not yet implemented. Never put provider keys in the web env file.
4. Fill in `apps/api/app/services/llm/pricing.yaml` with current USD per-million-token prices. The values start as `null` placeholders and are not used to invent cost data.
5. Apply migrations with `python -m alembic upgrade head`, then run the API and web commands above.

Generate a random JWT secret with `openssl rand -hex 32` (or an equivalent cryptographically secure generator). The `.env.example` values are placeholders, not production credentials. The local default is not suitable for deployment.

## Use The Chat

Open http://localhost:3000. No study code, consent screen, or admin login is needed. The first visit creates a device identity in browser local storage. The sidebar lists main chats; choose **New chat** to start another. Select text in an assistant answer and choose **Ask** to open an isolated context branch. Ask follow-ups in its modal; close it to leave a clickable anchor in the answer. Reopening the anchor restores that branch. Branch messages are stored separately and do not enter the main conversation prompt. The default `relevant` strategy includes the parent answer and selects earlier turns using lexical overlap; set `CONTEXT_STRATEGY=full` to restore the baseline.

## Tests

From `apps/api`, run `python -m pytest` for context isolation/cutoff, device identity, SSE persistence/edit, isolated thread behavior, and evaluation tests. From `apps/web`, run `npm run build` for TypeScript, lint, and production compilation. See [apps/api/evaluation/README.md](apps/api/evaluation/README.md) for the paired context benchmark, including its optional normal single-chat baseline. It records provider token usage and creates a blinded rating sheet; mock-provider outputs are only a pipeline smoke test. `scripts/export_data.py` can export stored calls without raw prompts; there is no analytics/admin UI in this simplified version.

## Readiness Boundary

Current scope: per-browser local identity, main-chat history, server-side provider calls with SSE, isolated AskPanel branches, clickable anchor reopening, user-message edits, and stored prompt/token/timing records for later usage comparisons. The old study endpoints and tables remain in the API/database for possible later use but are no longer part of the chat UI.

This is not pilot-ready and does not yet satisfy every LLD acceptance criterion. Still outstanding: complete quiz/survey participant flow and scoring coverage; snapshot/checkpoint summarization; robust detached generation/cancel/restart handling; complete metric definitions and inferential stats/mixed models/Holm correction; LLM judge provider/worker/rubrics/human agreement UI; full admin compare/session detail/replay; golden cross-markdown anchor tests and Playwright acceptance suite; rate limiting, daily spend guard, privacy deletion, backup/deployment/a11y hardening. Do not recruit participants with this build until those pieces, the approved consent materials, tasks/answers, model settings, and ethics review are completed.

## Security note

The app currently uses Next.js 14.2.35. `npm audit --omit=dev` still reports high and critical production advisories on the Next 14 line; review and upgrade to a patched supported release before deployment. Do not use the development configuration or mock provider for real data collection.
