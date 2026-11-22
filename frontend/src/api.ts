import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

export const USER = "demo-user";

export class ApiError extends Error {}

export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json", "x-lotus-user": USER },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data as T;
}

/* ---------- live stream: a global "tick" that bumps on every server event ---------- */
let tick = 0;
let connected = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
let es: EventSource | null = null;
let timer: number | undefined;

function connect() {
  if (es) return;
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
  connect();
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

export async function act<T = any>(path: string, body: unknown = {}, ok?: string): Promise<T | null> {
  try {
    const r = await api<T>(path, body);
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
