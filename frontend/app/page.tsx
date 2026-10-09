import Machines from "@/components/Machines";
import Workbench from "@/components/Workbench";
import { loadRun } from "@/lib/data";
import { METRIC_LABEL, clock, ntd, num } from "@/lib/format";

export default async function Page() {
  const run = await loadRun();
  const winner = run.evaluations.find((e) => e.plan_id === run.ranking.winner);
  const winnerPlan = run.plans.find((p) => p.id === run.ranking.winner);
  const eligible = run.evaluations.filter((e) => e.status === "ok" && e.feasible && e.violations?.length === 0);
  const customers = new Map(run.after.customers.map((c) => [c.id, c]));
  const limits = run.policy.hard_limits;

  return (
    <main>
      <header className="page-head">
        <div>
          <p className="eyebrow">FactoryMind · 事件工作台</p>
          <h1>射出機 IM-02 停機與策略客戶急單</h1>
        </div>
        <ul className="meta">
          <li>快照 v{run.after.version}</li>
          <li>政策 {run.policy.version}</li>
          <li>計算引擎 {run.evaluations[0].engine_version}</li>
          <li className="warn">合成資料,非實測值</li>
        </ul>
      </header>

      <section className="summary">
        <div className="card hero">
          <p className="eyebrow">系統建議</p>
          {winner?.kpis && winnerPlan ? (
            <>
              <h2>
                <span className="chip good">✓ 推薦</span> {winnerPlan.id} {winnerPlan.label}
              </h2>
              <dl className="tiles">
                <div>
                  <dt>策略客戶延誤</dt>
                  <dd>{num(winner.kpis.strategic_tardiness_min)} <small>分</small></dd>
                </div>
                <div>
                  <dt>總延誤</dt>
                  <dd>{num(winner.kpis.total_tardiness_min)} <small>分</small></dd>
                </div>
                <div>
                  <dt>改動工單</dt>
                  <dd>{winner.kpis.changed_work_orders ?? "—"} <small>張</small></dd>
                </div>
                <div>
                  <dt>成本</dt>
                  <dd>{ntd(winner.kpis.cost_ntd)}</dd>
                </div>
              </dl>
              <p className="footnote">
                {run.evaluations.length} 個候選方案中 {eligible.length} 個合規且可行。尚待主管核准,系統未執行任何變更。
              </p>
            </>
          ) : (
            <h2>未選出方案:{run.ranking.undecided_reason}</h2>
          )}
        </div>

        <div className="card">
          <p className="eyebrow">事件</p>
          <ul className="events">
            {run.events.map((event) => {
              if (event.type === "machine_down") {
                const hit = run.original.operations.filter((op) => op.machine_id === event.subject);
                return (
                  <li key={event.subject}>
                    <b>✕ 射出機 {event.subject} 停機</b>
                    <span>{event.note};原排在此機台的 {hit.map((op) => op.work_order_id).join("、")} 需重新安排。</span>
                  </li>
                );
              }
              const w = run.after.work_orders.find((x) => x.id === event.subject)!;
              return (
                <li key={event.subject}>
                  <b>＋ 急單 {w.id}</b>
                  <span>
                    {customers.get(w.customer_id)?.name},{w.product_id} × {num(w.quantity)} 件,交期 {clock(w.due_min)}。
                  </span>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="card">
          <p className="eyebrow">企業政策</p>
          <ol className="policy">
            {run.policy.ranking.map((c) => (
              <li key={c.metric}>
                {METRIC_LABEL[c.metric]}
                {c.threshold > 0 && <small> 差 {num(c.threshold)} 以內同等</small>}
              </li>
            ))}
          </ol>
          <p className="footnote">
            硬規則:單機單日加班上限 {limits.max_overtime_min_per_machine_day} 分;加班預算上限 {ntd(limits.overtime_budget_ntd)}。
          </p>
        </div>
      </section>

      <Workbench run={run} />
      <Machines snapshot={run.after} />
    </main>
  );
}
