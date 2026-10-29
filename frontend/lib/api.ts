"use client";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type Session = { userId: string; name: string; token: string; wallet: "client" | "freelancer" | null };

const SESSIONS_KEY = "cc.sessions";
const ACTIVE_KEY = "cc.active";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* almacenamiento bloqueado: la sesión vive solo en memoria */
  }
  window.dispatchEvent(new Event("cc-session"));
}

export function sessions(): Session[] {
  return read<Session[]>(SESSIONS_KEY, []);
}

export function activeSession(): Session | null {
  const id = read<string | null>(ACTIVE_KEY, null);
  return sessions().find((s) => s.userId === id) ?? null;
}

export function setActive(userId: string) {
  write(ACTIVE_KEY, userId);
}

export function signOut() {
  const current = activeSession();
  write(SESSIONS_KEY, sessions().filter((s) => s.userId !== current?.userId));
  write(ACTIVE_KEY, sessions()[0]?.userId ?? null);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detail(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && "errors" in d) return ((d as { errors: string[] }).errors ?? []).join(" · ");
    return JSON.stringify(d);
  }
  return "Request failed";
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; json?: unknown; form?: FormData; token?: string | null } = {},
): Promise<T> {
  const token = opts.token === undefined ? activeSession()?.token : opts.token;
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  const res = await fetch(`${API_URL}${path}`, { method: opts.method ?? (body ? "POST" : "GET"), headers, body });
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json() : null;
  if (!res.ok) throw new ApiError(res.status, detail(data));
  return data as T;
}

/** Descarga autenticada (capturas, video, ZIP): devuelve un object URL para <img>/<video>/<a>. */
export async function blobUrl(path: string): Promise<{ url: string; headers: Headers }> {
  const token = activeSession()?.token;
  const res = await fetch(`${API_URL}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return { url: URL.createObjectURL(await res.blob()), headers: res.headers };
}

export async function signUp(name: string, wallet: Session["wallet"]): Promise<Session> {
  const created = await api<{ user: { user_id: string }; token: string }>("/users", { json: { name }, token: null });
  if (wallet) {
    await api("/me/demo-wallet", { json: { role: wallet }, token: created.token });
  }
  const session: Session = { userId: created.user.user_id, name, token: created.token, wallet };
  write(SESSIONS_KEY, [...sessions().filter((s) => s.userId !== session.userId), session]);
  write(ACTIVE_KEY, session.userId);
  return session;
}

export const EXPLORER = "https://stellar.expert/explorer/testnet";

export function short(hash?: string | null, n = 10) {
  if (!hash) return "—";
  const clean = hash.replace("sha256:", "");
  return clean.length > n * 2 ? `${clean.slice(0, n)}…${clean.slice(-4)}` : clean;
}
