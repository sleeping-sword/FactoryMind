import type { Elimination, Evaluation, Metric, OvertimeGrant, Snapshot, Stage, Status } from "./types";

export const MIN_PER_DAY = 1440;
/** 退回理由上限;與後端 governance.MAX_REASON_CHARS 相同。 */
export const MAX_REASON_CHARS = 200;

export const METRIC_LABEL: Record<Metric, string> = {
  strategic_tardiness_min: "策略客戶延誤",
  total_tardiness_min: "總延誤",
  changed_work_orders: "改動工單",
  cost_ntd: "成本",
};

export const METRIC_UNIT: Record<Metric, string> = {
  strategic_tardiness_min: "分",
  total_tardiness_min: "分",
  changed_work_orders: "張",
  cost_ntd: "元",
};

export const COST_LABEL: Record<string, string> = { overtime: "加班", outsourcing: "外包", setup: "換模", trial: "試模" };

const number = new Intl.NumberFormat("zh-TW");

export function num(value: number): string {
  return number.format(value);
}

export function ntd(value: number): string {
  return `NT$${number.format(Math.round(value))}`;
}

export function clock(minute: number): string {
  const day = Math.floor(minute / MIN_PER_DAY);
  const rest = minute - day * MIN_PER_DAY;
  return `D${day} ${String(Math.floor(rest / 60)).padStart(2, "0")}:${String(rest % 60).padStart(2, "0")}`;
}

export function isMetric(stage: string): stage is Metric {
  return stage in METRIC_LABEL;
}

/** 機台可工作的時間區間,與後端 replay.working_windows 相同的算法。 */
export function workingWindows(snapshot: Snapshot, machineId: string, overtime: OvertimeGrant[]): [number, number][] {
  const cal = snapshot.calendar;
  const windows: [number, number][] = [];
  for (let day = 0; day < cal.horizon_days; day++) {
    const extra = overtime
      .filter((g) => g.machine_id === machineId && g.day === day)
      .reduce((sum, g) => sum + g.minutes, 0);
    const start = day * MIN_PER_DAY + cal.day_start_min;
    const end = day * MIN_PER_DAY + cal.day_end_min + extra;
    const last = windows[windows.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else windows.push([start, end]);
  }
  return windows;
}

export const STAGES: { key: Stage; label: string }[] = [
  { key: "detect", label: "偵測" },
  { key: "assess", label: "評估" },
  { key: "propose", label: "提案" },
  { key: "simulate", label: "模擬評估" },
  { key: "decide", label: "決策" },
  { key: "report", label: "稽核回報" },
];

/** 每個狀態的說法、圖示,以及下一步輪到誰。狀態不只靠顏色區分。 */
export const STATUS: Record<Status, { label: string; icon: string; tone: "good" | "critical" | "wait" | ""; next: string }> = {
  processing: { label: "系統處理中", icon: "…", tone: "", next: "系統計算中,請稍候" },
  awaiting_approval: { label: "等你決定", icon: "!", tone: "wait", next: "生產主管:核准或退回建議" },
  no_compliant_plan: { label: "沒有合規方案", icon: "✕", tone: "critical", next: "生產主管:調整條件或補資料" },
  rejected: { label: "已退回建議", icon: "↩", tone: "wait", next: "生產主管:改選方案或重新評估" },
  stale: { label: "方案已過期", icon: "↻", tone: "wait", next: "生產主管:重新評估" },
  failed: { label: "處理失敗", icon: "✕", tone: "critical", next: "系統維護者:排除問題後重新評估" },
  applied_pending_tasks: { label: "生產已調整・設備維修中", icon: "◐", tone: "wait", next: "維修確認者:回報復機" },
  closed: { label: "已結案", icon: "✓", tone: "good", next: "無" },
};

/** 還要主管做決定的狀態,和後端 NEEDS_DECISION 一致;有這種事件時新事件先排隊。 */
export const NEEDS_DECISION = new Set<Status>(["processing", "awaiting_approval", "no_compliant_plan", "rejected", "stale", "failed"]);

/** 把分鐘換成主管習慣的說法。 */
export function duration(minutes: number): string {
  if (minutes === 0) return "0 分";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} 分`;
  return rest ? `${num(hours)} 小時 ${rest} 分` : `${num(hours)} 小時`;
}

export function timeOfDay(iso: string): string {
  return iso.slice(11, 19);
}

/** 用一句白話說明方案為什麼沒被選上。 */
export function explainElimination(out: Elimination): string {
  const over = (out.value ?? 0) - (out.best ?? 0);
  switch (out.stage) {
    case "strategic_tardiness_min":
      return `策略客戶的訂單會延誤 ${duration(out.value!)},有其他方案能做到 ${duration(out.best!)}。`;
    case "total_tardiness_min":
      return `全部訂單合計延誤 ${duration(out.value!)},比最好的方案多 ${duration(over)}(容許差距 ${duration(out.threshold!)})。`;
    case "changed_work_orders":
      return `要改動 ${out.value} 張既有工單,比最好的方案多 ${over} 張(容許差距 ${out.threshold} 張)。`;
    case "cost_ntd":
      return `成本 ${ntd(out.value!)},比最好的方案貴 ${ntd(over)}。`;
    case "硬規則":
      return `違反不可突破的規則:${out.reason}`;
    case "可行性":
      return `排不進去:${out.reason}`;
    default:
      return out.reason;
  }
}

/** 用幾句白話說明一個方案的後果。 */
export function describeOutcome(evaluation: Evaluation): string[] {
  const k = evaluation.kpis;
  if (!k) return [];
  const costs = Object.entries(k.cost_breakdown_ntd)
    .filter(([, value]) => value > 0)
    .map(([key, value]) => `${COST_LABEL[key] ?? key} ${ntd(value)}`);
  return [
    k.strategic_tardiness_min === 0 ? "策略客戶的訂單全部準時" : `策略客戶的訂單延誤 ${duration(k.strategic_tardiness_min)}`,
    k.late_work_orders.length === 0
      ? "所有訂單都能在承諾交期前完成"
      : `${k.late_work_orders.length} 張訂單逾期(${k.late_work_orders.join("、")}),合計延誤 ${duration(k.total_tardiness_min)}`,
    k.changed_work_orders == null ? "" : `需要改動 ${k.changed_work_orders} 張既有工單`,
    `額外成本 ${ntd(k.cost_ntd)}${costs.length ? `(${costs.join("、")})` : ""}`,
  ].filter(Boolean);
}
