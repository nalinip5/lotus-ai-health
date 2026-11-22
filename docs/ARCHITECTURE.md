# Lotus AI Health — Architecture

## 1. Layers

```mermaid
flowchart LR
  subgraph App["App layer"]
    UI["Lotus console<br/>(React)"]
  end
  subgraph Agent["Agent layer — FastAPI + LangGraph"]
    API["REST + SSE controllers"]
    IW["Intake workflow"]
    AW["Appeal workflow (HITL)"]
    EW["Evidence workflow (cached)"]
    CH["Evidence chat"]
    AG["Agents: structuring · summary · intent-to-admit ·<br/>diagnosis reasoning · evidence · triage · appeal letter"]
    RU["Rules: integrity · readiness · evaluator · citation gate"]
  end
  DB[("DocStore<br/>cases · stage_runs · events ·<br/>notifications · traces")]
  BL[("Blob store<br/>packets · structured output · letters")]
  CP[("LangGraph checkpoints")]
  LLM["LLM provider<br/>(mock / OpenAI / Azure / Anthropic)"]
  OT["OTLP → Arize (optional)"]

  UI -- "/api, /api/stream" --> API
  API --> IW & AW & EW & CH
  IW & AW & EW & CH --> AG & RU
  AG --> LLM
  IW & AW --> CP
  AG & RU --> DB
  IW & AW --> BL
  AG -. spans .-> OT
```

## 2. Process flow

```mermaid
flowchart LR
  P["Packet (PDF / FHIR later)"] --> S["Structuring agent<br/>classify + section"]
  S --> I{"Integrity rules"}
  I -- "missing doc / scan defect" --> X["Exception<br/>(HIM: mark received / rescan / accept)"]
  I -- "identity conflict" --> Q["Quarantine<br/>(explicit reconciliation)"]
  I -- "all checks pass" --> R["Ready → Submit → Payer ack"]
  X --> I
  Q --> I
  R -- "denial notice" --> D["Denial intake<br/>(CARC, deadline: provisional)"]
  D --> DX["Diagnosis reasoning<br/>(top 4)"]
  DX --> E["Evidence agent → QuestionnaireResponse"]
  E --> EV["Evaluator (rules)"]
  EV --> T["Triage (advisory)"]
  T --> H1(("⏸ Human: prepare / write off"))
  H1 --> L["Appeal letter agent"]
  L --> G["Citation gate (rules)"]
  G --> H2(("⏸ Human: approve / write off"))
  H2 --> SENT["Appeal sent"]
```

## 3. Workflows

### Intake (`workflows/intake.py`, thread `intake:<case>`)

```mermaid
flowchart LR
  START --> ingest --> structure
  structure --> integrity
  structure --> intent_admit
  integrity --> summary
  intent_admit --> summary
  summary --> finalize --> END
```

| Node | Kind | Idempotency key | Output |
|---|---|---|---|
| ingest | tool | page checksums | manifest in blob, audit, events |
| structure | LLM ×documents (parallel) | page checksums | `classified_type`, canonical `sections` |
| integrity | rules | (recomputed; pure) | findings, blockers, status |
| intent_admit | LLM | page checksums | `admit_datetime` + quote |
| summary | LLM | checksums + admit time | summary JSON (≈1/10 size) |
| finalize | rules | (status, blockers) announcement key | Ready / Exceptions event + email |

### Appeal (`workflows/appeal.py`, thread `appeal:<case>:<notice date>`)

```mermaid
flowchart LR
  START --> diagnosis --> evidence --> triage --> await_prepare
  await_prepare -- prepare --> draft --> await_decision --> finalize
  await_prepare -- deny --> finalize
  finalize --> END
```

`await_prepare` and `await_decision` call `interrupt()`. The API checks which step a thread waits on
(`pending_step`) before resuming, so a stale button cannot skip a step. Approve is refused unless the citation
gate passed; write-off requires a rationale.

### Evidence workflow (`evidence_for`)

Fan-out per leaf question × per document (parallel, bounded by the LLM semaphore), rank by confidence and
document priority, drop below `evidence_confidence_threshold`, keep top 3. Cache key:
`case + questionnaire + record checksums + denial notice date`. The evaluator always re-runs (it is cheap
and pure), so criteria changes take effect without re-querying the model.

## 4. Data model

* **Case** — identifiers, payer + policy, documents → pages (text, OCR confidence, skew, checksum),
  findings, blockers, summary, admit time, denial, appeal, submission, audit log, correlation id.
* **Questionnaire / QuestionnaireResponse** (FHIR-lite) — groups carry `operator`, `n`, `arithmetic`,
  `points`; leaves carry retrieval `keywords`; answers carry `evidence[]` (doc, page, section, quote,
  confidence).
* **AppealLetter** — opening, closing, assertions (`text`, `citation`, `verified`), gate result.

Collections: `cases`, `stage_runs`, `events`, `notifications`, `traces`, `announcements`, `settings`.

## 5. Safety properties (tested)

| Property | Where | Test |
|---|---|---|
| Readiness is rule-based; any open blocker keeps a packet out of Ready | `agents/integrity.py` | `test_integrity_outcomes` |
| Critical scan defects can't be accepted; identity needs an explicit choice + rationale | `ops.py` | `test_human_resolution_paths` |
| Re-running a workflow repeats no LLM calls and emits no duplicate events | ledger | `test_intake_is_idempotent` |
| Paused appeals resume after restart without repeating work | checkpointer + ledger | `test_appeal_survives_restart` |
| Fabricated or missing citations are flagged; approval blocked | `citation_gate` + API | `test_citation_gate_rejects_fabricated_quote`, `test_console_api` |
| Chat rejects injection and off-topic questions; answers carry verified citations | `agents/chat.py` | `test_chat_guardrails` |
| Evaluator operator semantics | `agents/evidence.py` | `test_evaluator.py` |

## 6. Open decisions

1. **Who owns durability long-term** — keep LangGraph checkpoints for AI sub-flows only, and let a BPM /
   case engine own the long-running case if one is adopted, so two engines never checkpoint the same work.
2. **Token budget vs. parallelism** — `LOTUS_LLM_MAX_CONCURRENCY` is a global semaphore today; per-tenant
   budgets and queue-based back-pressure (Service Bus sessions) are the next step at volume.
3. **Criteria licensing** — demo questionnaires are illustrative; licensed sets plug in as Questionnaires.
4. **Exception taxonomy** — current kinds: completeness, identity, quality. Add coding and eligibility as
   the payer rules grow.
