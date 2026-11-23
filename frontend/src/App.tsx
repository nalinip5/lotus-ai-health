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

export const SITE_URL = "https://lotusbpmhealth.vercel.app";

function Logo() {
  // Lotus BPM Health line mark (same as the main site)
  return (
    <svg viewBox="-1 0 42 31" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 1C24 5 26.5 9.5 26.3 14C26 19 23 23 20 25.5C17 23 14 19 13.7 14C13.5 9.5 16 5 20 1Z" />
      <path d="M20 1V25.5" />
      <path d="M20 25.5C14 22 9 17 8.6 11C8.5 8 8.8 5.5 9 3.5C11 4.3 13 5.6 14.6 7.4" />
      <path d="M9 3.5L14.2 14" />
      <path d="M20 25.5C26 22 31 17 31.4 11C31.5 8 31.2 5.5 31 3.5C29 4.3 27 5.6 25.4 7.4" />
      <path d="M31 3.5L25.8 14" />
      <path d="M20 25.5C13 25 6.5 23 4 18C3 16 2 14 1.6 12C4 11.8 6.3 12.3 8.4 13" />
      <path d="M1.6 12L19 25" />
      <path d="M20 25.5C27 25 33.5 23 36 18C37 16 38 14 38.4 12C36 11.8 33.7 12.3 31.6 13" />
      <path d="M38.4 12L21 25" />
      <path d="M20 25.5C16 28 12 29.6 8 29.4C5 29.2 2 27.6 0.5 26C2.5 24.6 5 23.5 8 23" />
      <path d="M0.5 26C7 26.8 14 26.5 20 25.5" />
      <path d="M20 25.5C24 28 28 29.6 32 29.4C35 29.2 38 27.6 39.5 26C37.5 24.6 35 23.5 32 23" />
      <path d="M39.5 26C33 26.8 26 26.5 20 25.5" />
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
            <div className="brand-name">Lotus AI Health<sup>TM</sup></div>
            <div className="brand-sub">Claims integrity &amp; appeals · Lotus BPM Health</div>
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
          <a className="sitelink" href={SITE_URL} target="_blank" rel="noopener">lotusbpmhealth ↗</a>
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
      <footer className="footer">
        <span>Lotus AI Health — the revenue-cycle console of <a href={SITE_URL} target="_blank" rel="noopener">Lotus BPM Health</a>.</span>
        <span>Synthetic demo data · <a href="/docs" target="_blank" rel="noopener">API docs</a></span>
      </footer>
      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
      </div>
    </>
  );
}
