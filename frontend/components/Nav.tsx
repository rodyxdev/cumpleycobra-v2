"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useSyncExternalStore } from "react";

import { activeSession, Session, sessions, setActive, signOut } from "@/lib/api";

function subscribe(onChange: () => void) {
  window.addEventListener("cc-session", onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener("cc-session", onChange);
    window.removeEventListener("storage", onChange);
  };
}

function snapshot(): string {
  try {
    return `${window.localStorage.getItem("cc.sessions") ?? ""}|${window.localStorage.getItem("cc.active") ?? ""}`;
  } catch {
    return "|";
  }
}

/** Sesiones guardadas en el navegador. `undefined` mientras se hidrata (en el servidor no hay sesión). */
export function useSessions(): { session: Session | null; all: Session[] } | undefined {
  const raw = useSyncExternalStore(subscribe, snapshot, () => null);
  return useMemo(() => (raw === null ? undefined : { session: activeSession(), all: sessions() }), [raw]);
}

export function useSession(): Session | null | undefined {
  return useSessions()?.session;
}

export function Nav() {
  const state = useSessions();
  const router = useRouter();
  const session = state?.session;

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <span className="rounded-md bg-emerald-700 px-1.5 py-0.5 text-sm text-white">C&C</span>
          Cumple&amp;Cobra
          <span className="hidden text-xs font-normal text-slate-500 sm:inline">verified delivery, then payment</span>
        </Link>
        <nav className="flex items-center gap-4 text-sm">
          {session && <Link className="text-slate-700 hover:text-slate-900" href="/tasks">Tasks</Link>}
          {session && <Link className="text-slate-700 hover:text-slate-900" href="/tasks/new">New task</Link>}
          {session ? (
            <span className="flex items-center gap-2">
              <select
                aria-label="Active user"
                className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
                value={session.userId}
                onChange={(e) => {
                  setActive(e.target.value);
                  router.push("/tasks");
                }}
              >
                {state?.all.map((s) => (
                  <option key={s.userId} value={s.userId}>
                    {s.name}{s.wallet ? ` (${s.wallet} wallet)` : ""}
                  </option>
                ))}
              </select>
              <button className="text-slate-500 hover:text-slate-800" onClick={() => { signOut(); router.push("/"); }}>
                Sign out
              </button>
            </span>
          ) : (
            session === null && <Link className="font-medium text-emerald-800" href="/">Sign in</Link>
          )}
        </nav>
      </div>
    </header>
  );
}
