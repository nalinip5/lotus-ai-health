import { useState } from "react";
import { money, STATUS_TONE, time, useApi } from "../api";

const FILTERS: [string, string][] = [
  ["All", ""], ["Ready", "ready"], ["Exceptions", "exception"], ["Awaiting rescan", "awaiting_rescan"],
  ["Quarantined", "quarantined"], ["Submitted", "sent,acknowledged"], ["Denied / appeals", "denied,appeal_pending"],
  ["Closed", "appeal_sent,written_off,returned,archived"],
];

export default function Cases() {
  const [filter, setFilter] = useState("");
  const { data } = useApi<any[]>(`/cases${filter ? `?status=${filter}` : ""}`);
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Cases</h1>
          <p className="sub">Every packet in the workflow — click a row for the summary, evidence and letters.</p>
        </div>
      </div>
      <div className="chips">
        {FILTERS.map(([label, v]) => (
          <button key={label} className={filter === v ? "on" : ""} onClick={() => setFilter(v)}>{label}</button>
        ))}
      </div>
      <section className="card flush">
        <table className="rows">
          <thead>
            <tr><th>Case</th><th>Patient</th><th>Payer</th><th className="r">Billed</th><th>Status</th>
              <th className="r">Blockers</th><th>Denial</th><th>Deadline</th><th>Last activity</th></tr>
          </thead>
          <tbody>
            {(data ?? []).map((c) => (
              <tr key={c.id} onClick={() => (window.location.hash = `#/case/${c.id}/summary`)}>
                <td className="mono strong">{c.id}</td>
                <td className="mono">{c.patient_token}</td>
                <td>{c.payer}</td>
                <td className="r mono">{money(c.billed)}</td>
                <td><span className={`pill ${STATUS_TONE[c.status]}`}>{c.status_label}</span></td>
                <td className="r mono">{c.blockers}</td>
                <td className="mono">{c.denial_code ?? "—"}</td>
                <td className="mono">{c.deadline ? `${c.deadline} · ${c.days_left}d` : "—"}</td>
                <td className="mono dim">{time(c.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.length === 0 && <div className="empty">No cases match this filter.</div>}
      </section>
    </>
  );
}
