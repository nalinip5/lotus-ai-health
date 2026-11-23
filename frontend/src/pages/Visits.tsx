import { useState } from "react";
import { act, money, time, useApi } from "../api";
import { RationaleButton } from "./CaseDetail";

const STATUS: Record<string, [string, string]> = {
  received: ["Received", "muted"], coding: ["Coding", "info"], review: ["Needs review", "warn"],
  returned: ["With clinician", "violet"], claimed: ["Claim created", "ok"], rejected: ["Rejected", "muted"],
};

export function RiskMeter({ score, size = "md" }: { score: number; size?: "md" | "lg" }) {
  const tone = score >= 60 ? "bad" : score >= 20 ? "warn" : "ok";
  return (
    <span className={`meter ${tone} ${size}`} title={`Estimated denial risk ${score}%`}>
      <i style={{ width: `${Math.max(4, score)}%` }} /><b>{score}%</b>
    </span>
  );
}

export default function Visits() {
  const [filter, setFilter] = useState("review");
  const { data } = useApi<any[]>(`/visits${filter ? `?status=${filter}` : ""}`);
  const { data: sum } = useApi<any>("/visits/summary");
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Visit-to-Claim</h1>
          <p className="sub">Telehealth visits from the Lotus BPM Health app, coded with cited evidence and checked for
            denial risks before they become claims.</p>
        </div>
      </div>
      {sum && (
        <div className="tiles">
          <div className="tile"><div className="tile-label">Needs review</div><div className="tile-value">{sum.in_review}</div><div className="tile-sub">{sum.returned} with clinicians</div></div>
          <div className="tile"><div className="tile-label">Claims created</div><div className="tile-value">{sum.claimed}</div><div className="tile-sub">clean professional claims</div></div>
          <div className="tile"><div className="tile-label">Denial risk at intake → at claim</div><div className="tile-value">{sum.avg_risk_at_intake}% → {sum.avg_risk_at_claim}%</div><div className="tile-sub">average across created claims</div></div>
          <div className="tile"><div className="tile-label">Fixes before submission</div><div className="tile-value">{sum.fixes_applied}</div><div className="tile-sub">{sum.first_pass_clean}% of visits clean on first pass</div></div>
        </div>
      )}
      <div className="chips">
        {[["Needs review", "review"], ["With clinician", "returned"], ["Claim created", "claimed"], ["All", ""]].map(([l, v]) => (
          <button key={l} className={filter === v ? "on" : ""} onClick={() => setFilter(v)}>{l}</button>
        ))}
      </div>
      <section className="card flush">
        <table className="rows">
          <thead><tr><th>Visit</th><th>Clinician</th><th>Payer</th><th>Type</th><th>Status</th><th>Denial risk</th><th className="r">Open risks</th><th className="r">Billed</th><th>Updated</th></tr></thead>
          <tbody>
            {(data ?? []).map((v) => (
              <tr key={v.id} onClick={() => (window.location.hash = `#/visit/${v.id}`)}>
                <td className="mono strong">{v.id}{v.version > 1 && <span className="pill muted">v{v.version}</span>}</td>
                <td>{v.clinician}</td>
                <td>{v.payer}</td>
                <td>{v.specialty}</td>
                <td><span className={`pill ${STATUS[v.status][1]}`}>{STATUS[v.status][0]}</span></td>
                <td><RiskMeter score={v.risk_score} /></td>
                <td className="r mono">{v.open_risks}{v.critical > 0 && <span className="pill bad">{v.critical} critical</span>}</td>
                <td className="r mono">{money(v.billed)}</td>
                <td className="mono dim">{time(v.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.length === 0 && <div className="empty">Nothing here.</div>}
      </section>
    </>
  );
}

function Highlighted({ note, quotes }: { note: string; quotes: string[] }) {
  // Mark each verified evidence quote inside the note text.
  let parts: (string | React.ReactElement)[] = [note];
  quotes.filter(Boolean).forEach((q, qi) => {
    parts = parts.flatMap((p) => {
      if (typeof p !== "string") return [p];
      const i = p.indexOf(q);
      if (i < 0) return [p];
      return [p.slice(0, i), <mark key={`${qi}-${i}`}>{q}</mark>, p.slice(i + q.length)];
    });
  });
  return <pre className="note-text">{parts}</pre>;
}

export function VisitDetail({ id }: { id: string }) {
  const { data, error } = useApi<any>(`/visits/${id}`);
  if (error) return <div className="empty">{error}</div>;
  if (!data) return <div className="empty">Loading…</div>;
  const v = data.visit;
  const review = data.pending_step === "review";
  const open = v.risks.filter((r: any) => r.status === "open");
  const quotes = v.codes.filter((c: any) => c.verified && c.quote).map((c: any) => c.quote);
  const post = (path: string, body: any, ok: string) => act(`/visits/${id}/${path}`, body, ok);
  return (
    <>
      <div className="crumbs"><a href="#/visits">Visit-to-Claim</a> / <span className="mono">{v.id}</span></div>
      <div className="casehead">
        <span className="mono strong">{v.id}</span>
        <span className={`pill ${STATUS[v.status][1]}`}>{STATUS[v.status][0]}</span>
        {v.version > 1 && <span className="pill muted">note v{v.version}</span>}
      </div>
      <div className="dim casemeta">{v.patient_token} · {v.mrn} · {v.payer} · {v.clinician} · {v.specialty} · {v.modality} · {v.visit_date}</div>

      <div className="grid-2">
        <div className="stack">
          <section className="card">
            <h2>Denial risk <span className="tag">rules + learned payer outcomes</span></h2>
            <div className="riskhead">
              <RiskMeter score={v.risk_score} size="lg" />
              {v.initial_risk_score != null && v.initial_risk_score !== v.risk_score && (
                <span className="dim">was {v.initial_risk_score}% when coded · {v.fixes_applied} fix(es) applied</span>
              )}
            </div>
            {v.risks.length === 0 && <div className="okbox">No denial risks found. Every diagnosis cites the note.</div>}
            {v.risks.map((r: any) => (
              <div key={r.id} className={`risk ${r.severity} ${r.status}`}>
                <div className="risk-top">
                  <span className={`pill ${r.severity === "critical" ? "bad" : r.severity === "high" ? "warn" : "muted"}`}>{r.severity}</span>
                  <b>{r.message}</b>
                  {r.status !== "open" && <span className="pill ok">{r.status}</span>}
                </div>
                {r.fix && <div className="dim">Fix: {r.fix}</div>}
                {r.rationale && <div className="dim">Accepted: {r.rationale}</div>}
                {r.status === "open" && (
                  <div className="dim small">
                    Resolving this → denial risk <b>{data.impact[r.id]}%</b>
                    {data.learned[r.id] && <> · payer learning ×{data.learned[r.id].multiplier} from {data.learned[r.id].n} outcome(s)</>}
                  </div>
                )}
                {review && r.status === "open" && (
                  <div className="row-actions">
                    {r.fix_action && <button className="sm primary" onClick={() => post("fix", { risk_id: r.id }, "Fix applied")}>Apply fix</button>}
                    {r.needs_clinician && <span className="pill violet">clinician must document</span>}
                    {!r.acceptable && !r.needs_clinician && <span className="pill bad">must fix — compliance</span>}
                    {r.acceptable && (
                      <RationaleButton label="Accept risk…" placeholder="Why is it OK to submit with this risk?"
                        onSubmit={(t) => post("accept-risk", { risk_id: r.id, rationale: t }, "Risk accepted")} />
                    )}
                  </div>
                )}
              </div>
            ))}
            <p className="note">Score = chance the payer denies, combining each open risk's base rate with what Lotus has
              learned from this payer's real outcomes. Staff can fix codes or accept non-critical risks; only the
              clinician can add documentation.</p>
          </section>

          <section className="card">
            <h2>Claim lines <span className="tag">evidence-bound coding</span></h2>
            <table>
              <thead><tr><th>Type</th><th>Code</th><th>Description</th><th>Source</th><th>Evidence</th></tr></thead>
              <tbody>
                {v.codes.map((c: any, i: number) => (
                  <tr key={i}>
                    <td className="mono">{c.kind}</td>
                    <td className="mono strong">{c.code}{c.modifiers.length > 0 && `-${c.modifiers.join("-")}`}</td>
                    <td>{c.description}</td>
                    <td><span className="pill muted">{c.source}</span></td>
                    <td>{c.kind === "ICD-10" ? (c.verified ? <span className="pill ok">✓ cited</span> : <span className="pill bad">no evidence</span>) : <span className="dim">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="kv"><span>Place of service</span><span className="mono">{v.pos ?? "unknown"}</span></div>
            <div className="kv"><span>Documented time</span><span className="mono">{v.documented_minutes != null ? `${v.documented_minutes} min` : "not documented"}</span></div>
            <div className="kv"><span>Billed</span><b className="mono">{money(v.billed)}</b></div>
          </section>

          {review && (
            <section className="card">
              <h2>Decision</h2>
              <div className="row-actions">
                <button className="primary" disabled={open.length > 0}
                  onClick={() => post("decision", { action: "approve" }, "Clean claim created")}>Approve &amp; create claim</button>
                <RationaleButton label="Return to clinician…" placeholder="What needs documenting? (blank = use the open risks)"
                  onSubmit={(t) => post("decision", { action: "return", reason: t }, "Returned to clinician")} />
                <RationaleButton label="Reject…" placeholder="Why isn't this billable?"
                  onSubmit={(t) => post("decision", { action: "reject", reason: t }, "Visit rejected")} />
              </div>
              {open.length > 0 && <p className="note">{open.length} open risk(s) must be fixed, accepted or returned before a claim can be created.</p>}
            </section>
          )}
          {v.case_id && (
            <section className="card">
              <h2>Claim</h2>
              <p>Professional claim <a className="mono" href={`#/case/${v.case_id}/summary`}>{v.case_id}</a> was created
                and passed through the integrity checks in the claims console.</p>
            </section>
          )}
          {v.return_reason && v.status === "returned" && (
            <section className="card"><h2>Returned to clinician</h2><p>{v.return_reason}</p>
              <p className="note">When {v.clinician} adds an addendum in the Lotus BPM Health app, the note is re-coded and re-checked automatically as a new version.</p></section>
          )}
        </div>

        <div className="stack">
          <section className="card">
            <h2>Visit note {quotes.length > 0 && <span className="tag">highlighted = cited evidence</span>}</h2>
            <Highlighted note={v.note} quotes={quotes} />
          </section>
          <section className="card">
            <h2>Audit log</h2>
            <div className="audit">
              {[...v.audit].reverse().map((a: any, i: number) => (
                <div key={i}><span className="mono dim">{time(a.at)} · {a.actor}</span> — <b>{a.action}</b><div>{a.detail}</div></div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
