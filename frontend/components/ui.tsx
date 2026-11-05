"use client";

import { EXPLORER, short } from "@/lib/api";

const TONES: Record<string, string> = {
  approved: "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
  pass: "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
  released: "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
  rejected: "bg-rose-100 text-rose-800 ring-rose-600/20",
  fail: "bg-rose-100 text-rose-800 ring-rose-600/20",
  failed: "bg-rose-100 text-rose-800 ring-rose-600/20",
  error: "bg-rose-100 text-rose-800 ring-rose-600/20",
  refunded: "bg-slate-100 text-slate-700 ring-slate-500/20",
  manual_review: "bg-amber-100 text-amber-800 ring-amber-600/20",
  funded: "bg-sky-100 text-sky-800 ring-sky-600/20",
  accepted: "bg-indigo-100 text-indigo-800 ring-indigo-600/20",
  passed: "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
};

const LABELS: Record<string, string> = {
  manual_review: "manual review",
  not_evaluated: "not evaluated",
  awaiting_plan_approval: "awaiting plan approval",
  suite_rejected: "test suite rejected",
};

export function Badge({ value }: { value?: string | null }) {
  if (!value) return <span className="text-slate-400">—</span>;
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[value] ?? "bg-slate-100 text-slate-700 ring-slate-500/20"}`}>
      {LABELS[value] ?? value.replaceAll("_", " ")}
    </span>
  );
}

export function Card({ title, children, actions }: { title?: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="text-base font-semibold text-slate-900">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = "primary",
  type = "button",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger";
  type?: "button" | "submit";
}) {
  const styles = {
    primary: "bg-emerald-700 text-white hover:bg-emerald-800 disabled:bg-emerald-300",
    secondary: "bg-white text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50 disabled:text-slate-400",
    danger: "bg-rose-600 text-white hover:bg-rose-700 disabled:bg-rose-300",
  }[variant];
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition ${styles} disabled:cursor-not-allowed`}>
      {children}
    </button>
  );
}

/** Selector de archivos con un botón visible: el control nativo ("Seleccionar archivo") pasaba
 *  desapercibido. El <input> sigue en el DOM (oculto) para que funcione con teclado y lectores. */
export function FilePicker({
  label,
  accept,
  multiple,
  onFiles,
  selected,
}: {
  label: string;
  accept: string;
  multiple?: boolean;
  onFiles: (files: FileList | null) => void;
  selected?: string | null;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-3">
      <input type="file" accept={accept} multiple={multiple} className="sr-only"
        onChange={(e) => onFiles(e.target.files)} />
      <span className="inline-flex items-center gap-2 rounded-lg bg-white px-3.5 py-2 text-sm font-medium text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50">
        📎 {label}
      </span>
      <span className="text-sm text-slate-600">{selected ?? "No file selected"}</span>
    </label>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-600">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-emerald-700" />
      {label}
    </span>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800 ring-1 ring-rose-200">{error}</p>;
}

export function Hash({ label, value, href }: { label: string; value?: string | null; href?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
      <span className="text-slate-500">{label}</span>
      {value ? (
        href ? (
          <a className="font-mono text-emerald-800 underline decoration-dotted" href={href} target="_blank" rel="noreferrer" title={value}>
            {short(value)}
          </a>
        ) : (
          <span className="font-mono text-slate-800" title={value}>{short(value)}</span>
        )
      ) : (
        <span className="text-slate-400">—</span>
      )}
    </div>
  );
}

export function TxList({ txs }: { txs?: { action: string; hash: string; url: string; at: string }[] }) {
  if (!txs?.length) return <p className="text-sm text-slate-500">No on-chain transactions yet.</p>;
  return (
    <ul className="divide-y divide-slate-100 text-sm">
      {txs.map((tx) => (
        <li key={tx.hash} className="flex items-center justify-between gap-3 py-1.5">
          <span className="font-medium capitalize text-slate-800">{tx.action.replace("_", " ")}</span>
          <a className="font-mono text-emerald-800 underline decoration-dotted" href={tx.url} target="_blank" rel="noreferrer">
            {short(tx.hash, 8)} ↗
          </a>
        </li>
      ))}
    </ul>
  );
}

export function contractUrl(id?: string | null) {
  return id ? `${EXPLORER}/contract/${id}` : undefined;
}
