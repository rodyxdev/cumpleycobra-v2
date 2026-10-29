export type WebCheck = { kind: string; page: string; args: string[] };
export type StaticCheck = { kind: string; args: string[] };

export type Criterion = {
  id: string;
  text: string;
  type: "test" | "static" | "visual";
  required: boolean;
  static_check: StaticCheck | null;
  web_check?: WebCheck | null;
};

export type Mockup = { id: string; sha256: string; page: string; viewport: "desktop" | "mobile"; description: string };

export type Draft = {
  title: string;
  profile: "script" | "api" | "web";
  description: string;
  interface: { language: string; symbols: { module: string; name: string; kind: string; signature: string; description: string }[] };
  criteria: Criterion[];
  allowed_dependencies: string[];
  examples: { description: string; input: string; expected_output: string }[];
  web?: { pages: string[]; mockups: Mockup[] } | null;
};

export type ReviewIssue = {
  criterion_id: string;
  kind: string;
  severity: "blocker" | "warning";
  explanation: string;
  suggestion: string;
};

export type Review = { ok: boolean; issues: ReviewIssue[]; summary: string };

export type Tx = { action: string; hash: string; ledger: number | null; url: string; at: string };

export type Escrow = {
  status?: "funded" | "accepted" | "released" | "refunded";
  contract_id?: string;
  amount?: string;
  deadline?: number;
  client_address?: string;
  freelancer_address?: string;
  released_by?: string;
  released_submission_id?: string;
  workspace_hash?: string;
  verdict_hash?: string;
  txs?: Tx[];
};

export type Task = {
  task_id: string;
  status: "awaiting_plan_approval" | "open" | "suite_rejected";
  created_at: string;
  rules_hash: string;
  suite_hash: string;
  contract: Draft;
  test_plan: Record<string, string[]>;
  escrow: Escrow;
  role: "client" | "freelancer" | "arbiter" | "visitor";
  client_name: string | null;
  freelancer_name: string | null;
};

export type TaskSummary = {
  task_id: string;
  title: string;
  profile: string;
  status: string;
  role: string;
  escrow_status: string | null;
  amount: string | null;
  created_at: string;
};

export type ReportCriterion = {
  id: string;
  type: string;
  required: boolean;
  status: "pass" | "fail" | "manual_review" | "not_evaluated";
  stage: string;
  summary?: string;
  detail?: string;
  visual_reasoning?: string;
  judge_reasoning?: string;
  judge_verdict?: string;
  tests: { name?: string; nodeid?: string; outcome: string; summary?: string }[];
};

export type Report = {
  submission_id: string;
  profile: string;
  verdict_hash: string;
  verdict: {
    status: "approved" | "rejected" | "manual_review";
    approved: boolean;
    stage: string;
    workspace_hash: string;
    rules_hash: string;
    suite_hash: string;
    flags: Record<string, boolean>;
  };
  reasons: string[];
  criteria: ReportCriterion[];
  signals: { kind: string; detail: string; ref?: string; snippet?: string }[];
  screenshots: { id: string; page: string; viewport: string; label: string; file: string }[];
  videos: string[];
  received_at?: string;
  payment?: Escrow | { error: string } | null;
};
