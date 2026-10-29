"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Button, Card, ErrorNote, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { Criterion, Draft, Review } from "@/lib/types";

const SAMPLES: Record<string, string> = {
  "Python function": `I need a Python module sales.py with a function summarize_sales(text: str) -> dict[str, int].
Each line has the format product,quantity,unit_price_cents. Return the total sold per product in cents
(quantity x price, adding up every line of the same product).
Rules:
- Split the text with splitlines(). Skip a line if line.strip() is empty or starts with "#" (comments).
- Strip spaces around each of the 3 fields; the product name is lowercased and used as the key.
- If a non-skipped line does not have exactly 3 comma-separated fields, or the product is empty after
  stripping, raise ValueError whose message contains the line number (1-based, counting every line from
  splitlines(), including blank lines and comments).
- Quantity and price (after stripping) must be ASCII digits only (regex ^[0-9]+$): "two", "-1", "+5", "2.0"
  or "" raise ValueError with the line number.
- Empty input (or only skipped lines) returns {}. Standard library only, no eval/exec, and the public
  function has type hints.
Examples:
- summarize_sales("apple,2,150\\npear,1,300") returns {"apple": 300, "pear": 300}
- summarize_sales("Apple, 1, 100\\napple,3,100") returns {"apple": 400}
- summarize_sales("# header\\n\\ngrape,3,50") returns {"grape": 150}
- summarize_sales("pear,two,300") raises ValueError`,
  "Landing page": `A one-page landing for "Huerta Viva", an organic vegetable box service.
Header with the brand on the left and navigation (Servicios, Contacto) on the right. A hero with the title
"Verduras orgánicas a tu puerta", a short sentence and a "Pedir ahora" button. Three service cards in a row
(stacked on mobile). A contact form with Nombre, Email and Mensaje and an "Enviar" button that POSTs the data
to /api/contacto and shows "¡Gracias! Te responderemos pronto.". It must work on a 390 px phone with no
horizontal scroll, and the browser tab title must say "Huerta Viva". Mockups attached.`,
};

type Uploaded = { id: string; sha256: string; description: string; preview: string };

