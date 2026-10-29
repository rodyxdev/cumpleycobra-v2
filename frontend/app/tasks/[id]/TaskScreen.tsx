"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useSession } from "@/components/Nav";
import { Badge, Button, Card, contractUrl, ErrorNote, Hash, Spinner, TxList } from "@/components/ui";
import { api, blobUrl } from "@/lib/api";
import { Report, Task } from "@/lib/types";

export function TaskScreen({ id }: { id: string }) {
  const session = useSession();
  const router = useRouter();
  const [task, setTask] = useState<Task | null>(null);
  const [subs, setSubs] = useState<Report[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const userId = session?.userId;

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api<Task>(`/tasks/${id}`)
      .then(async (t) => {
        if (cancelled) return;
        setTask(t);
        if (t.role !== "visitor") {
          const list = await api<Report[]>(`/tasks/${id}/submissions`);
          if (!cancelled) setSubs(list);
        }
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [id, userId, version]);

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      setVersion((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (session === null) return <p>Sign in first.</p>;
  if (!task) return error ? <ErrorNote error={error} /> : <Spinner label="Loading task…" />;

  const c = task.contract;
  const escrow = task.escrow;
  const latest = subs[subs.length - 1];
  const releasedSub = escrow.released_submission_id;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm text-slate-500">Task {task.task_id.slice(0, 8)} · you are the <strong>{task.role}</strong></p>
          <h1 className="text-2xl font-bold">{c.title}</h1>
          <p className="mt-1 max-w-3xl text-slate-600">{c.description}</p>
        </div>
        <div className="flex gap-2">
          <Badge value={c.profile} />
          <Badge value={task.status} />
          <Badge value={escrow.status ?? "not funded"} />
        </div>
      </div>

      {busy && <Spinner label={busy} />}
      <ErrorNote error={error} />

      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <div className="space-y-6">
          <Card title="Acceptance criteria and test plan">
            <p className="mb-3 text-sm text-slate-500">
              The freelancer sees what will be tested, never the hidden test code.
            </p>
            <ul className="space-y-3">
              {c.criteria.map((cr) => (
                <li key={cr.id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <strong>{cr.id}</strong>
                    <Badge value={cr.type} />
                    {!cr.required && <span className="text-xs text-slate-500">optional</span>}
                  </div>
                  <p className="mt-1 text-sm text-slate-800">{cr.text}</p>
                  {task.test_plan[cr.id] && (
                    <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-600">
                      {task.test_plan[cr.id].map((line, i) => <li key={i}>{line}</li>)}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </Card>

          {task.role === "freelancer" && escrow.status === "accepted" && (
            <SubmitForm taskId={id} profile={c.profile} onBusy={setBusy} onError={setError}
              onDone={(sid) => router.push(`/tasks/${id}/submissions/${sid}`)} />
          )}

          {subs.length > 0 && (
            <Card title="Submissions">
              <ul className="divide-y divide-slate-100 text-sm">
                {subs.map((s) => (
                  <li key={s.submission_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <Link className="font-mono text-emerald-800 hover:underline" href={`/tasks/${id}/submissions/${s.submission_id}`}>
                      {s.submission_id.slice(0, 10)}
                    </Link>
                    <span className="text-slate-500">{s.received_at?.replace("T", " ").slice(0, 16)} UTC</span>
                    <Badge value={s.verdict.status} />
                    {releasedSub === s.submission_id && <Badge value="released" />}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          {task.role === "client" && (
            <ClientActions task={task} latest={latest} busy={!!busy} act={act} />
          )}
          {task.role === "visitor" && escrow.status === "funded" && (
            <Card title="Take this task">
              <p className="mb-3 text-sm text-slate-600">
                Accepting binds the escrow to your wallet on-chain. You will be paid automatically when a submission is approved.
              </p>
              <Button disabled={!!busy} onClick={() => act("Accepting on-chain…", () => api(`/tasks/${id}/accept`, { method: "POST" }))}>
                Accept for {escrow.amount} USDC
              </Button>
            </Card>
          )}

          <Card title="Payment (Soroban escrow, testnet)">
            {escrow.status ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-500">Status</span><Badge value={escrow.status} />
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-500">Amount</span><span className="font-mono">{escrow.amount} USDC</span>
                </div>
                {escrow.deadline && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-500">Deadline</span>
                    <span>{new Date(escrow.deadline * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC</span>
                  </div>
                )}
                <Hash label="Contract" value={escrow.contract_id} href={contractUrl(escrow.contract_id)} />
                <Hash label="rules_hash (on-chain)" value={task.rules_hash} />
                {escrow.workspace_hash && <Hash label="workspace_hash" value={escrow.workspace_hash} />}
                {escrow.verdict_hash && <Hash label="verdict_hash" value={escrow.verdict_hash} />}
                <div className="border-t border-slate-100 pt-2">
                  <TxList txs={escrow.txs} />
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Not funded yet.</p>
            )}
          </Card>

          <Card title="Integrity">
            <Hash label="rules_hash" value={task.rules_hash} />
            <Hash label="suite_hash" value={task.suite_hash} />
            {releasedSub && (task.role === "client" || task.role === "freelancer") && (
              <div className="pt-3">
                <Button variant="secondary" onClick={() => download(id, releasedSub, setError)}>Download verified code (ZIP)</Button>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

async function download(taskId: string, sid: string, setError: (e: string) => void) {
  try {
    const { url, headers } = await blobUrl(`/tasks/${taskId}/submissions/${sid}/source.zip`);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${taskId.slice(0, 8)}-${sid.slice(0, 8)}.zip`;
    a.click();
    console.info("workspace_hash", headers.get("X-Workspace-Hash"));
  } catch (e) {
    setError(e instanceof Error ? e.message : String(e));
  }
}

function ClientActions({ task, latest, busy, act }: {
  task: Task; latest?: Report; busy: boolean; act: (label: string, fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [amount, setAmount] = useState("25");
  const [days, setDays] = useState(7);
  const id = task.task_id;
  const escrow = task.escrow;
  return (
    <Card title="Client actions">
      <div className="space-y-4 text-sm">
        {task.status === "awaiting_plan_approval" && (
          <div className="space-y-2">
            <p className="text-slate-600">Check the test plan. Once approved, it is what the delivery will be verified against.</p>
            <Button disabled={busy} onClick={() => act("Approving plan…", () => api(`/tasks/${id}/plan/approve`, { method: "POST" }))}>
              Approve test plan
            </Button>
          </div>
        )}
        {task.status === "suite_rejected" && <p className="text-rose-700">The hidden test suite could not be validated. Create the task again.</p>}
        {task.status === "open" && !escrow.status && (
          <form className="space-y-2" onSubmit={(e) => {
            e.preventDefault();
            act("Depositing USDC on Stellar testnet…", () =>
              api(`/tasks/${id}/escrow/deposit`, { json: { amount, deadline_days: days } }));
          }}>
            <p className="text-slate-600">Deposit the payment into escrow. It is locked to this contract&apos;s rules_hash.</p>
            <div className="flex gap-2">
              <label className="flex-1">Amount (USDC)
                <input className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" value={amount}
                  onChange={(e) => setAmount(e.target.value)} inputMode="decimal" />
              </label>
              <label className="w-28">Deadline (days)
                <input className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" type="number" min={1} max={90}
                  value={days} onChange={(e) => setDays(Number(e.target.value))} />
              </label>
            </div>
            <Button type="submit" disabled={busy}>Deposit</Button>
          </form>
        )}
        {escrow.status === "funded" && <p className="text-slate-600">Waiting for a freelancer to accept the task.</p>}
        {escrow.status === "accepted" && (
          <div className="space-y-2">
            <p className="text-slate-600">
              Accepted by <strong>{task.freelancer_name}</strong>. Payment is released automatically when a submission is approved.
            </p>
            {latest?.verdict.status === "manual_review" && (
              <>
                <p className="text-amber-800">The latest submission needs manual review. Inspect the report, then you can release manually.</p>
                <Button variant="secondary" disabled={busy}
                  onClick={() => act("Releasing payment…", () => api(`/tasks/${id}/escrow/client-release`, { method: "POST" }))}>
                  Release payment manually
                </Button>
              </>
            )}
          </div>
        )}
        {escrow.status === "released" && (
          <p className="text-emerald-800">
            Paid ({escrow.released_by === "arbiter" ? "automatically, approved verdict" : "manually by the client"}). You can now download the verified code.
          </p>
        )}
      </div>
    </Card>
  );
}

function SubmitForm({ taskId, profile, onBusy, onError, onDone }: {
  taskId: string; profile: string; onBusy: (s: string | null) => void; onError: (e: string | null) => void;
  onDone: (sid: string) => void;
}) {
  const [mode, setMode] = useState<"github" | "zip">("zip");
  const [repo, setRepo] = useState("");
  const [sha, setSha] = useState("");
  const [installation, setInstallation] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);

  async function submit() {
    const form = new FormData();
    if (mode === "zip" && file) form.append("zip", file);
    if (mode === "github") {
      form.append("repo", repo.trim());
      form.append("sha", sha.trim());
      if (installation.trim()) form.append("installation_id", installation.trim());
    }
    setSending(true);
    onError(null);
    onBusy(profile === "web"
      ? "Building and checking your site in a real browser, then judging screenshots (about 30–90 s)…"
      : "Running the hidden tests in isolated containers and judging the result (about 30–60 s)…");
    try {
      const report = await api<Report>(`/tasks/${taskId}/submissions`, { form });
      onDone(report.submission_id);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
      onBusy(null);
    }
  }

  const ready = mode === "zip" ? !!file : repo.includes("/") && /^[0-9a-f]{40}$/i.test(sha.trim());
  return (
    <Card title="Deliver">
      <div className="space-y-3 text-sm">
        <div className="flex gap-2">
          <Button variant={mode === "zip" ? "primary" : "secondary"} onClick={() => setMode("zip")}>ZIP upload</Button>
          <Button variant={mode === "github" ? "primary" : "secondary"} onClick={() => setMode("github")}>GitHub repo + commit</Button>
        </div>
        {mode === "zip" ? (
          <input type="file" accept=".zip,application/zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            <input className="rounded-lg border border-slate-300 px-2 py-1.5" placeholder="owner/repo" value={repo} onChange={(e) => setRepo(e.target.value)} />
            <input className="rounded-lg border border-slate-300 px-2 py-1.5 font-mono" placeholder="full 40-char commit SHA" value={sha} onChange={(e) => setSha(e.target.value)} />
            <input className="rounded-lg border border-slate-300 px-2 py-1.5 sm:col-span-2" placeholder="GitHub App installation id (only for private repos)"
              value={installation} onChange={(e) => setInstallation(e.target.value)} />
          </div>
        )}
        <Button disabled={!ready || sending} onClick={submit}>Submit for verification</Button>
      </div>
    </Card>
  );
}
