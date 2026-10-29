"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useSession } from "@/components/Nav";
import { Badge, Card, ErrorNote, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { TaskSummary } from "@/lib/types";

export default function TasksPage() {
  const session = useSession();
  const [tasks, setTasks] = useState<TaskSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session) return;
    api<TaskSummary[]>("/tasks").then(setTasks).catch((e) => setError(e.message));
  }, [session]);

  if (session === null) return <p className="text-slate-600">Sign in from the <Link className="underline" href="/">home page</Link>.</p>;

  const mine = tasks?.filter((t) => t.role !== "visitor") ?? [];
  const available = tasks?.filter((t) => t.role === "visitor") ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Tasks</h1>
        <Link href="/tasks/new" className="rounded-lg bg-emerald-700 px-3.5 py-2 text-sm font-medium text-white hover:bg-emerald-800">
          New task
        </Link>
      </div>
      <ErrorNote error={error} />
      {!tasks && !error && <Spinner label="Loading…" />}
      {tasks && (
        <>
          <Card title="My tasks">
            <TaskTable tasks={mine} empty="You are not part of any task yet." />
          </Card>
          <Card title="Available to accept (funded, no freelancer yet)">
            <TaskTable tasks={available} empty="No funded tasks waiting for a freelancer." />
          </Card>
        </>
      )}
    </div>
  );
}

function TaskTable({ tasks, empty }: { tasks: TaskSummary[]; empty: string }) {
  if (!tasks.length) return <p className="text-sm text-slate-500">{empty}</p>;
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-slate-500">
        <tr>
          <th className="py-1 font-medium">Task</th>
          <th className="py-1 font-medium">Profile</th>
          <th className="py-1 font-medium">My role</th>
          <th className="py-1 font-medium">Escrow</th>
          <th className="py-1 text-right font-medium">Amount</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {tasks.map((t) => (
          <tr key={t.task_id}>
            <td className="py-2">
              <Link className="font-medium text-emerald-800 hover:underline" href={`/tasks/${t.task_id}`}>{t.title}</Link>
            </td>
            <td className="py-2 capitalize text-slate-600">{t.profile}</td>
            <td className="py-2 capitalize text-slate-600">{t.role}</td>
            <td className="py-2"><Badge value={t.escrow_status ?? (t.status === "open" ? "not funded" : t.status)} /></td>
            <td className="py-2 text-right font-mono">{t.amount ? `${t.amount} USDC` : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
