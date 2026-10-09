"use client";

import { useState } from "react";
import Gantt from "./Gantt";
import { COST_LABEL, METRIC_LABEL, MIN_PER_DAY, clock, isMetric, ntd, num } from "@/lib/format";
import type { DemoRun, Elimination, Evaluation, Metric, Plan } from "@/lib/types";

const ORIGINAL = "ORIGINAL";

function Status({ run, evaluation }: { run: DemoRun; evaluation: Evaluation }) {
  const out = run.ranking.eliminated.find((e) => e.plan_id === evaluation.plan_id);
  if (run.ranking.winner === evaluation.plan_id) return <span className="chip good">✓ 推薦</span>;
  if (!out) return <span className="chip">同等</span>;
  if (isMetric(out.stage)) {
    const order = run.policy.ranking.findIndex((c) => c.metric === out.stage) + 1;
    return <span className="chip">– 第 {order} 順位淘汰</span>;
  }
  return <span className="chip critical">✕ {out.stage === "硬規則" ? "違反硬規則" : `${out.stage}不通過`}</span>;
}

function MetricCell({ metric, evaluation, out }: { metric: Metric; evaluation: Evaluation; out?: Elimination }) {
  const value = evaluation.kpis?.[metric];
  if (value == null) return <td className="num">—</td>;
  const text = metric === "cost_ntd" ? ntd(value) : num(value);
  if (out?.stage !== metric) return <td className="num">{text}</td>;
  return (
    <td className="num flagged" title={`最佳值 ${num(out.best!)},容許差距 ${num(out.threshold!)}`}>
      <span aria-hidden>✕</span> {text}
    </td>
  );
}

