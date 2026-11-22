import { useEffect, useState } from "react";
import { act, money, useApi } from "../api";
import { RationaleButton } from "./CaseDetail";

export default function Appeals() {
  const { data } = useApi<any>("/appeals");
  const [sel, setSel] = useState<string | null>(null);
  const pending: any[] = data?.pending ?? [];
  const current = sel && pending.some((p) => p.id === sel) ? sel : pending[0]?.id ?? null;
  const { data: detail } = useApi<any>(current ? `/cases/${current}` : null);

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || !current) return;
      const i = pending.findIndex((p) => p.id === current);
      const step = detail?.pending_step;
      if (e.key === "j") setSel(pending[Math.min(i + 1, pending.length - 1)]?.id);
      if (e.key === "k") setSel(pending[Math.max(i - 1, 0)]?.id);
      if (e.key === "p" && step === "prepare") act(`/cases/${current}/appeal/prepare`, {}, "Draft generated");
      if (e.key === "a" && step === "decision") act(`/cases/${current}/appeal/decision`, { action: "approve" }, "Appeal approved & sent");
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [current, pending, detail]);

  const c = detail?.case;
  const step = detail?.pending_step;
  const letter = c?.appeal.letter;
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Appeal decision workbench</h1>
          <p className="sub">One screen per appeal: triage recommendation, evidence, deadline — then approve or deny.
            Shortcuts: J/K move · P prepare · A approve.</p>
        </div>
        <div className="pill-note">{pending.length} pending · {data?.decided.length ?? 0} decided</div>
      </div>
      <div className="grid-side">
        <div className="stack">
          {pending.map((p) => (
            <button key={p.id} className={`queue ${p.id === current ? "on" : ""}`} onClick={() => setSel(p.id)}>
              <div className="qtop"><span className="mono strong">{p.id}</span>
                <span className={`days ${p.days_left <= 10 ? "hot" : ""}`}>{p.days_left}d</span></div>
              <div className="dim">{p.payer} · {p.denial_code} · {money(p.billed)}</div>
              <div className="qstat">{p.appeal_status === "drafted" ? "draft ready" : "evidence ready"}
                {p.triage && <> · triage {p.triage.confidence}</>}</div>
            </button>
          ))}
          {pending.length === 0 && <div className="empty">Queue is clear.</div>}
          <h3 className="sectionlabel">Recently decided</h3>
          {(data?.decided ?? []).slice(0, 8).map((d: any) => (
            <a key={d.id} className="decided" href={`#/case/${d.id}/letter`}>
              <span className="mono">{d.id}</span>
              <span className={`pill ${d.appeal_status === "sent" ? "ok" : "muted"}`}>{d.appeal_status === "sent" ? "approved" : "written off"}</span>
            </a>
          ))}
        </div>
        {c ? (
          <section className="card">
            <h2>Decision required — {c.id}</h2>
            <div className="dim">{c.patient_token} · {c.payer} · {money(c.billed)}</div>
            <div className="decision-grid">
              <div className="deadline-box"><b>{detail.row.days_left}</b> day(s) to {c.denial.deadline_verified ? "verified" : "provisional"} deadline</div>
              <div><b>Denial {c.denial.code}:</b> {c.denial.reason} <span className="dim">(notice {c.denial.notice_date})</span></div>
            </div>
            {c.appeal.triage && (
              <div className="triage">AI triage: <b>{c.appeal.triage.category.replace("_", " ")}</b> — confidence {c.appeal.triage.confidence}. {c.appeal.triage.recommendation}</div>
            )}
            <p className="note">AI recommendation is advisory only — the decision and the evidence gate are yours.</p>
            {c.appeal.evaluation && (
              <div className="kv"><span>Criteria ({detail.questionnaire?.title})</span>
                <span className={`pill ${c.appeal.evaluation.met ? "ok" : "bad"}`}>{c.appeal.evaluation.met ? "met" : "not met"}</span></div>
            )}
            <h3 className="sectionlabel">Evidence assertions</h3>
            {!letter ? <div className="empty sm">No draft yet.</div> : letter.assertions.map((a: any, i: number) => (
              <div key={i} className={`assert ${a.verified ? "ok" : "bad"}`}>
                <span>{a.verified ? "✓" : "⚠"}</span>
                <div>{a.text}{a.citation && <div className="mono dim">{a.citation.doc_id} p.{a.citation.page}</div>}</div>
              </div>
            ))}
            <div className="row-actions">
              {step === "prepare" && <button className="primary" onClick={() => act(`/cases/${c.id}/appeal/prepare`, {}, "Draft generated")}>Prepare appeal (auto-attach evidence)</button>}
              {step === "decision" && letter && !letter.gate_passed && <button onClick={() => act(`/cases/${c.id}/appeal/letter/remove-unverified`, {}, "Unverified assertions removed")}>Remove unverified</button>}
              {step === "decision" && <button className="primary" disabled={!letter?.gate_passed} onClick={() => act(`/cases/${c.id}/appeal/decision`, { action: "approve" }, "Appeal approved & sent")}>Approve &amp; send appeal</button>}
              <RationaleButton label="Deny — write off…" placeholder="Write-off rationale (required)"
                onSubmit={(r) => act(`/cases/${c.id}/appeal/decision`, { action: "deny", rationale: r }, "Written off")} />
            </div>
            <div className="links">
              <a href={`#/case/${c.id}/letter`}>View / edit letter →</a>
              <a href={`#/case/${c.id}/evidence`}>Evidence &amp; criteria →</a>
              <a href={`#/case/${c.id}/runs`}>Agent runs →</a>
            </div>
          </section>
        ) : <section className="card"><div className="empty">Select an appeal.</div></section>}
      </div>
    </>
  );
}
