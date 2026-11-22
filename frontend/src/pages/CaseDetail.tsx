import { useState } from "react";
import { act, api, money, STATUS_TONE, time, useApi } from "../api";

export function RationaleButton({ label, placeholder, onSubmit, className = "" }:
  { label: string; placeholder: string; onSubmit: (r: string) => void; className?: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  if (!open) return <button className={`sm ${className}`} onClick={() => setOpen(true)}>{label}</button>;
  return (
    <div className="rationale">
      <input autoFocus value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)}
             onKeyDown={(e) => e.key === "Enter" && text.trim() && (onSubmit(text), setOpen(false))} />
      <button className="sm" disabled={!text.trim()} onClick={() => { onSubmit(text); setOpen(false); }}>Confirm</button>
      <button className="sm ghost-dark" onClick={() => setOpen(false)}>Cancel</button>
    </div>
  );
}

const TABS = [
  ["summary", "packet status & actions", "Summary"],
  ["evidence", "documents, pages, citations", "Evidence"],
  ["letter", "denial & appeal letters", "Appeal / decision letter"],
  ["runs", "LangGraph stages, LLM & tool spans", "Agent runs"],
];

export default function CaseDetail({ id, tab }: { id: string; tab: string }) {
  const { data, error } = useApi<any>(`/cases/${id}`);
  if (error) return <div className="empty">{error}</div>;
  if (!data) return <div className="empty">Loading…</div>;
  const c = data.case;
  return (
    <>
      <div className="crumbs"><a href="#/dashboard">Dashboard</a> / <a href="#/cases">Cases</a> / <span className="mono">{c.id}</span></div>
      <div className="casehead">
        <span className="mono strong">{c.id}</span>
        <span className={`pill ${STATUS_TONE[c.status]}`}>{data.row.status_label}</span>
      </div>
      <div className="dim casemeta">
        {c.patient_token} · {c.mrn} · {c.encounter} · {c.payer} · service {c.service_date} · {money(c.billed)}
      </div>
      <div className="tabs">
        {TABS.map(([key, hint, label]) => (
          <a key={key} href={`#/case/${c.id}/${key}`} className={tab === key ? "on" : ""}>
            <span className="hint">{key === "letter" && !c.denial ? "no denial yet" : hint}</span>
            <span>{label}</span>
          </a>
        ))}
      </div>
      {tab === "summary" && <SummaryTab data={data} />}
      {tab === "evidence" && <EvidenceTab data={data} />}
      {tab === "letter" && <LetterTab data={data} />}
      {tab === "runs" && <RunsTab data={data} />}
    </>
  );
}

const post = (id: string, body: any, ok: string) => act(`/cases/${id}/actions`, body, ok);

