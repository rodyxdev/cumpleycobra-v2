"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useSession } from "@/components/Nav";
import { Badge, Card, contractUrl, ErrorNote, Hash, Spinner, TxList } from "@/components/ui";
import { api, blobUrl } from "@/lib/api";
import { Report, Task } from "@/lib/types";

const STAGES: Record<string, string> = {
  hygiene: "hygiene checks",
  build: "dependency install / build",
  static: "static analysis",
  tests: "hidden tests",
  judge: "Muse Spark judge",
  final: "final rule",
};

export function ReportScreen({ taskId, submissionId }: { taskId: string; submissionId: string }) {
  const session = useSession();
  const [report, setReport] = useState<Report | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session) return;
    Promise.all([api<Report>(`/tasks/${taskId}/submissions/${submissionId}`), api<Task>(`/tasks/${taskId}`)])
      .then(([r, t]) => {
        setReport(r);
        setTask(t);
      })
      .catch((e) => setError(e.message));
  }, [session, taskId, submissionId]);

  if (session === null) return <p>Sign in first.</p>;
  if (!report || !task) return error ? <ErrorNote error={error} /> : <Spinner label="Loading report…" />;

  const v = report.verdict;
  const escrow = task.escrow;
  const paidThis = escrow.released_submission_id === report.submission_id;
  const flags = Object.entries(v.flags ?? {}).filter(([, on]) => on).map(([k]) => k);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href={`/tasks/${taskId}`} className="text-sm text-emerald-800 hover:underline">← {task.contract.title}</Link>
          <h1 className="mt-1 text-2xl font-bold">Verification report</h1>
          <p className="text-sm text-slate-500">
            Submission {report.submission_id.slice(0, 10)} · {report.received_at?.replace("T", " ").slice(0, 16)} UTC · viewing as{" "}
            <strong>{task.role}</strong>
          </p>
        </div>
        <div className="text-right">
          <div className="text-3xl"><Badge value={v.status} /></div>
          <p className="mt-1 text-sm text-slate-500">decided at: {STAGES[v.stage] ?? v.stage}</p>
        </div>
      </div>

      {error && <ErrorNote error={error} />}

      {(report.reasons?.length > 0 || flags.length > 0) && (
        <Card title="Why">
          <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
            {report.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
          {report.signals?.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {report.signals.map((s, i) => (
                <li key={i} className="flex gap-2"><Badge value="manual_review" /> <span><strong>{s.kind}</strong>: {s.detail}{s.ref ? ` (${s.ref})` : ""}</span></li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <Card title="Criteria">
          <ul className="space-y-3">
            {report.criteria.map((c) => {
              const criterion = task.contract.criteria.find((x) => x.id === c.id);
              return (
                <li key={c.id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <strong>{c.id}</strong>
                    <Badge value={c.status} />
                    <span className="text-slate-500">{c.type}{c.required ? "" : " · optional"}</span>
                  </div>
                  {criterion && <p className="mt-1 text-sm text-slate-800">{criterion.text}</p>}
                  {(c.summary || c.detail) && <p className="mt-1 text-xs text-slate-600">{c.summary} {c.detail}</p>}
                  {(c.visual_reasoning || c.judge_reasoning) && (
                    <p className="mt-1 text-xs italic text-slate-600">{c.visual_reasoning ?? c.judge_reasoning}</p>
                  )}
                  {c.tests?.length > 0 && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer text-slate-600">
                        {c.tests.filter((t) => t.outcome === "passed").length}/{c.tests.length} hidden tests passed
                      </summary>
                      <ul className="mt-1 space-y-0.5">
                        {c.tests.map((t, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <Badge value={t.outcome} />
                            <span className="font-mono">{t.name ?? t.nodeid}</span>
                            {t.summary && t.outcome !== "passed" && <span className="text-slate-500">— {t.summary}</span>}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>

        <div className="space-y-6">
          <Card title="Payment">
            <div className="flex items-center justify-between text-sm">
              <span className="text-slate-500">Escrow</span><Badge value={escrow.status ?? "not funded"} />
            </div>
            {paidThis && <p className="mt-2 text-sm text-emerald-800">This submission was paid ({escrow.released_by === "arbiter" ? "approved verdict" : "released by the client"}).</p>}
            <div className="mt-2 border-t border-slate-100 pt-2"><TxList txs={escrow.txs} /></div>
          </Card>
          <Card title="Hashes">
            <Hash label="rules_hash" value={v.rules_hash} />
            <Hash label="suite_hash" value={v.suite_hash} />
            <Hash label="workspace_hash" value={v.workspace_hash} />
            <Hash label="verdict_hash" value={report.verdict_hash} />
            <Hash label="escrow contract" value={escrow.contract_id} href={contractUrl(escrow.contract_id)} />
            {paidThis && escrow.verdict_hash && (
              <p className="mt-2 text-xs text-slate-500">
                The release transaction recorded this workspace_hash and verdict_hash on-chain.
              </p>
            )}
          </Card>
        </div>
      </div>

      {report.screenshots?.length > 0 && (
        <Card title="Evidence: screenshots">
          <div className="grid gap-4 md:grid-cols-[3fr_1fr]">
            {report.screenshots.filter((s) => s.label === "página completa").map((s) => (
              <Evidence key={s.id} path={`/tasks/${taskId}/submissions/${report.submission_id}/evidence/${s.file}`}
                caption={`${s.id} · ${s.viewport} (${s.viewport === "desktop" ? 1440 : 390} px) · ${s.page}`} />
            ))}
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 md:grid-cols-4">
            {report.screenshots.filter((s) => s.label !== "página completa").map((s) => (
              <Evidence key={s.id} path={`/tasks/${taskId}/submissions/${report.submission_id}/evidence/${s.file}`}
                caption={`${s.id} · after ${s.label.replace("tras ", "")}`} />
            ))}
          </div>
        </Card>
      )}

      {report.videos?.length > 0 && (
        <Card title="Evidence: browser session video">
          <div className="grid gap-4 md:grid-cols-[3fr_1fr]">
            {report.videos.map((name) => (
              <Evidence key={name} video path={`/tasks/${taskId}/submissions/${report.submission_id}/evidence/${name}`} caption={name} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function Evidence({ path, caption, video }: { path: string; caption: string; video?: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let revoke: string | null = null;
    blobUrl(path).then(({ url }) => {
      revoke = url;
      setUrl(url);
    }).catch(() => setFailed(true));
    return () => {
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [path]);
  return (
    <figure className="space-y-1">
      {failed ? <p className="text-sm text-slate-500">Not available.</p> : !url ? <Spinner /> : video ? (
        <video src={url} controls className="w-full rounded-lg border border-slate-200" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt={caption} className="max-h-[720px] w-full rounded-lg border border-slate-200 object-contain object-top" />
      )}
      <figcaption className="text-xs text-slate-500">{caption}</figcaption>
    </figure>
  );
}
