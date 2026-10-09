import type { Metric, OvertimeGrant, Snapshot } from "./types";

export const MIN_PER_DAY = 1440;

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

export const COST_LABEL: Record<string, string> = { overtime: "加班", outsourcing: "外包", setup: "換模" };

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