export default function Workbench({ run }: { run: DemoRun }) {
  const [selected, setSelected] = useState(run.ranking.winner ?? run.plans[0].id);
  const isOriginal = selected === ORIGINAL;
  const evaluation = isOriginal ? run.original : run.evaluations.find((e) => e.plan_id === selected)!;
  const plan: Plan | undefined = run.plans.find((p) => p.id === selected);
  const snapshot = isOriginal ? run.before : run.after;
  const search = run.search[selected];
  const out = run.ranking.eliminated.find((e) => e.plan_id === selected);
  const metrics = run.policy.ranking.map((c) => c.metric);

  // 所有方案共用同一條時間軸,切換時才能直接比較
  const latest = Math.max(...[run.original, ...run.evaluations].flatMap((e) => e.operations.map((op) => op.end_min)));
  const cal = run.after.calendar;
  const axisEnd = Math.floor(latest / MIN_PER_DAY) * MIN_PER_DAY + cal.day_end_min + run.policy.hard_limits.max_overtime_min_per_machine_day;

  const workOrders = new Map(snapshot.work_orders.map((w) => [w.id, w]));
  const maxLoad = Math.max(1, ...Object.values(evaluation.kpis?.machine_load ?? {}));

  return (
    <>
      <section className="card">
        <header className="card-head">
          <h2>候選方案比較</h2>
          <p>由左至右是企業政策的比較順位;點選一列查看排程。</p>
        </header>
        <div className="table-scroll">
          <table className="plans">
            <thead>
              <tr>
                <th>方案</th>
                <th>結果</th>
                {run.policy.ranking.map((c, i) => (
                  <th key={c.metric} className="num">
                    <small>第 {i + 1} 順位</small>
                    {METRIC_LABEL[c.metric]}
                    {c.metric === "cost_ntd" ? "" : c.metric === "changed_work_orders" ? "(張)" : "(分)"}
                  </th>
                ))}
                <th className="num">逾期(張)</th>
                <th className="num">換模(次)</th>
              </tr>
            </thead>
            <tbody>
              {run.evaluations.map((ev, i) => {
                const elimination = run.ranking.eliminated.find((e) => e.plan_id === ev.plan_id);
                return (
                  <tr
                    key={ev.plan_id}
                    className={ev.plan_id === selected ? "is-selected" : ""}
                    onClick={() => setSelected(ev.plan_id)}
                  >
                    <th scope="row">
                      <button type="button" onClick={() => setSelected(ev.plan_id)} aria-pressed={ev.plan_id === selected}>
                        <b>{ev.plan_id}</b> {run.plans[i].label}
                      </button>
                    </th>
                    <td>
                      <Status run={run} evaluation={ev} />
                    </td>
                    {metrics.map((m) => (
                      <MetricCell key={m} metric={m} evaluation={ev} out={elimination} />
                    ))}
                    <td className="num">{ev.kpis ? ev.kpis.late_work_orders.length : "—"}</td>
                    <td className="num">{ev.kpis ? ev.kpis.mold_changes : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="footnote">
          ✕ 標示該方案被淘汰的指標。門檻:
          {run.policy.ranking
            .filter((c) => c.threshold > 0)
            .map((c) => `${METRIC_LABEL[c.metric]}差距 ${num(c.threshold)} 以內視為同等`)
            .join(";")}
          。{run.ranking.tie_break}
        </p>
      </section>

      <section className="card">
        <header className="card-head split">
          <div>
            <h2>
              {isOriginal ? "原始排程(事件發生前)" : `${selected} ${plan!.label}`}
            </h2>
            <p>
              {isOriginal
                ? `快照 v${run.before.version},尚未發生停機與急單。`
                : out
                  ? `淘汰原因:${out.reason}`
                  : selected === run.ranking.winner
                    ? "通過硬規則,並在各比較順位勝出。"
                    : "通過硬規則。"}
            </p>
          </div>
          <div className="toggle" role="group" aria-label="切換排程">
            <button type="button" aria-pressed={!isOriginal} onClick={() => setSelected(run.ranking.winner ?? run.plans[0].id)}>
              候選方案
            </button>
            <button type="button" aria-pressed={isOriginal} onClick={() => setSelected(ORIGINAL)}>
              原始排程
            </button>
          </div>
        </header>

        {plan && (plan.overtime.length > 0 || plan.outsourced.length > 0 || search) && (
          <ul className="levers">
            {plan.overtime.map((g) => (
              <li key={`${g.machine_id}-${g.day}`}>
                加班 {g.machine_id} · D{g.day} · {g.minutes} 分
              </li>
            ))}
            {plan.outsourced.map((id) => (
              <li key={id}>
                外包 {id} · {clock(run.after.outsource_lead_time_min)} 交回
              </li>
            ))}
            {search && (
              <li>
                局部搜尋:自 {search.start_rule.toUpperCase()} 法則出發,改進 {search.moves} 步,試算 {num(search.evaluations)} 個方案,
                {search.local_optimum ? "已達局部最佳(不保證全域最佳)" : "試算次數用完"}
              </li>
            )}
          </ul>
        )}

        {(evaluation.violations ?? []).map((v) => (
          <p key={v.code + v.message} className="alert">
            <span aria-hidden>✕</span> <b>硬規則</b> {v.message}
          </p>
        ))}
        {evaluation.infeasible_reasons.map((reason) => (
          <p key={reason} className="alert">
            <span aria-hidden>✕</span> <b>不可行</b> {reason}
          </p>
        ))}

        <div className="legend" aria-hidden>
          <span><i className="swatch tier-normal" /> 一般客戶</span>
          <span><i className="swatch tier-strategic" /> ★ 策略客戶</span>
          <span><i className="swatch is-setup" /> 換模／換色</span>
          <span><i className="swatch overtime" /> 加班時段</span>
          <span><i className="swatch night" /> 非上班時間</span>
        </div>
        <Gantt snapshot={snapshot} evaluation={evaluation} overtime={plan?.overtime ?? []} axisEnd={axisEnd} />

        {evaluation.kpis && (
          <div className="detail-grid">
            <div>
              <h3>成本 {ntd(evaluation.kpis.cost_ntd)}</h3>
              <dl className="rows">
                {Object.entries(evaluation.kpis.cost_breakdown_ntd).map(([key, value]) => (
                  <div key={key}>
                    <dt>{COST_LABEL[key] ?? key}</dt>
                    <dd>{ntd(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <h3>機台負荷(工作時間 ÷ 正常班時間)</h3>
              <div className="loads">
                {Object.entries(evaluation.kpis.machine_load).map(([machineId, load]) => (
                  <div key={machineId} className="load" title={`${machineId} ${Math.round(load * 100)}%`}>
                    <span>{machineId}</span>
                    <div className="load-track">
                      <i style={{ width: `${(load / maxLoad) * 100}%` }} />
                    </div>
                    <span className="num">{Math.round(load * 100)}%</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        <details className="table-view">
          <summary>以表格檢視排程({evaluation.operations.length} 張工單)</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>機台</th>
                  <th>工單</th>
                  <th>產品</th>
                  <th>開始</th>
                  <th>完成</th>
                  <th className="num">換模(分)</th>
                  <th>交期</th>
                  <th className="num">延誤(分)</th>
                </tr>
              </thead>
              <tbody>
                {[...evaluation.operations]
                  .sort((a, b) => a.machine_id.localeCompare(b.machine_id) || a.start_min - b.start_min)
                  .map((op) => {
                    const w = workOrders.get(op.work_order_id)!;
                    return (
                      <tr key={op.work_order_id}>
                        <td>{op.machine_id}</td>
                        <td>{op.work_order_id}</td>
                        <td>{w.product_id}</td>
                        <td>{clock(op.start_min)}</td>
                        <td>{clock(op.end_min)}</td>
                        <td className="num">{op.setup_min}</td>
                        <td>{clock(w.due_min)}</td>
                        <td className="num">{num(Math.max(0, op.end_min - w.due_min))}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </>
  );
}