function SummaryTab({ data }: { data: any }) {
  const c = data.case;
  const open = c.blockers.filter((b: any) => !b.resolved);
  const identity = c.blockers.filter((b: any) => b.kind === "identity");
  const findings = c.findings.filter((f: any) => f.status !== "resolved");
  return (
    <div className="grid-2">
      <div className="stack">
        <section className="card">
          <h2>Readiness evaluation</h2>
          {open.length === 0 ? (
            <div className="okbox">All deterministic checks passed.</div>
          ) : open.map((b: any) => (
            <div key={b.id} className={`blocker ${b.kind}`}>
              <b>{{ completeness: "Packet completeness", identity: "Identity reconciliation", quality: "Scan quality" }[b.kind as string]}:</b> {b.message}
            </div>
          ))}
          <p className="note">Readiness is computed by rules, never by an LLM. Any open blocker keeps the packet out of Ready.</p>
        </section>

        <section className="card">
          <h2>Packet checklist — {c.policy_id}</h2>
          {data.checklist.map((i: any) => (
            <div className="check" key={i.doc_type}>
              <span className={`dot ${i.present ? "ok" : "bad"}`}>{i.present ? "✓" : "✗"}</span>
              <span>{i.doc_type}</span>
              {!i.present && c.status !== "returned" && (
                <button className="sm" onClick={() => post(c.id, { action: "mark_received", doc_type: i.doc_type }, `${i.doc_type} received — intake re-run`)}>
                  Mark received (HIM)
                </button>
              )}
            </div>
          ))}
        </section>

        <section className="card">
          <h2>Quality findings</h2>
          {findings.length === 0 && <div className="empty sm">No open findings.</div>}
          {findings.map((f: any) => (
            <div key={f.id} className={`finding ${f.severity}`}>
              <div><b>{f.kind.replace("_", " ")}</b> · {f.doc_id} p.{f.page} — {f.severity}
                <span className={`pill ${f.status === "open" ? "warn" : f.status === "accepted" ? "ok" : "info"}`}>{f.status.replace("_", " ")}</span></div>
              <div className="dim">{f.message}</div>
              {f.rationale && <div className="dim">Rationale: {f.rationale}</div>}
              {f.status === "open" && (
                <div className="row-actions">
                  <button className="sm" onClick={() => post(c.id, { action: "request_rescan", finding_id: f.id }, "Rescan requested from HIM")}>Request rescan</button>
                  {f.severity === "minor" && (
                    <RationaleButton label="Accept with rationale…" placeholder="Why is this page acceptable?"
                      onSubmit={(r) => post(c.id, { action: "accept_finding", finding_id: f.id, rationale: r }, "Finding accepted")} />
                  )}
                </div>
              )}
            </div>
          ))}
        </section>

        <section className="card">
          <h2>Clinical summary <span className="tag">Summary agent</span></h2>
          {!c.summary ? <div className="empty sm">Not summarized yet.</div> : (
            <>
              <div className="kv"><span>Admit type</span><b>{c.summary.admit_type}</b></div>
              <div className="kv"><span>Admission (intent-to-admit agent)</span><b className="mono">{c.admit_datetime ?? "not found"}</b></div>
              <p>{c.summary.clinical_course}</p>
              <ul className="bul">{c.summary.key_findings.map((k: string, i: number) => <li key={i}>{k}</li>)}</ul>
              <div className="kv"><span>Disposition</span><span>{c.summary.disposition}</span></div>
              {c.summary.timeline.length > 0 && (
                <div className="timeline">{c.summary.timeline.map((t: any, i: number) => (
                  <div key={i}><span className="mono">{t.date}</span> {t.event}</div>))}</div>
              )}
            </>
          )}
        </section>
      </div>

      <div className="stack">
        {identity.length > 0 && (
          <section className="card">
            <h2>Identity reconciliation</h2>
            {identity.map((b: any) => (
              <div key={b.id} className={`identity ${b.resolved ? "done" : ""}`}>
                <b>{b.resolved ? "Reconciled." : "Quarantined — patient/encounter mismatch."}</b>
                <div>{b.message.replace("Patient/encounter identifier conflict unresolved — ", "Identity agent: ")}</div>
              </div>
            ))}
            {c.status === "quarantined" && (
              <>
                <RationaleButton className="primary wide" label="Reconcile: cover sheet authoritative (note has typo)"
                  placeholder="Rationale (required)"
                  onSubmit={(r) => post(c.id, { action: "reconcile_identity", choice: "cover_authoritative", rationale: r }, "Identity reconciled")} />
                <RationaleButton className="wide" label="Reconcile: different patient — return packet"
                  placeholder="Rationale (required)"
                  onSubmit={(r) => post(c.id, { action: "reconcile_identity", choice: "different_patient", rationale: r }, "Packet returned")} />
                <p className="note">A rationale-only acceptance is refused — an identity conflict requires an explicit reconciliation choice with a recorded rationale.</p>
              </>
            )}
          </section>
        )}
        <section className="card">
          <h2>Submission tracking</h2>
          {c.submission ? (
            <>
              <div className="kv"><span>Sent</span><span className="mono">{c.submission.sent_at}</span></div>
              <div className="kv"><span>Receipt</span><span className="mono">{c.submission.receipt}</span></div>
              <div className="kv"><span>Payer ack</span><span>{c.submission.ack}{c.submission.ack_ref ? ` · ${c.submission.ack_ref}` : ""}</span></div>
            </>
          ) : c.status === "ready" ? (
            <button className="primary" onClick={() => post(c.id, { action: "submit" }, "Packet submitted")}>Submit packet to payer</button>
          ) : <div className="empty sm">Not yet submitted. Only Ready packets can be submitted.</div>}
        </section>
        <section className="card">
          <h2>Appeal deadline</h2>
          {!c.denial ? <div className="empty sm">No appeal deadline — no denial on file.</div> : (
            <>
              <div className="kv"><span>Denial</span><b>{c.denial.code}</b></div>
              <div className="kv"><span>Deadline</span><span className="mono">{c.denial.deadline} · {data.row.days_left}d</span></div>
              <div className="kv"><span>Status</span><span className={`pill ${c.denial.deadline_verified ? "ok" : "warn"}`}>{c.denial.deadline_verified ? `verified by ${c.denial.verified_by}` : "provisional"}</span></div>
              {!c.denial.deadline_verified && <button className="sm" onClick={() => post(c.id, { action: "verify_deadline" }, "Deadline verified")}>Verify against payer terms</button>}
            </>
          )}
        </section>
        <section className="card">
          <h2>Immutable audit log</h2>
          <div className="audit">
            {[...c.audit].reverse().map((a: any, i: number) => (
              <div key={i}><span className="mono dim">{time(a.at)} · {a.actor}</span> — <b>{a.action}</b><div>{a.detail}</div></div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function citedPages(c: any): Set<string> {
  const s = new Set<string>();
  c.appeal.letter?.assertions.forEach((a: any) => a.citation && a.verified && s.add(`${a.citation.doc_id}:${a.citation.page}`));
  return s;
}

function ResponseTree({ items, groups }: { items: any[]; groups: any }) {
  return (
    <ul className="tree">
      {items.map((it) => {
        const g = groups?.[it.linkId];
        return (
          <li key={it.linkId}>
            <div className="q">
              <span className={`dot ${it.answer ? "ok" : "bad"}`}>{it.answer ? "✓" : "✗"}</span>
              <span className="mono dim">{it.linkId}</span> {it.text}
              {g && <span className="tag">{g.rule} · {g.points !== undefined ? `${g.points} pts` : `${g.true}/${g.of}`}</span>}
            </div>
            {it.evidence?.map((e: any, i: number) => (
              <div key={i} className="quote">“{e.quote}” <span className="mono dim">{e.doc_id} p.{e.page} · {e.section} · conf {e.confidence}</span></div>
            ))}
            {it.item?.length > 0 && <ResponseTree items={it.item} groups={groups} />}
          </li>
        );
      })}
    </ul>
  );
}

function EvidenceTab({ data }: { data: any }) {
  const c = data.case;
  const cited = citedPages(c);
  const findingAt = (d: string, p: number) => c.findings.find((f: any) => f.doc_id === d && f.page === p && f.status !== "resolved" && f.status !== "accepted");
  return (
    <div className="grid-2">
      <div className="stack">
        <section className="card">
          <h2>Document inventory &amp; page quality <span className="tag">Structuring agent</span></h2>
          {c.documents.map((d: any) => (
            <div className="docrow" key={d.id}>
              <div>
                <b>{d.classified_type ?? d.declared_type}</b>
                {d.classified_type && d.classified_type !== d.declared_type && <span className="pill warn">declared {d.declared_type}</span>}
                <div className="mono dim">{d.id} · v{d.version} · {d.pages.length}p · sha256 {d.sha256.slice(0, 4)}…</div>
              </div>
              <div className="pages">
                {d.pages.map((p: any) => {
                  const f = findingAt(d.id, p.number);
                  const cls = f?.severity === "critical" ? "crit" : f ? "minor" : cited.has(`${d.id}:${p.number}`) ? "cited" : "";
                  return <span key={p.number} className={`chip ${cls}`} title={`${p.text.slice(0, 220)}\n\nOCR ${p.ocr_confidence}`}>p{p.number}</span>;
                })}
              </div>
            </div>
          ))}
          <p className="note">Page chips: red critical unreadable · amber minor open finding · teal cited in appeal evidence. Hover for page text.</p>
        </section>
        {c.appeal.response && data.questionnaire && (
          <section className="card">
            <h2>Criteria evaluation — {data.questionnaire.title}
              <span className={`pill ${c.appeal.evaluation.met ? "ok" : "bad"}`}>{c.appeal.evaluation.met ? "met" : "not met"}</span></h2>
            <p className="note">Evidence agent fills the FHIR QuestionnaireResponse; the evaluator applies group operators deterministically.
              {" "}<a href={`/api/fhir/cases/${c.id}/QuestionnaireResponse`} target="_blank">FHIR R4 export ↗</a></p>
            <ResponseTree items={c.appeal.response.item} groups={c.appeal.evaluation.groups} />
          </section>
        )}
      </div>
      <div className="stack">
        {c.appeal.diagnoses.length > 0 && (
          <section className="card">
            <h2>Top diagnoses <span className="tag">Diagnosis reasoning agent</span></h2>
            {c.appeal.diagnoses.map((d: any) => (
              <div key={d.rank} className="dx">
                <span className="rank">{d.rank}</span>
                <div><b>{d.description}</b> {d.icd10 && <span className="mono pill muted">{d.icd10}</span>}
                  <div className="dim">{d.rationale} · {d.doc_id} p.{d.page}</div></div>
              </div>
            ))}
          </section>
        )}
        <EvidenceChat id={c.id} />
        <section className="card">
          <h2>Submission manifest</h2>
          <div className="dim">packet v1 · {c.documents.reduce((s: number, d: any) => s + d.pages.length, 0)} pages · per-page sha256 checksums</div>
          {c.submission && <div className="dim">sent {c.submission.sent_at} · receipt {c.submission.receipt} · ack {c.submission.ack}</div>}
        </section>
      </div>
    </div>
  );
}

function EvidenceChat({ id }: { id: string }) {
  const [q, setQ] = useState("");
  const [log, setLog] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try {
      const r = await api(`/cases/${id}/chat`, { question: q });
      setLog((l) => [...l, { q, ...r }]);
      setQ("");
    } finally { setBusy(false); }
  };
  return (
    <section className="card">
      <h2>Evidence chat <span className="tag">guardrailed</span></h2>
      <div className="chatlog">
        {log.length === 0 && <div className="dim">Ask about this record, e.g. “What was the lactate?” or “When was the patient admitted?”</div>}
        {log.map((m, i) => (
          <div key={i} className="chatturn">
            <div className="you">{m.q}</div>
            <div className={`bot ${m.blocked ? "blocked" : ""}`}>
              {m.answer}
              {m.citations.map((ct: any, j: number) => (
                <div key={j} className="mono dim">{ct.verified ? "✓" : "⚠"} {ct.doc_type ?? ct.doc_id} p.{ct.page}</div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="chatin">
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} placeholder="Ask the record…" />
        <button disabled={busy} onClick={ask}>Ask</button>
      </div>
    </section>
  );
}

export function LetterTab({ data }: { data: any }) {
  const c = data.case;
  const step = data.pending_step;
  const letter = c.appeal.letter;
  return (
    <div className="grid-even">
      <section className="card">
        <h2>Decision letter — payer denial notice</h2>
        {c.denial ? <pre className="letter">{c.denial.letter_text}</pre> : <div className="empty">No denial on file.</div>}
      </section>
      <section className="card">
        <h2>Appeal letter — evidence-linked draft <span className="tag">Appeal-letter agent</span></h2>
        {!c.denial ? <div className="empty">No denial — nothing to appeal.</div> : (
          <>
            {c.appeal.triage && (
              <div className="triage">AI triage: <b>{c.appeal.triage.category.replace("_", " ")}</b> — confidence {c.appeal.triage.confidence}. {c.appeal.triage.recommendation}</div>
            )}
            {!letter && <div className="empty sm">No appeal letter yet.</div>}
            {letter && (
              <>
                <div className={`gate ${letter.gate_passed ? "ok" : "bad"}`}>
                  Citation integrity gate: {letter.gate_passed ? "passed — every assertion cites a verified source page" : `${letter.unverified} unverified assertion(s) — fix before approval`}
                </div>
                <pre className="letter">{letter.text}</pre>
                {letter.assertions.length === 0 && (
                  <div className="gate bad">No verified evidence remains — this appeal cannot be approved. Request documentation, a peer-to-peer review, or write off.</div>
                )}
              </>
            )}
            <div className="row-actions">
              {step === "prepare" && <button className="primary" onClick={() => act(`/cases/${c.id}/appeal/prepare`, {}, "Draft generated")}>Generate evidence-linked appeal draft</button>}
              {step === "decision" && letter && !letter.gate_passed && (
                <button onClick={() => act(`/cases/${c.id}/appeal/letter/remove-unverified`, {}, "Unverified assertions removed")}>Remove unverified assertions</button>
              )}
              {step === "decision" && <button className="primary" disabled={!letter?.gate_passed} onClick={() => act(`/cases/${c.id}/appeal/decision`, { action: "approve" }, "Appeal approved & sent")}>Approve &amp; send appeal</button>}
              {(step === "prepare" || step === "decision") && (
                <RationaleButton label="Deny — write off…" placeholder="Write-off rationale (required)"
                  onSubmit={(r) => act(`/cases/${c.id}/appeal/decision`, { action: "deny", rationale: r }, "Written off")} />
              )}
            </div>
            {c.appeal.decided_by && <p className="note">Decided by {c.appeal.decided_by} at {c.appeal.decided_at}{c.appeal.rationale ? ` — ${c.appeal.rationale}` : ""}.</p>}
            <p className="note">The drafting agent never invents evidence — assertions without a verified source are flagged, not filled in. Only a human approval sends an appeal.</p>
          </>
        )}
      </section>
    </div>
  );
}

function RunsTab({ data }: { data: any }) {
  const c = data.case;
  const { data: spans } = useApi<any[]>(`/cases/${c.id}/traces`);
  const groups: Record<string, any[]> = {};
  (spans ?? []).forEach((s) => (groups[s.correlation_id ?? "—"] ??= []).push(s));
  return (
    <div className="grid-2">
      <section className="card">
        <h2>Traces by correlation id</h2>
        <p className="note">Every sync and async step of a transaction shares one correlation id (exported to Arize via OpenTelemetry when configured).</p>
        {Object.entries(groups).reverse().map(([corr, list], idx) => (
          <details key={corr} className="trace" open={idx === 0}>
            <summary className="mono strong">{corr} <span className="dim">· {list.length} spans · {list.filter((s) => s.kind === "llm").length} LLM calls · {list.filter((s) => s.kind === "agent").map((s) => s.name.replace("agent.", "")).filter((v, i, a) => a.indexOf(v) === i).join(", ")}</span></summary>
            {list.map((s, i) => (
              <div key={i} className="span">
                <span className={`kind k-${s.kind}`}>{s.kind}</span>
                <span className="mono">{s.name}</span>
                <span className="dim">{Object.entries(s.attrs || {}).map(([k, v]) => `${k}=${v}`).join(" ")}</span>
                <span className={`mono r ${s.status === "error" ? "badtxt" : "dim"}`}>{s.duration_ms} ms</span>
              </div>
            ))}
          </details>
        ))}
      </section>
      <div className="stack">
        <section className="card">
          <h2>Idempotent stage ledger</h2>
          <p className="note">LangGraph checkpoints each thread; every node records (case, stage, input fingerprint). A retry or resume returns the stored output instead of repeating LLM/tool calls.</p>
          {data.stage_runs.map((r: any, i: number) => (
            <div key={i} className="kv"><span className="mono">{r.stage}</span><span className="mono dim">{r.fp}</span></div>
          ))}
        </section>
        <section className="card">
          <h2>Workflow threads</h2>
          <div className="kv"><span>Intake</span><span className="mono">intake:{c.id}</span></div>
          {c.appeal.thread_id && <div className="kv"><span>Appeal</span><span className="mono">{c.appeal.thread_id}</span></div>}
          <div className="kv"><span>Waiting on human</span><b>{data.pending_step ?? "—"}</b></div>
        </section>
      </div>
    </div>
  );
}