export default function NewTaskPage() {
  const router = useRouter();
  const [request, setRequest] = useState("");
  const [mockups, setMockups] = useState<Uploaded[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(label);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function upload(files: FileList | null) {
    if (!files) return;
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("file", file);
      const res = await run("Uploading mockup…", () => api<{ sha256: string }>("/mockups", { form }));
      if (res) {
        setMockups((m) => [...m, { id: `M${m.length + 1}`, sha256: res.sha256, description: file.name, preview: URL.createObjectURL(file) }]);
      }
    }
  }

  const generate = () =>
    run("Muse Spark is drafting the contract…", async () => {
      const d = await api<Draft>("/tasks/draft", {
        json: { request, mockups: mockups.map(({ id, sha256, description }) => ({ id, sha256, description })) },
      });
      setDraft(d);
      setReview(null);
    });

  const runReview = () =>
    run("Reviewing criteria for vague, unverifiable or implementation-imposing wording…", async () => {
      setReview(await api<Review>("/tasks/draft/review", { json: { draft } }));
    });

  const confirm = () =>
    run(draft?.profile === "web" ? "Freezing the contract…" : "Freezing the contract and generating the hidden test suite (about a minute)…",
      async () => {
        const task = await api<{ task_id: string }>("/tasks", { json: { draft } });
        router.push(`/tasks/${task.task_id}`);
      });

  function updateCriterion(index: number, patch: Partial<Criterion>) {
    if (!draft) return;
    const criteria = draft.criteria.map((c, i) => (i === index ? { ...c, ...patch } : c));
    setDraft({ ...draft, criteria });
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">New task</h1>

      <Card title="1 · Describe what you need">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2 text-sm">
            <span className="text-slate-500">Samples:</span>
            {Object.keys(SAMPLES).map((k) => (
              <button key={k} className="text-emerald-800 underline decoration-dotted" onClick={() => setRequest(SAMPLES[k])}>{k}</button>
            ))}
          </div>
          <textarea className="h-44 w-full rounded-lg border border-slate-300 p-3 font-mono text-sm" value={request}
            onChange={(e) => setRequest(e.target.value)} placeholder="Describe the deliverable, rules and examples…" />
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm text-slate-700">
              Mockups (web, optional):{" "}
              <input type="file" accept="image/png,image/jpeg" multiple onChange={(e) => upload(e.target.files)} className="text-sm" />
            </label>
            {mockups.map((m) => (
              <figure key={m.id} className="flex items-center gap-2 rounded-lg border border-slate-200 p-1 pr-2 text-xs">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={m.preview} alt={m.description} className="h-10 w-10 rounded object-cover" />
                <span><strong>{m.id}</strong> {m.description}</span>
              </figure>
            ))}
          </div>
          <Button disabled={!!busy || request.trim().length < 10} onClick={generate}>Generate draft</Button>
        </div>
      </Card>

      {draft && (
        <Card title={`2 · Review the contract — ${draft.title}`} actions={<Badge value={draft.profile} />}>
          <div className="space-y-4">
            <textarea className="w-full rounded-lg border border-slate-300 p-2 text-sm" rows={3} value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            {draft.interface.symbols.length > 0 && (
              <p className="text-sm text-slate-600">
                Interface:{" "}
                {draft.interface.symbols.map((s) => <code key={s.name} className="rounded bg-slate-100 px-1">{s.module}.{s.signature}</code>)}
              </p>
            )}
            <ul className="space-y-3">
              {draft.criteria.map((c, i) => {
                const issues = review?.issues.filter((x) => x.criterion_id === c.id) ?? [];
                const check = c.web_check ?? c.static_check;
                return (
                  <li key={c.id} className="rounded-lg border border-slate-200 p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                      <strong>{c.id}</strong>
                      <Badge value={c.type} />
                      {check && <code className="rounded bg-slate-100 px-1 text-xs">{check.kind}({check.args.join(", ")})</code>}
                      <label className="ml-auto flex items-center gap-1 text-slate-600">
                        <input type="checkbox" checked={c.required} onChange={(e) => updateCriterion(i, { required: e.target.checked })} />
                        required
                      </label>
                      <button className="text-rose-700 hover:underline"
                        onClick={() => setDraft({ ...draft, criteria: draft.criteria.filter((_, j) => j !== i) })}>
                        remove
                      </button>
                    </div>
                    <textarea className="w-full rounded border border-slate-200 p-2 text-sm" rows={2} value={c.text}
                      onChange={(e) => updateCriterion(i, { text: e.target.value })} />
                    {issues.map((issue, k) => (
                      <div key={k} className={`mt-2 rounded-md p-2 text-sm ${issue.severity === "blocker" ? "bg-rose-50" : "bg-amber-50"}`}>
                        <p><Badge value={issue.severity === "blocker" ? "fail" : "manual_review"} /> <strong>{issue.kind.replaceAll("_", " ")}</strong>: {issue.explanation}</p>
                        {issue.suggestion && (
                          <p className="mt-1 text-slate-700">
                            Suggestion: {issue.suggestion}{" "}
                            <button className="text-emerald-800 underline decoration-dotted" onClick={() => updateCriterion(i, { text: issue.suggestion })}>
                              apply
                            </button>
                            {check && (
                              <span className="mt-1 block text-xs text-slate-500">
                                “apply” only rewrites the text. The automated check stays{" "}
                                <code>{check.kind}({check.args.join(", ")})</code>; if the suggestion needs a different
                                check, remove this criterion or regenerate the draft with a more precise request.
                              </span>
                            )}
                          </p>
                        )}
                      </div>
                    ))}
                  </li>
                );
              })}
            </ul>
            {review && review.issues.filter((x) => x.criterion_id === "*").map((issue, k) => (
              <p key={k} className="rounded-md bg-amber-50 p-2 text-sm"><strong>{issue.kind}</strong>: {issue.explanation}</p>
            ))}
            {draft.examples.length > 0 && (
              <details className="text-sm">
                <summary className="cursor-pointer text-slate-600">Public examples ({draft.examples.length})</summary>
                <ul className="mt-2 space-y-1 font-mono text-xs">
                  {draft.examples.map((e, i) => <li key={i}>{e.input} → {e.expected_output}</li>)}
                </ul>
              </details>
            )}
            {review && <p className="text-sm text-slate-600">{review.summary}</p>}
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" disabled={!!busy} onClick={runReview}>{review ? "Review again" : "Review criteria"}</Button>
              <Button disabled={!!busy} onClick={confirm}>Confirm contract</Button>
            </div>
            <p className="text-xs text-slate-500">
              Confirming freezes the contract: its canonical JSON is hashed into <code>rules_hash</code>, which is stored
              on-chain with your deposit. Edit or apply suggestions before confirming.
            </p>
          </div>
        </Card>
      )}

      {busy && <Spinner label={busy} />}
      <ErrorNote error={error} />
    </div>
  );
}
