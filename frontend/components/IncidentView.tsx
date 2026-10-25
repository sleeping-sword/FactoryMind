"use client";

import { useEffect, useState } from "react";
import AiPanel from "./AiPanel";
import Gantt from "./Gantt";
import { Legend } from "./Overview";
import { api } from "@/lib/api";
import {
  MAX_REASON_CHARS,
  METRIC_LABEL,
  MIN_PER_DAY,
  STAGES,
  STATUS,
  clock,
  duration,
  explainElimination,
  ntd,
  num,
  timeOfDay,
} from "@/lib/format";
import type { Evaluation, Incident, Metric, Plan, Policy } from "@/lib/types";

type Act = (action: () => Promise<unknown>) => Promise<void>;

const ORIGINAL = "ORIGINAL";

function eligible(ev: Evaluation): boolean {
  return ev.status === "ok" && ev.feasible === true && ev.violations?.length === 0;
}

/** 這個方案相對原排程,每張工單具體會怎麼變。 */
function changesOf(incident: Incident, evaluation: Evaluation) {
  const before = new Map(incident.reference.operations.map((op) => [op.work_order_id, op]));
  const rows: { id: string; kind: string; from: string; to: string }[] = [];
  for (const op of evaluation.operations) {
    const old = before.get(op.work_order_id);
    const to = `${op.machine_id}・${clock(op.start_min)} 開始`;
    if (!old) rows.push({ id: op.work_order_id, kind: "新排入", from: "尚未排入", to });
    else if (old.machine_id !== op.machine_id)
      rows.push({ id: op.work_order_id, kind: "換機台", from: `${old.machine_id}・${clock(old.start_min)} 開始`, to });
    else if (old.start_min !== op.start_min)
      rows.push({ id: op.work_order_id, kind: "改時間", from: `${old.machine_id}・${clock(old.start_min)} 開始`, to });
  }
  for (const id of evaluation.outsourced) {
    if (incident.reference.outsourced.includes(id)) continue;
    const old = before.get(id);
    rows.push({
      id,
      kind: "改外包",
      from: old ? `${old.machine_id}・${clock(old.start_min)} 開始` : "尚未排入",
      to: `外包・${clock(incident.snapshot.outsource_lead_time_min)} 交回`,
    });
  }
  const order = ["新排入", "改外包", "換機台", "改時間"];
  return rows.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || a.id.localeCompare(b.id));
}

function Stepper({ incident }: { incident: Incident }) {
  const reached = STAGES.findIndex((s) => s.key === incident.stage);
  const finished = incident.status === "closed";
  return (
    <ol className="stepper" aria-label="處理進度">
      {STAGES.map((stage, i) => {
        const state = finished || i < reached ? "done" : i === reached ? "current" : "todo";
        return (
          <li key={stage.key} className={state} aria-current={state === "current" ? "step" : undefined}>
            <span>{state === "done" ? "✓" : i + 1}</span>
            {stage.label}
          </li>
        );
      })}
    </ol>
  );
}

