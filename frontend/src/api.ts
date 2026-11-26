import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

/* ---------- session: a tiny store the whole app listens to ---------- */
export type Me = { email: string; name: string; role: "admin" | "staff"; must_change_password: boolean; auth_disabled?: boolean };
let me: Me | null | undefined = undefined; // undefined = not checked yet
const meListeners = new Set<() => void>();
export function setMe(v: Me | null) { me = v; meListeners.forEach((l) => l()); if (!v) closeStream(); }
export function useMe() {
  return useSyncExternalStore((cb) => { meListeners.add(cb); return () => meListeners.delete(cb); }, () => me);
}

export async function api<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("/auth/login")) setMe(null);
  if (!res.ok) throw new ApiError(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`, res.status);
  return data as T;
}

/* ---------- live stream: a global "tick" that bumps on every server event ---------- */
let tick = 0;
let connected = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
let es: EventSource | null = null;
let timer: number | undefined;

function closeStream() { es?.close(); es = null; connected = false; }

function connect() {
  if (es || !me) return;
  es = new EventSource("/api/stream");
  es.addEventListener("hello", () => { connected = true; emit(); });
  const bump = () => {
    // Coalesce bursts (one workflow emits several events) into one refresh.
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { tick++; emit(); }, 250);
  };
  ["event", "notification", "feed", "refresh"].forEach((k) => es!.addEventListener(k, bump));
  es.onerror = () => { connected = false; emit(); };
}

export function useLive() {
  connect();  // no-op until signed in
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb); },
    () => `${tick}:${connected}`,
  );
}

export function refreshNow() { tick++; emit(); }

export function useApi<T = any>(path: string | null, live = true) {
  const liveKey = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pathRef = useRef(path);
  pathRef.current = path;
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (pathRef.current === path) { setData(d); setError(null); }
    } catch (e) { setError((e as Error).message); }
  }, [path]);
  useEffect(() => { load(); }, [load, live ? liveKey.split(":")[0] : 0]);
  return { data, error, reload: load, setData };
}

/* ---------- toasts ---------- */
type Toast = { id: number; text: string; tone: "ok" | "bad" };
let toasts: Toast[] = [];
const toastListeners = new Set<() => void>();
export function toast(text: string, tone: "ok" | "bad" = "ok") {
  const t = { id: Date.now() + Math.random(), text, tone };
  toasts = [...toasts, t];
  toastListeners.forEach((l) => l());
  setTimeout(() => { toasts = toasts.filter((x) => x.id !== t.id); toastListeners.forEach((l) => l()); }, 4200);
}
export function useToasts() {
  return useSyncExternalStore((cb) => { toastListeners.add(cb); return () => toastListeners.delete(cb); }, () => toasts);
}

export async function act<T = any>(path: string, body: unknown = {}, ok?: string, method?: string): Promise<T | null> {
  try {
    const r = await api<T>(path, body, method);
    if (ok) toast(ok);
    refreshNow();
    return r;
  } catch (e) {
    toast((e as Error).message, "bad");
    return null;
  }
}

/* ---------- formatting ---------- */
export const money = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
export const time = (iso: string) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toISOString().slice(11, 19) + " UTC";
};
export const STATUS_TONE: Record<string, string> = {
  ready: "ok", exception: "warn", awaiting_rescan: "info", quarantined: "violet", processing: "muted",
  sent: "muted", acknowledged: "ok", denied: "bad", appeal_pending: "warn", appeal_sent: "ok",
  written_off: "muted", returned: "bad", archived: "muted",
};
