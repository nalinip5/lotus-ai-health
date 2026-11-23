# Lotus AI Health

Real-time claim document integrity, exception review and appeals — an operations console on top of an
AI **agent layer** built with FastAPI + LangGraph.

Packets come in, a structuring agent turns them into a canonical record, deterministic rules decide whether
the packet is ready, and people resolve exceptions. When a payer denies a claim, an evidence agent fills a
FHIR `QuestionnaireResponse` with page-cited evidence, a rules engine evaluates the criteria, and an appeal
letter agent drafts an evidence-linked letter. **A human approves every appeal**, and only once every
assertion cites a verified source page.

> Synthetic demo: every case, patient, payer and letter is generated. No PHI, no payer/EHR connections.

---

## Quick start

```bash
make install          # pip + npm
make run              # builds the console, serves API + UI on http://localhost:8000
```

Or for development (hot reload on both sides):

```bash
make api              # http://localhost:8000/api   (OpenAPI docs at /docs)
make web              # http://localhost:5173       (proxies /api to :8000)
make test             # 24 tests: evaluator, integrity, idempotency, HITL, citation gate, restart-resume, API
```

Docker: `docker build -t lotus-ai-health . && docker run -p 8000:8000 -v lotus-data:/data lotus-ai-health`

### Deploy on Render

The repo includes a Render Blueprint (`render.yaml`): one Docker web service with a `/api/health` check and
auto-deploy on every push to `main`. In Render: **New → Blueprint → pick this repo → Apply**. The free plan has
no persistent disk, so the synthetic book of work is re-seeded on each restart; for durable data switch to a
paid plan and attach a disk at `/data`. To use a real model, set `LOTUS_LLM_PROVIDER` and `LOTUS_LLM_API_KEY`
in the service's Environment tab (never commit keys). GitHub Actions (`.github/workflows/ci.yml`) runs the
tests, the console build and a Docker build on every push and pull request.

It runs fully offline with a deterministic **mock LLM**. To use a real model, copy `.env.example` to `.env`
(or export the variables) and set `LOTUS_LLM_PROVIDER` to `openai`, `azure_openai` or `anthropic` with a key.
Every LLM call returns a schema-validated object, so the same agents work with any provider.

On first start the app seeds a book of work covering every status (ready, missing documents, identity
quarantine, unreadable / skewed pages, three open denials, one approved appeal) and then a live feed keeps
ingesting packets, submitting, acknowledging and denying claims. Pause it or change its speed from the header.

---

## Access control

The console is a **staff portal**: nothing is visible without signing in.

| Role | Can do |
|---|---|
| **staff** | Dashboard, cases, exceptions, evidence chat, **prepare / approve / write off appeals**, notifications |
| **admin** | Everything staff can, plus the **Team** page: add users, change roles, disable access, reset passwords |

- The first admin is created on first start from `LOTUS_ADMIN_EMAIL` / `LOTUS_ADMIN_PASSWORD`. On Render the
  password is generated (`generateValue`), so read it from the service's **Environment** tab. Locally, if it's
  unset, a random one is printed once in the server log.
- New users get a temporary password and must set their own at first sign-in.
- Passwords: PBKDF2-SHA256 (600k iterations). Sessions: HMAC-signed HttpOnly cookie, 12 h, `SameSite=Lax`,
  `Secure` on HTTPS. Changing a password, resetting it or disabling a user revokes their sessions.
- 5 failed sign-ins per email (20 per IP) lock sign-in for 15 minutes. At least one active admin is always kept.
- Every audit entry and appeal decision records the signed-in user (`Name (role)`), never a client-supplied name.
- Only `/api/health` and `/api/auth/login` are public.

> Free Render plan: the disk is not persistent, so team accounts added in the UI are lost on restart (the
> env-configured admin is recreated). Attach a disk at `/data` on a paid plan to keep them.

## What's in the console

