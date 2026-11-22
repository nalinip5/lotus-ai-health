import { useEffect, useState } from "react";
import { act, useApi, useLive, useToasts } from "./api";
import Dashboard from "./pages/Dashboard";
import Cases from "./pages/Cases";
import CaseDetail from "./pages/CaseDetail";
import Appeals from "./pages/Appeals";
import Notifications from "./pages/Notifications";

function useHash() {
  const [hash, setHash] = useState(() => window.location.hash || "#/dashboard");
  useEffect(() => {
    const on = () => { setHash(window.location.hash || "#/dashboard"); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

function Logo() {
  return (
    <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden>
      <rect width="34" height="34" rx="9" fill="#1d4a4c" />
      <path d="M17 8c3 3.2 3 8.6 0 12-3-3.4-3-8.8 0-12Z" fill="#7fd4c6" />
      <path d="M17 21c-3.5.3-7.3-1.6-9-5.2 3.8-.6 7.4 1.4 9 5.2Zm0 0c3.5.3 7.3-1.6 9-5.2-3.8-.6-7.4 1.4-9 5.2Z" fill="#b6ece3" />
      <path d="M11 25h12" stroke="#7fd4c6" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export default function App() {
  const hash = useHash();
  const live = useLive();
  const { data } = useApi<any>("/dashboard");
  const toasts = useToasts();
  const route = hash.replace(/^#\//, "").split("/");
  const feed = data?.feed ?? { paused: false, speed: 1 };
  const connected = live.endsWith("true");

  const nav = [
    { key: "dashboard", label: "Dashboard" },
    { key: "cases", label: "Cases" },
    { key: "appeals", label: "Appeals", badge: data?.kpis.pending_decisions },
    { key: "notifications", label: "Notifications", badge: data?.kpis.unread },
  ];
  const active = route[0] === "case" ? "cases" : route[0];

  let page;
  if (route[0] === "case" && route[1]) page = <CaseDetail id={route[1]} tab={route[2] || "summary"} />;
  else if (route[0] === "cases") page = <Cases />;
  else if (route[0] === "appeals") page = <Appeals />;
  else if (route[0] === "notifications") page = <Notifications />;
  else page = <Dashboard data={data} />;

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/dashboard">
          <Logo />
          <div>
            <div className="brand-name">Lotus AI Health</div>
            <div className="brand-sub">Real-time claim document integrity &amp; appeals</div>
          </div>
        </a>
        <nav className="nav">
          {nav.map((n) => (
            <a key={n.key} href={`#/${n.key}`} className={active === n.key ? "on" : ""}>
              {n.label}
              {!!n.badge && <span className="badge">{n.badge}</span>}
            </a>
          ))}
        </nav>
        <div className="feedctl">
          <span className={`live ${feed.paused || !connected ? "off" : ""}`}>
            <i />{!connected ? "OFFLINE" : feed.paused ? "PAUSED" : "LIVE"}
          </span>
          <button className="ghost" onClick={() => act("/feed", { paused: !feed.paused })}>
            {feed.paused ? "Resume feed" : "Pause feed"}
          </button>
          <div className="seg">
            {[1, 2, 4].map((s) => (
              <button key={s} className={feed.speed === s ? "on" : ""} onClick={() => act("/feed", { speed: s })}>
                {s}×
              </button>
            ))}
          </div>
        </div>
      </header>
      <div className="banner">
        Synthetic demonstration environment — all cases, patients, payers and letters are generated data. Not
        connected to payers or EHRs. No PHI.
      </div>
      <main>{page}</main>
      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
      </div>
    </>
  );
}
