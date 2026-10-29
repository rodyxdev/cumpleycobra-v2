"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useSession } from "@/components/Nav";
import { Button, Card, ErrorNote, Spinner } from "@/components/ui";
import { api, Session, signUp } from "@/lib/api";

const STEPS = [
  ["Describe", "The client writes the request in plain text. Muse Spark drafts verifiable acceptance criteria and flags vague ones."],
  ["Freeze & fund", "The contract is hashed (rules_hash) and the client deposits USDC into a Soroban escrow on Stellar testnet."],
  ["Deliver", "The freelancer accepts and delivers a repo + commit SHA (public or private via GitHub App) or a ZIP."],
  ["Verify", "Hidden tests run black-box in isolated containers; web deliveries are checked in a real browser with screenshots and video."],
  ["Get paid", "An approved verdict releases the escrow automatically, recording the workspace and verdict hashes on-chain."],
];

export default function Home() {
  const session = useSession();
  const router = useRouter();
  const [name, setName] = useState("");
  const [wallet, setWallet] = useState<Session["wallet"]>("client");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<{ escrow_contract_url?: string; escrow_contract_id?: string } | null>(null);

  useEffect(() => {
    api<{ escrow_contract_url?: string; escrow_contract_id?: string }>("/config", { token: null })
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);

  async function create(nameValue: string, walletValue: Session["wallet"]) {
    setBusy(true);
    setError(null);
    try {
      await signUp(nameValue, walletValue);
      router.push(walletValue === "client" ? "/tasks/new" : "/tasks");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[1.3fr_1fr]">
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Get paid when the work is verified.</h1>
          <p className="mt-3 text-lg text-slate-600">
            Cumple&amp;Cobra turns a client request into machine-checkable acceptance criteria, verifies the
            freelancer&apos;s delivery with executable evidence, and only then releases a USDC escrow on Stellar.
          </p>
        </div>
        <ol className="space-y-3">
          {STEPS.map(([title, text], i) => (
            <li key={title} className="flex gap-3">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-700 text-sm font-semibold text-white">{i + 1}</span>
              <div>
                <p className="font-medium text-slate-900">{title}</p>
                <p className="text-sm text-slate-600">{text}</p>
              </div>
            </li>
          ))}
        </ol>
        {config?.escrow_contract_id && (
          <p className="text-sm text-slate-500">
            Escrow contract on testnet:{" "}
            <a className="font-mono text-emerald-800 underline decoration-dotted" href={config.escrow_contract_url} target="_blank" rel="noreferrer">
              {config.escrow_contract_id.slice(0, 10)}…{config.escrow_contract_id.slice(-6)}
            </a>
          </p>
        )}
      </div>

      <Card title={session ? `Signed in as ${session.name}` : "Start the demo"}>
        <div className="space-y-4">
          <p className="text-sm text-slate-600">
            Demo accounts use pre-funded <strong>Stellar testnet</strong> wallets that the backend signs for. Create a
            client and a freelancer, then switch between them from the top bar.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <Button disabled={busy} onClick={() => create("Demo client", "client")}>Continue as demo client</Button>
            <Button disabled={busy} variant="secondary" onClick={() => create("Demo freelancer", "freelancer")}>
              Continue as demo freelancer
            </Button>
          </div>
          <form
            className="space-y-2 border-t border-slate-100 pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create(name.trim(), wallet);
            }}
          >
            <label className="block text-sm font-medium text-slate-700" htmlFor="name">Or pick a name</label>
            <div className="flex gap-2">
              <input id="name" className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                value={name} onChange={(e) => setName(e.target.value)} placeholder="Ana (client)" />
              <select aria-label="Wallet" className="rounded-lg border border-slate-300 px-2 text-sm" value={wallet ?? ""}
                onChange={(e) => setWallet((e.target.value || null) as Session["wallet"])}>
                <option value="client">client wallet</option>
                <option value="freelancer">freelancer wallet</option>
              </select>
              <Button type="submit" disabled={busy || !name.trim()}>Create</Button>
            </div>
          </form>
          {busy && <Spinner label="Creating account…" />}
          <ErrorNote error={error} />
        </div>
      </Card>
    </div>
  );
}
