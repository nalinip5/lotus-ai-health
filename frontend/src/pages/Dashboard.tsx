import { act, time } from "../api";

const TILES: [string, string, (k: any) => string][] = [
  ["Active cases", "active", () => "in workflow"],
  ["Ready to submit", "ready", () => "all checks passed"],
  ["Exceptions", "exceptions", () => "blocked from Ready"],
  ["Quarantined", "quarantined", () => "identity conflicts"],
  ["Denials", "denials", () => "notices ingested"],
  ["Appeal decisions", "decided", (k) => `${k.approved} approved · ${k.written_off} written off`],
  ["Pending decisions", "pending_decisions", () => "in the appeal queue"],
  ["Unread emails", "unread", () => "notification center"],
];

const STATUS_BAR: [string, string, string][] = [
  ["ready", "Ready", "var(--ok)"], ["exception", "Exceptions", "var(--warn)"],
  ["awaiting_rescan", "Awaiting rescan", "var(--info)"], ["quarantined", "Quarantined", "var(--violet)"],
  ["processing", "Processing", "var(--muted)"],
];

export default function Dashboard({ data }: { data: any }) {
  if (!data) return <div className="empty">Loading…</div>;
  const k = data.kpis;
  const total = STATUS_BAR.reduce((s, [key]) => s + data.by_status[key], 0) || 1;
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Operations dashboard</h1>
          <p className="sub">Live view of packet integrity, exceptions, denials and appeals. Demo clock {data.today}.</p>
        </div>
        <div className="pill-note">Agent layer · LLM <b>{data.llm}</b> · LangGraph workflows</div>
      </div>

      <div className="tiles">
        {TILES.map(([label, key, sub]) => (
          <div className="tile" key={key}>
            <div className="tile-label">{label}</div>
            <div className="tile-value">{k[key]}</div>
            <div className="tile-sub">{sub(k)}</div>
          </div>
        ))}
      </div>

      <div className="grid-2">
        <div className="stack">
          <section className="card">
            <h2>Live activity</h2>
            <ul className="feed">
              {data.events.map((e: any) => (
                <li key={e.id} className={`tone-${e.tone}`}>
                  <div className="feed-title">{e.title}</div>
                  {e.case_id ? <a href={`#/case/${e.case_id}/summary`}>{e.detail}</a> : <span>{e.detail}</span>}
                  <div className="mono dim">{time(e.at)}{e.case_id ? ` · ${e.case_id}` : ""}</div>
                </li>
              ))}
            </ul>
          </section>
          <section className="card">
            <h2>Cases by status</h2>
            <div className="stackbar">
              {STATUS_BAR.map(([key, label, color]) => data.by_status[key] > 0 && (
                <div key={key} title={`${label}: ${data.by_status[key]}`}
                     style={{ flex: data.by_status[key] / total, background: color }} />
              ))}
            </div>
            <div className="legend">
              {STATUS_BAR.map(([key, label, color]) => (
                <a key={key} href="#/cases"><i style={{ background: color }} />{label} · {data.by_status[key]}</a>
              ))}
            </div>
          </section>
        </div>
        <div className="stack">
          <section className="card">
            <h2>Deadline watchlist</h2>
            {data.watchlist.length === 0 ? <div className="empty">No open appeal deadlines.</div> : (
              <table>
                <thead><tr><th>Case</th><th>Deadline</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {data.watchlist.map((w: any) => (
                    <tr key={w.id}>
                      <td><a className="mono" href={`#/case/${w.id}/summary`}>{w.id}</a></td>
                      <td className="mono">{w.deadline} · <span className={`days ${w.days_left <= 10 ? "hot" : ""}`}>{w.days_left}d</span></td>
                      <td><span className={`pill ${w.verified ? "ok" : "warn"}`}>{w.verified ? "verified" : "provisional"}</span></td>
                      <td>{!w.verified && <button className="sm" onClick={() => act(`/cases/${w.id}/actions`, { action: "verify_deadline" }, `Deadline verified for ${w.id}`)}>Verify</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="note">Deadlines are provisional until an authorized reviewer verifies the payer's terms.</p>
          </section>
          <section className="card">
            <h2>Recent email notifications</h2>
            {data.notifications.map((n: any) => (
              <a key={n.id} className="notif-row" href="#/notifications">
                <span className={n.read ? "" : "strong"}>{n.subject}</span>
                <span className="dim mono">{time(n.at)}</span>
              </a>
            ))}
            <a className="link-more" href="#/notifications">Open notification center →</a>
          </section>
          <section className="card">
            <h2>Simulate intake</h2>
            <p className="note">Generate a new synthetic packet and run it through the intake workflow
              (structuring → integrity ∥ intent-to-admit → summary).</p>
            <button onClick={async () => {
              const r = await act<any>("/packets", {});
              if (r) window.location.hash = `#/case/${r.id}/summary`;
            }}>Ingest synthetic packet</button>
          </section>
        </div>
      </div>
    </>
  );
}