| Screen | What it does |
|---|---|
| **Dashboard** | KPIs, live activity stream (SSE), cases by status, deadline watchlist with *Verify*, recent emails, “Ingest synthetic packet”. |
| **Cases** | Every packet with status, blockers, denial and deadline; filter chips. |
| **Case → Summary** | Readiness evaluation, payer checklist (*Mark received (HIM)*), quality findings (*Request rescan* / *Accept with rationale*), identity reconciliation (explicit choice + rationale), AI clinical summary, submission tracking, deadline, immutable audit log. |
| **Case → Evidence** | Document inventory with page chips (critical / minor / cited), criteria evaluation tree with quotes, top-4 diagnoses, guardrailed evidence chat, FHIR R4 export. |
| **Case → Appeal letter** | Payer denial notice beside the evidence-linked draft, AI triage, citation-integrity gate, *Remove unverified*, *Approve & send*, *Deny — write off*. |
| **Case → Agent runs** | Spans grouped by correlation id (agent / LLM / tool / rule), the idempotent stage ledger, LangGraph thread ids and which human step a thread is waiting on. |
| **Appeals** | Decision workbench: queue sorted by deadline, triage, assertions, one-click actions. Keys: `J`/`K` move, `P` prepare, `A` approve. |
| **Notifications** | Synthetic emails from workflow events, preview pane, per-category on/off. |

---

## Architecture

Two layers, as in the reference design: the **app layer** (the console and its API surface) gives people
appeal capabilities; the **agent layer** runs AI functions, tools and workflows. Workflows live in the agent
layer so agents are called locally as functions rather than over remote APIs. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for diagrams and the full mapping.

```
frontend/ (React + Vite)            backend/lotus/
  Dashboard · Cases · Case detail     api/routes.py        REST + SSE controllers (thin)
  Appeals · Notifications             workflows/intake.py  LangGraph: ingest → structure → (integrity ∥ intent-to-admit) → summary
        │  /api + /api/stream         workflows/appeal.py  LangGraph: diagnosis → evidence → triage → ⏸prepare → draft → ⏸decision → finalize
        ▼                             agents/              structuring, summary, intent-to-admit, diagnosis reasoning,
  FastAPI ── LangGraph ── agents                           evidence, evaluator (rules), triage, appeal letter, chat
        │                             agents/integrity.py  deterministic quality / identity / completeness / readiness
        ├─ DocStore (SQLite, Mongo-shaped collections)     store/ · services.py (repo, event bus, notifier)
        ├─ BlobStore (packets, structured output, letters) llm/   mock · OpenAI · Azure OpenAI · Anthropic, back-pressure
        └─ Checkpoints (LangGraph AsyncSqliteSaver)        observability.py (correlation ids, spans, optional OTLP→Arize)
```

### Design rules carried over from the reference architecture

- **Fixed workflows, not LLM-driven routing.** LangGraph is used for workflow definition, reusable nodes,
  durability and human-in-the-loop. Nodes are coarse (no “every function is a node”).
- **Every node is idempotent.** Each node records `(case, stage, input fingerprint)` in the `stage_runs`
  ledger; a retry or resume returns the stored output instead of calling the LLM or writing duplicates.
  State lives in nodes, not in the workflow. `test_intake_is_idempotent` and `test_appeal_survives_restart`
  prove it.
- **Durable HITL.** Appeal threads pause with `interrupt()` and resume with `Command(resume=…)` from the
  API, even after a restart (SQLite checkpointer, thread id `appeal:<case>:<notice date>`).
- **Rules decide, LLMs find.** Readiness, criteria evaluation and the citation gate are plain code. The LLM
  extracts, retrieves and drafts; its output is schema-validated.
- **FHIR contract.** Criteria are `Questionnaire`s, evidence is a `QuestionnaireResponse`; both export as
  FHIR R4 (`/api/fhir/...`), with citations on an answer extension.
- **One correlation id per transaction** across sync and async steps, on every span.

### Evidence evaluator semantics

