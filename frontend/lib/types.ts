// 與後端 factorymind 的資料契約對應(models.py、plan.py、policy.py、ranking.py、governance.py)。

export type Metric = "strategic_tardiness_min" | "total_tardiness_min" | "changed_work_orders" | "cost_ntd";

export interface Machine {
  id: string;
  role: string;
  tonnage: number;
  status: "up" | "down";
  current_mold_id: string | null;
  current_material_id: string | null;
  current_color_shade: number | null;
}

export interface Mold {
  id: string;
  min_tonnage: number;
  max_tonnage: number;
}

export interface Product {
  id: string;
  name: string;
  mold_id: string;
  material_id: string;
}

export interface Customer {
  id: string;
  name: string;
  tier: "strategic" | "normal";
}

export interface WorkOrder {
  id: string;
  customer_id: string;
  product_id: string;
  quantity: number;
  quantity_done: number;
  due_min: number;
  status: "not_started" | "in_progress" | "done";
  is_rush: boolean;
  max_delay_min: number | null;
}

export interface Snapshot {
  factory_id: string;
  version: number;
  source: string;
  machines: Machine[];
  molds: Mold[];
  products: Product[];
  customers: Customer[];
  work_orders: WorkOrder[];
  calendar: { day_start_min: number; day_end_min: number; horizon_days: number };
  outsource_lead_time_min: number;
}

export interface OvertimeGrant {
  machine_id: string;
  day: number;
  minutes: number;
}

export interface Plan {
  id: string;
  label: string;
  overtime: OvertimeGrant[];
  outsourced: string[];
}

export interface Operation {
  work_order_id: string;
  machine_id: string;
  start_min: number;
  setup_min: number;
  end_min: number;
  mold_changed: boolean;
}

export interface Kpis {
  strategic_tardiness_min: number;
  total_tardiness_min: number;
  late_work_orders: string[];
  changed_work_orders: number | null;
  cost_ntd: number;
  cost_breakdown_ntd: Record<string, number>;
  mold_changes: number;
  setup_min: number;
  machine_load: Record<string, number>;
}

export interface Evaluation {
  plan_id: string;
  snapshot_version: number;
  policy_version: string;
  engine_version: string;
  status: "ok" | "invalid_plan";
  errors: string[];
  feasible: boolean | null;
  infeasible_reasons: string[];
  violations: { code: string; message: string; subjects: string[] }[] | null;
  kpis: Kpis | null;
  operations: Operation[];
  outsourced: string[];
}

export interface Elimination {
  plan_id: string;
  stage: string;
  reason: string;
  value: number | null;
  best: number | null;
  threshold: number | null;
}

export interface Policy {
  version: string;
  hard_limits: { max_overtime_min_per_machine_day: number; overtime_budget_ntd: number };
  ranking: { metric: Metric; threshold: number }[];
}

export interface Ranking {
  winner: string | null;
  eliminated: Elimination[];
  undecided_reason: string | null;
  tie_break: string | null;
}

export type Stage = "detect" | "assess" | "propose" | "simulate" | "decide" | "report";

export type Status =
  | "processing"
  | "awaiting_approval"
  | "no_compliant_plan"
  | "rejected"
  | "stale"
  | "failed"
  | "applied_pending_tasks"
  | "closed";

export interface FactoryEvent {
  type: "machine_down" | "rush_order";
  subject: string;
  note: string;
  work_order: WorkOrder | null;
}

export interface Task {
  id: string;
  subject: string;
  role: string;
  description: string;
  done: boolean;
  done_by: string | null;
  done_at: string | null;
}

export interface Decision {
  kind: "approved" | "rejected";
  plan_id: string | null;
  actor: string;
  at: string;
  reason: string;
  followed_recommendation: boolean | null;
}

export interface TraceEntry {
  seq: number;
  at: string;
  stage: Stage;
  actor: string;
  message: string;
}

export interface AiBrief {
  headline: string;
  reasons: string[];
  watch: string[];
  source: "ai" | "rules";
  model: string | null;
  note: string | null;
  at: string;
}

export interface ChatTurn {
  question: string;
  answer: string;
  source: "ai" | "rules";
  model: string | null;
  actor: string;
  at: string;
}

export interface Incident {
  id: string;
  title: string;
  opened_at: string;
  events: FactoryEvent[];
  status: Status;
  stage: Stage;
  snapshot: Snapshot;
  policy_version: string;
  reference: Evaluation;
  assessment: { affected_work_orders: string[]; if_unhandled: string[] } | null;
  plans: Plan[];
  evaluations: Evaluation[];
  search: Record<string, { start_rule: string; moves: number; evaluations: number; local_optimum: boolean }>;
  ranking: Ranking | null;
  decisions: Decision[];
  applied_plan_id: string | null;
  tasks: Task[];
  trace: TraceEntry[];
  error: string | null;
  ai_brief: AiBrief | null;
  chat: ChatTurn[];
}

export interface IncidentSummary {
  id: string;
  title: string;
  opened_at: string;
  status: Status;
  stage: Stage;
  trace_length: number;
}

export interface FactoryState {
  revision: number;
  running: boolean;
  ai: { enabled: boolean; provider: string; model: string };
  snapshot: Snapshot;
  policy: Policy;
  schedule: { version: number; plan: Plan; evaluation: Evaluation; still_valid: boolean };
  incidents: IncidentSummary[];
}