function DecisionPanel({
  incident,
  plan,
  evaluation,
  act,
}: {
  incident: Incident;
  plan: Plan;
  evaluation: Evaluation;
  act: Act;
}) {
  const [mode, setMode] = useState<"idle" | "approve" | "reject">("idle");
  const [reason, setReason] = useState("");
  useEffect(() => setMode("idle"), [plan.id, incident.status]);

  const recommended = incident.ranking?.winner === plan.id;
  const out = incident.ranking?.eliminated.find((e) => e.plan_id === plan.id);
  const ok = eligible(evaluation);
  const canDecide = incident.status === "awaiting_approval" || incident.status === "rejected";
  const changes = changesOf(incident, evaluation);
  // 口徑:既有工單改動(排序指標,不含新單)+ 新排入 = 本次變更
  const added = changes.filter((c) => c.from === "尚未排入").length;
  const existing = evaluation.kpis?.changed_work_orders ?? changes.length - added;
  const tally = `既有 ${existing} + 新排 ${added} = ${existing + added} 張`;
  const applied = incident.applied_plan_id === plan.id;
  const needsSignoff = plan.overtime.length > 0 || plan.outsourced.length > 0;

  const k = evaluation.kpis;
  const trimmed = reason.trim().length;
  const reasonProblem =
    trimmed === 0 ? "請寫下退回原因" : trimmed > MAX_REASON_CHARS ? `超過 ${trimmed - MAX_REASON_CHARS} 字,請刪減到 ${MAX_REASON_CHARS} 字內` : null;

  return (
    <div className={`decision ${recommended ? "good" : ok ? "" : "critical"}`}>
      <h3 className="decision-title">
        <span className={`chip ${recommended ? "good" : ok ? "" : "critical"}`}>
          {recommended ? "✓ 系統建議" : ok ? "可選" : "✕ 不能採用"}
        </span>
        {plan.id}・{plan.label}
        {ok && <span className={`chip ${applied ? "good" : ""}`}>{applied ? "已套用" : "試算・未套用"}</span>}
      </h3>

      {ok && k ? (
        <dl className="tiles">
          <div>
            <dt>策略客戶</dt>
            <dd>{k.strategic_tardiness_min === 0 ? "準時" : <>延誤 <small>{duration(k.strategic_tardiness_min)}</small></>}</dd>
          </div>
          <div>
            <dt>逾期訂單</dt>
            <dd>{k.late_work_orders.length} <small>張</small></dd>
          </div>
          <div>
            <dt>改動既有工單</dt>
            <dd>
              {existing} <small>張{added > 0 && `・另新排 ${added}`}</small>
            </dd>
          </div>
          <div>
            <dt>額外成本</dt>
            <dd>{ntd(k.cost_ntd)}</dd>
          </div>
        </dl>
      ) : (
        <ul className="bullets">
          {(evaluation.violations ?? []).map((v) => (
            <li key={v.message}>{v.message}</li>
          ))}
          {evaluation.infeasible_reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
          {evaluation.errors.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {ok && out && <p className="why-not">落選原因:{explainElimination(out)}</p>}
      {ok && needsSignoff && <p className="why-not">含加班或外包,核准後需另行通知人員或下單。</p>}

      {ok && (
        <details className="changes">
          <summary className="changes-sum">變更明細:{tally}</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>工單</th>
                  <th>變動</th>
                  <th>原本</th>
                  <th>調整後</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => (
                  <tr key={c.id}>
                    <td>{c.id}</td>
                    <td>{c.kind}</td>
                    <td>{c.from}</td>
                    <td>{c.to}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {canDecide && ok && mode === "idle" && (
        <div className="actions">
          <button type="button" className="primary" onClick={() => setMode("approve")}>
            {recommended ? "核准" : "改選這個方案"}
          </button>
          {incident.status === "awaiting_approval" && recommended && (
            <button type="button" onClick={() => setMode("reject")}>
              退回
            </button>
          )}
        </div>
      )}
      {mode === "approve" && (
        <div className="confirm" role="group" aria-label="確認核准">
          <p>
            核准 <b>{plan.id}</b>({tally})?{!recommended && "(非系統建議,會記入紀錄)"}
          </p>
          <div className="actions">
            <button type="button" className="primary" onClick={() => act(() => api.approve(incident.id, plan.id))}>
              確認核准
            </button>
            <button type="button" onClick={() => setMode("idle")}>
              取消
            </button>
          </div>
        </div>
      )}
      {mode === "reject" && (
        <div className="confirm" role="group" aria-label="確認退回">
          <label>
            退回原因(必填)
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="例如:這週不希望動到這麼多工單"
              aria-invalid={reasonProblem !== null}
              aria-describedby="reason-hint"
            />
          </label>
          <p id="reason-hint" className={`field-hint${reason.trim().length > MAX_REASON_CHARS ? " bad" : ""}`}>
            {reasonProblem ?? `${reason.trim().length} / ${MAX_REASON_CHARS} 字`}
          </p>
          <div className="actions">
            <button
              type="button"
              className="danger"
              disabled={reasonProblem !== null}
              onClick={() => act(() => api.reject(incident.id, reason.trim()))}
            >
              確認退回
            </button>
            <button type="button" onClick={() => setMode("idle")}>
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function IncidentView({
  incident,
  policy,
  aiEnabled,
  act,
}: {
  incident: Incident;
  policy: Policy;
  aiEnabled: boolean;
  act: Act;
}) {
  const fallback = incident.applied_plan_id ?? incident.ranking?.winner ?? incident.plans[0]?.id ?? ORIGINAL;
  const [selected, setSelected] = useState(fallback);
  // 換事件、重新評估或套用後,回到該看的那個方案
  useEffect(() => setSelected(fallback), [incident.id, fallback]);

  const status = STATUS[incident.status];
  const plan = incident.plans.find((p) => p.id === selected);
  const evaluation = incident.evaluations.find((e) => e.plan_id === selected);
  const viewingOriginal = !plan || !evaluation;
  const metrics = policy.ranking.map((c) => c.metric);
  const workOrders = new Map(incident.snapshot.work_orders.map((w) => [w.id, w]));
  const customers = new Map(incident.snapshot.customers.map((c) => [c.id, c]));
  const down = new Set(incident.events.filter((e) => e.type === "machine_down").map((e) => e.subject));

  const cal = incident.snapshot.calendar;
  const latest = Math.max(
    cal.day_end_min,
    ...[incident.reference, ...incident.evaluations].flatMap((e) => e.operations.map((op) => op.end_min)),
  );
  const axisEnd = Math.floor(latest / MIN_PER_DAY) * MIN_PER_DAY + cal.day_end_min + policy.hard_limits.max_overtime_min_per_machine_day;
  const displaced = new Set(incident.reference.operations.filter((op) => down.has(op.machine_id)).map((op) => op.work_order_id));
  const lastDecision = incident.decisions[incident.decisions.length - 1];
  const applied = incident.plans.find((p) => p.id === incident.applied_plan_id);
  const canReassess = ["stale", "rejected", "no_compliant_plan", "failed"].includes(incident.status);

  const schedule = viewingOriginal ? incident.reference : evaluation!;

  return (
    <>
      <section className="card">
        <header className="card-head split">
          <div>
            <p className="eyebrow">
              {incident.id}・{timeOfDay(incident.opened_at)}
            </p>
            <h2 className="incident-title">{incident.title}</h2>
          </div>
          <span className={`chip ${status.tone}`} title={`下一步:${status.next}`}>
            {status.icon} {status.label}
          </span>
        </header>
        <Stepper incident={incident} />
        <ul className="facts">
          {incident.events.map((event) => {
            if (event.type === "machine_down") {
              return (
                <li key={event.subject}>
                  <b>✕ {event.subject} 停機</b>
                  {displaced.size > 0 && <span>{[...displaced].join("、")} 要搬走</span>}
                </li>
              );
            }
            const w = event.work_order!;
            return (
              <li key={event.subject}>
                <b>＋ 急單 {w.id}</b>
                <span>
                  {customers.get(w.customer_id)?.name}・{incident.snapshot.products.find((p) => p.id === w.product_id)?.name || w.product_id} × {num(w.quantity)}・交期 {clock(w.due_min)}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      {incident.status === "processing" && (
        <section className="card callout" aria-live="polite">
          <h2>系統正在{STAGES.find((s) => s.key === incident.stage)?.label}…</h2>
        </section>
      )}
      {incident.status === "failed" && (
        <section className="card callout critical">
          <h2>✕ 處理失敗</h2>
          <p>{incident.error}</p>
        </section>
      )}
      {incident.status === "no_compliant_plan" && (
        <section className="card callout critical">
          <h2>✕ 沒有合規的方案</h2>
          <p>需調整條件或補資料後重新評估。</p>
        </section>
      )}
      {incident.status === "stale" && (
        <section className="card callout wait">
          <h2>↻ 方案已過期</h2>
          <p>工廠狀態變了,請重新評估。</p>
        </section>
      )}
      {incident.status === "rejected" && lastDecision && (
        <section className="card callout wait">
          <h2>↩ 已退回建議</h2>
          <p>{lastDecision.reason || "可改選其他方案,或重新評估。"}</p>
        </section>
      )}
      {applied && lastDecision && (
        <section className="card callout good">
          <h2>✓ 已套用 {applied.id}・{applied.label}</h2>
          <p>
            {lastDecision.actor}・{timeOfDay(lastDecision.at)}
            {lastDecision.followed_recommendation ? "" : "・非系統建議"}
          </p>
        </section>
      )}
      {canReassess && (
        <div className="actions">
          <button type="button" className="primary" onClick={() => act(() => api.reassess(incident.id))}>
            重新評估
          </button>
        </div>
      )}

      {incident.tasks.some((t) => t.done) && (
        <section className="card">
          <ul className="tasks">
            {incident.tasks.filter((t) => t.done).map((task) => (
              <li key={task.id}>
                <b>✓ {task.subject} 已復機</b>
                <span>
                  {task.done_by}・{timeOfDay(task.done_at!)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <AiPanel incident={incident} aiEnabled={aiEnabled} />

      {incident.tasks.some((t) => !t.done) && incident.applied_plan_id && (
        <section className="card">
          <ul className="tasks">
            {incident.tasks.filter((t) => !t.done).map((task) => (
              <li key={task.id}>
                <b>◐ {task.subject} 維修中</b>
                <button type="button" onClick={() => act(() => api.confirmTask(incident.id, task.id))}>
                  回報已復機
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {incident.evaluations.length > 0 && (
        <section className="card">
          {plan && evaluation && <DecisionPanel incident={incident} plan={plan} evaluation={evaluation} act={act} />}

          <div className="table-scroll">
            <table className="plans">
              <thead>
                <tr>
                  <th>方案</th>
                  <th />
                  {policy.ranking.map((c) => (
                    <th key={c.metric} className="num">
                      {METRIC_LABEL[c.metric]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {incident.evaluations.map((ev, i) => {
                  const out = incident.ranking?.eliminated.find((e) => e.plan_id === ev.plan_id);
                  const cell = (metric: Metric) => {
                    const value = ev.kpis?.[metric];
                    if (value == null) return "—";
                    if (metric === "cost_ntd") return ntd(value);
                    return metric === "changed_work_orders" ? `${value} 張` : duration(value);
                  };
                  return (
                    <tr key={ev.plan_id} className={ev.plan_id === selected ? "is-selected" : ""} onClick={() => setSelected(ev.plan_id)}>
                      <th scope="row">
                        <button type="button" onClick={() => setSelected(ev.plan_id)} aria-pressed={ev.plan_id === selected}>
                          <b>{ev.plan_id}</b> {incident.plans[i].label}
                        </button>
                      </th>
                      <td>
                        {incident.applied_plan_id === ev.plan_id ? (
                          <span className="chip good">✓ 已套用</span>
                        ) : incident.ranking?.winner === ev.plan_id ? (
                          <span className="chip good">✓ 建議</span>
                        ) : !eligible(ev) ? (
                          <span className="chip critical">✕ {ev.feasible === false ? "排不進去" : "違規"}</span>
                        ) : null}
                      </td>
                      {metrics.map((m) => (
                        <td key={m} className={`num${out?.stage === m ? " flagged" : ""}`} title={out?.stage === m ? explainElimination(out) : undefined}>
                          {out?.stage === m && <span aria-hidden>✕ </span>}
                          {cell(m)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="footnote">由左到右是政策的比較順位;✕ 是落選的那一項。點一列可預覽。</p>
        </section>
      )}

      <section className="card">
        <header className="card-head split">
          <h2>
            {viewingOriginal ? "事件前基準" : `${plan!.id} ${incident.applied_plan_id === plan!.id ? "已套用" : "試算預覽・未套用"}`}
          </h2>
          {incident.plans.length > 0 && (
            <div className="toggle" role="group" aria-label="切換排程">
              <button type="button" aria-pressed={!viewingOriginal} onClick={() => setSelected(fallback === ORIGINAL ? incident.plans[0].id : fallback)}>
                方案
              </button>
              <button type="button" aria-pressed={viewingOriginal} onClick={() => setSelected(ORIGINAL)}>
                事件前
              </button>
            </div>
          )}
        </header>
        <Legend />
        <Gantt
          snapshot={incident.snapshot}
          evaluation={schedule}
          overtime={viewingOriginal ? [] : plan!.overtime}
          axisEnd={axisEnd}
          flagged={viewingOriginal ? displaced : undefined}
        />
        <details className="table-view">
          <summary>表格</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>機台</th>
                  <th>工單</th>
                  <th>產品</th>
                  <th>開始</th>
                  <th>完成</th>
                  <th>交期</th>
                  <th>延誤</th>
                </tr>
              </thead>
              <tbody>
                {[...schedule.operations]
                  .sort((a, b) => a.machine_id.localeCompare(b.machine_id) || a.start_min - b.start_min)
                  .map((op) => {
                    const w = workOrders.get(op.work_order_id)!;
                    const late = Math.max(0, op.end_min - w.due_min);
                    return (
                      <tr key={op.work_order_id}>
                        <td>{op.machine_id}</td>
                        <td>{op.work_order_id}</td>
                        <td>{w.product_id}</td>
                        <td>{clock(op.start_min)}</td>
                        <td>{clock(op.end_min)}</td>
                        <td>{clock(w.due_min)}</td>
                        <td>{late ? duration(late) : "準時"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <details className="card fold">
        <summary>處理歷程({incident.trace.length})</summary>
        <ol className="trace">
          {incident.trace.map((entry) => (
            <li key={entry.seq}>
              <time>{timeOfDay(entry.at)}</time>
              <span className="chip">{STAGES.find((s) => s.key === entry.stage)?.label}</span>
              <p>
                <b>{entry.actor}</b> {entry.message}
              </p>
            </li>
          ))}
        </ol>
      </details>
    </>
  );
}