The reference table was ambiguous (“at-least-one = more than 1”, “at-least-n = more than N”). Lotus uses:

| Operator | Met when |
|---|---|
| `all` | every child is true |
| `at-least-one` | ≥ 1 child true |
| `exactly-one` | exactly 1 child true (e.g. one level of care) |
| `at-least-n` | ≥ n children true |
| `not-exist` | no child true (exclusions / absence of a condition) |
| points (`>` or `>=`) | sum of points of true children compared with the group's points |

---

## API (selected)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/dashboard` · `/api/cases?status=` · `/api/cases/{id}` | console views |
| POST | `/api/cases/{id}/actions` | `mark_received`, `request_rescan`, `accept_finding`, `reconcile_identity`, `verify_deadline`, `submit` |
| POST | `/api/cases/{id}/appeal/prepare` | resume the appeal thread → draft letter |
| POST | `/api/cases/{id}/appeal/decision` | `approve` (gate must pass) or `deny` (rationale required) |
| POST | `/api/cases/{id}/appeal/letter/remove-unverified` | human edit, re-runs the gate |
| POST | `/api/cases/{id}/evidence` | Evidence workflow for any questionnaire (cached) |
| POST | `/api/evaluate` | Evidence evaluator API (Questionnaire + QuestionnaireResponse → result) |
| POST | `/api/cases/{id}/chat` | evidence chat (malicious → irrelevant guardrails → cited answer) |
| GET | `/api/cases/{id}/traces` | spans for the Agent runs tab |
| GET | `/api/fhir/Questionnaire/{id}` · `/api/fhir/cases/{id}/QuestionnaireResponse` | FHIR R4 export |
| GET | `/api/stream` | Server-Sent Events: activity, notifications, feed state |

Business-rule refusals return **409** with a readable message (shown as a toast in the console).

---

## From demo to production

The seams are in place; these are the swaps:

| Demo | Production |
|---|---|
| `DocStore` on SQLite | MongoDB / Cosmos DB (same 5-method interface: `put`, `insert_if_absent`, `get`, `find`, `delete`) |
| `BlobStore` on local disk | Azure Blob Storage (content-addressed paths already used) |
| Synthetic packets, mock OCR confidences | Azure AI Document Intelligence output feeding `structure_document` |
| In-process `EventBus` | Azure Service Bus topics for intake / denial events; SSE stays for the UI |
| `AsyncSqliteSaver` | LangGraph Postgres checkpointer |
| `X-Lotus-User` header | Entra ID (Azure AD) auth + role checks on approve / reconcile / write-off |
| Demo criteria questionnaires | Licensed criteria sets (InterQual, MCG, payer policies) loaded as Questionnaires |
| Spans in the DocStore | `LOTUS_OTEL_ENDPOINT` → Arize; keep Splunk for app logs |

Before real PHI: a BAA with every LLM / tracing vendor, prompt and trace redaction, encryption at rest for
blobs and checkpoints, and tenant isolation (per-customer collections, containers and criteria sets).

---

## Layout

```
lotus-ai-health/
├── backend/
│   ├── lotus/
│   │   ├── agents/        clinical.py · evidence.py · appeal.py · chat.py · integrity.py · schemas.py
│   │   ├── workflows/     intake.py · appeal.py · common.py (ledger, context)
│   │   ├── llm/           base.py · providers.py · mock.py
│   │   ├── domain/        models.py (case + FHIR-lite) · policies.py (payers, CARCs, questionnaires)
│   │   ├── store/         docstore.py · blob.py
│   │   ├── sim/           generator.py (synthetic packets) · feed.py (seed + live feed)
│   │   ├── api/routes.py  ops.py  services.py  fhir.py  observability.py  container.py  main.py
│   └── tests/
├── frontend/src/          App.tsx · api.ts · pages/* · styles.css
├── docs/ARCHITECTURE.md
├── Dockerfile · Makefile · .env.example
```
