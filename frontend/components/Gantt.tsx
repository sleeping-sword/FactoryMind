"use client";

import { useState } from "react";
import { MIN_PER_DAY, clock, num, workingWindows } from "@/lib/format";
import type { Customer, Evaluation, Operation, OvertimeGrant, Snapshot, WorkOrder } from "@/lib/types";

interface Segment {
  from: number;
  to: number;
  setup: boolean;
}

interface Bar {
  op: Operation;
  workOrder: WorkOrder;
  customer: Customer;
  segments: Segment[];
  late: number;
}

/** 把一張工單切成實際工作的片段:跨夜會停工,前 setup_min 分鐘是換模。 */
function segmentsOf(op: Operation, windows: [number, number][]): Segment[] {
  const segments: Segment[] = [];
  let setupLeft = op.setup_min;
  for (const [winStart, winEnd] of windows) {
    let from = Math.max(winStart, op.start_min);
    const to = Math.min(winEnd, op.end_min);
    if (to <= from) continue;
    if (setupLeft > 0) {
      const cut = Math.min(to, from + setupLeft);
      segments.push({ from, to: cut, setup: true });
      setupLeft -= cut - from;
      from = cut;
    }
    if (to > from) segments.push({ from, to, setup: false });
  }
  return segments;
}

export default function Gantt({
  snapshot,
  evaluation,
  overtime,
  axisEnd,
  flagged,
}: {
  snapshot: Snapshot;
  evaluation: Evaluation;
  overtime: OvertimeGrant[];
  axisEnd: number;
  /** 需要特別標出的工單,例如排在停機機台上、必須搬走的工單。 */
  flagged?: Set<string>;
}) {
  const [tip, setTip] = useState<{ bar: Bar; x: number; y: number } | null>(null);
  const cal = snapshot.calendar;
  const axisStart = cal.day_start_min;
  const span = axisEnd - axisStart;
  const pct = (minute: number) => `${((minute - axisStart) / span) * 100}%`;
  const width = (from: number, to: number) => `${((to - from) / span) * 100}%`;

  const workOrders = new Map(snapshot.work_orders.map((w) => [w.id, w]));
  const customers = new Map(snapshot.customers.map((c) => [c.id, c]));
  const productNames = new Map(snapshot.products.map((p) => [p.id, p.name]));
  const days = Array.from({ length: Math.ceil(axisEnd / MIN_PER_DAY) }, (_, day) => day);

  const rows = snapshot.machines.map((machine) => {
    const windows = workingWindows(snapshot, machine.id, overtime);
    const bars: Bar[] = evaluation.operations
      .filter((op) => op.machine_id === machine.id)
      .map((op) => {
        const workOrder = workOrders.get(op.work_order_id)!;
        return {
          op,
          workOrder,
          customer: customers.get(workOrder.customer_id)!,
          segments: segmentsOf(op, windows),
          late: Math.max(0, op.end_min - workOrder.due_min),
        };
      });
    const overtimeBands = overtime
      .filter((g) => g.machine_id === machine.id)
      .map((g) => {
        const from = g.day * MIN_PER_DAY + cal.day_end_min;
        return { from, to: from + g.minutes };
      });
    return { machine, bars, overtimeBands };
  });

  const show = (bar: Bar, target: Element) => {
    const box = target.getBoundingClientRect();
    setTip({ bar, x: box.left + box.width / 2, y: box.top });
  };

  return (
    <div className="gantt-scroll">
      <div className="gantt" role="img" aria-label={`${evaluation.plan_id} 各機台排程時間軸,詳細資料見下方表格`}>
        <div className="gantt-row gantt-axis">
          <div className="gantt-name" />
          <div className="gantt-track">
            {days.map((day) => (
              <span
                key={day}
                className="gantt-day"
                style={{
                  left: pct(day * MIN_PER_DAY + cal.day_start_min),
                  width: width(cal.day_start_min, cal.day_end_min),
                }}
              >
                D{day} <small>{clock(cal.day_start_min).slice(3)}–{clock(cal.day_end_min).slice(3)}</small>
              </span>
            ))}
          </div>
        </div>

        {rows.map(({ machine, bars, overtimeBands }) => (
          <div key={machine.id} className={`gantt-row${machine.status === "down" ? " is-down" : ""}`}>
            <div className="gantt-name">
              <strong>{machine.id}</strong>
              <small>{machine.status === "down" ? "✕ 停機" : `射出機 ${machine.tonnage} 噸`}</small>
            </div>
            <div className="gantt-track">
              {days.map((day) => (
                <span
                  key={day}
                  className="gantt-shift"
                  style={{
                    left: pct(day * MIN_PER_DAY + cal.day_start_min),
                    width: width(cal.day_start_min, cal.day_end_min),
                  }}
                />
              ))}
              {overtimeBands.map((band) => (
                <span
                  key={band.from}
                  className="gantt-overtime"
                  style={{ left: pct(band.from), width: width(band.from, band.to) }}
                  title={`加班 ${band.to - band.from} 分`}
                />
              ))}
              {bars.map((bar) => {
                const widest = bar.segments.reduce((a, b) => (b.to - b.from > a.to - a.from ? b : a));
                return bar.segments.map((segment) => (
                  <span
                    key={`${bar.op.work_order_id}-${segment.from}`}
                    className={`gantt-bar tier-${bar.customer.tier}${segment.setup ? " is-setup" : ""}${flagged?.has(bar.op.work_order_id) ? " is-flagged" : ""}`}
                    style={{ left: pct(segment.from), width: width(segment.from, segment.to) }}
                    tabIndex={segment === widest ? 0 : -1}
                    aria-label={`${bar.op.work_order_id} ${clock(bar.op.start_min)} 至 ${clock(bar.op.end_min)}`}
                    onMouseEnter={(e) => show(bar, e.currentTarget)}
                    onMouseLeave={() => setTip(null)}
                    onFocus={(e) => show(bar, e.currentTarget)}
                    onBlur={() => setTip(null)}
                  >
                    {segment === widest && (segment.to - segment.from) / span > 0.035 && (
                      <span className="gantt-label">
                        {bar.customer.tier === "strategic" && "★"}
                        {bar.op.work_order_id.replace("WO-", "")}
                        {bar.workOrder.is_rush && <em>急</em>}
                        {bar.late > 0 && <em className="late">逾</em>}
                      </span>
                    )}
                  </span>
                ));
              })}
            </div>
          </div>
        ))}
      </div>

      {tip && (
        <div className="tooltip" style={{ left: tip.x, top: tip.y }} role="status">
          <strong>
            {tip.bar.op.work_order_id}
            {tip.bar.workOrder.is_rush && " · 急單"}
          </strong>
          <span>{tip.bar.customer.name}</span>
          <dl>
            <dt>產品</dt>
            <dd>
              {productNames.get(tip.bar.workOrder.product_id) || tip.bar.workOrder.product_id} × {num(tip.bar.workOrder.quantity - tip.bar.workOrder.quantity_done)} 件
            </dd>
            <dt>時間</dt>
            <dd>
              {clock(tip.bar.op.start_min)} → {clock(tip.bar.op.end_min)}
            </dd>
            <dt>換模</dt>
            <dd>{tip.bar.op.setup_min ? `${tip.bar.op.setup_min} 分${tip.bar.op.mold_changed ? "(換模具)" : ""}` : "無"}</dd>
            <dt>交期</dt>
            <dd>
              {clock(tip.bar.workOrder.due_min)} · {tip.bar.late ? `延誤 ${num(tip.bar.late)} 分` : "準時"}
            </dd>
          </dl>
        </div>
      )}
    </div>
  );
}
