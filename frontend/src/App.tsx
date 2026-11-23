import { useEffect, useState } from "react";
import { act, api, Me, setMe, useApi, useLive, useMe, useToasts } from "./api";
import { Logo, SITE_URL } from "./brand";
import Login, { ChangePassword } from "./pages/Login";
import Team from "./pages/Team";
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

export default function App() {
  const me = useMe();
  const toasts = useToasts();
  useEffect(() => { api<Me>("/auth/me").then(setMe).catch(() => setMe(null)); }, []);
  let body;
  if (me === undefined) body = <div className="empty">Loading…</div>;
  else if (me === null) body = <Login />;
  else if (me.must_change_password) body = <ChangePassword forced />;
  else body = <Console me={me} />;
  return (
    <>
      {body}
      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
      </div>
    </>
  );
}

function UserMenu({ me }: { me: Me }) {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState(false);
  return (
    <div className="usermenu">
      <button className="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="avatar">{me.name.slice(0, 1).toUpperCase()}</span>{me.name.split(" ")[0]}
        <span className="rolechip">{me.role}</span>
      </button>
      {open && (
        <div className="menu" onMouseLeave={() => setOpen(false)}>
          <div className="dim mono">{me.email}</div>
          <a href={SITE_URL} target="_blank" rel="noopener">Lotus BPM Health site ↗</a>
          <button onClick={() => { setPw(true); setOpen(false); }}>Change password</button>
          <button onClick={async () => { await api("/auth/logout", {}); setMe(null); }}>Sign out</button>
        </div>
      )}
      {pw && <div className="modal"><ChangePassword onDone={() => setPw(false)} /></div>}
    </div>
  );
}

function Console({ me }: { me: Me }) {
  const hash = useHash();
  const live = useLive();
  const { data } = useApi<any>("/dashboard");
  const route = hash.replace(/^#\//, "").split("/");
  const feed = data?.feed ?? { paused: false, speed: 1 };
  const connected = live.endsWith("true");

  const nav = [
    { key: "dashboard", label: "Dashboard" },
    { key: "cases", label: "Cases" },
    { key: "appeals", label: "Appeals", badge: data?.kpis.pending_decisions },
    { key: "notifications", label: "Notifications", badge: data?.kpis.unread },
    ...(me.role === "admin" ? [{ key: "team", label: "Team" }] : []),
  ];
  const active = route[0] === "case" ? "cases" : route[0];

  let page;
  if (route[0] === "case" && route[1]) page = <CaseDetail id={route[1]} tab={route[2] || "summary"} />;
  else if (route[0] === "cases") page = <Cases />;
  else if (route[0] === "appeals") page = <Appeals />;
  else if (route[0] === "notifications") page = <Notifications />;
  else if (route[0] === "team") page = <Team />;
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
          <span className={`live ${feed.paused || !connected ? "off" : ""}`}>
            <i />{!connected ? "OFFLINE" : feed.paused ? "PAUSED" : "LIVE"}
          </span>
          <button className="ghost" onClick={() => act("/feed", { paused: !feed.paused })}>
            {feed.paused ? "Resume feed" : "Pause feed"}
          </button>
          <UserMenu me={me} />
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
    </>
  );
}
