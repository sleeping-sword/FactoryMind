import Gantt from "./Gantt";
import Machines from "./Machines";
import { METRIC_LABEL, MIN_PER_DAY, STATUS, duration, ntd, num } from "@/lib/format";
import type { FactoryState } from "@/lib/types";

/** 營運總覽:先讓主管知道要不要介入,再看目前生效的排程。 */
export default function Overview({ state, onOpen }: { state: FactoryState; onOpen: (id: string) => void }) {
  const { snapshot, schedule, policy } = state;
  const kpis = schedule.evaluation.kpis;
  const up = snapshot.machines.filter((m) => m.status === "up").length;
  const open = snapshot.work_orders.filter((w) => w.status !== "done").length;
  const needing = state.incidents.filter((i) => STATUS[i.status].tone === "wait" || STATUS[i.status].tone === "critical");
  const latest = Math.max(snapshot.calendar.day_end_min, ...schedule.evaluation.operations.map((op) => op.end_min));
  const axisEnd = Math.floor(latest / MIN_PER_DAY) * MIN_PER_DAY + snapshot.calendar.day_end_min + policy.hard_limits.max_overtime_min_per_machine_day;
  const limits = policy.hard_limits;

  return (
    <>
      <section className={`card callout ${needing.length ? "wait" : "good"}`}>
        {needing.length ? (
          <>
            <h2>有 {needing.length} 件事需要處理</h2>
            <ul className="todo">
              {needing.map((i) => (
                <li key={i.id}>
                  <button type="button" className="link" onClick={() => onOpen(i.id)}>
                    {i.title}
                  </button>
                  <span>{STATUS[i.status].label}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <h2>✓ 目前不需要你介入</h2>
            {!state.running && <p>按「開始運轉」,工廠會自己發生狀況。</p>}
          </>
        )}
      </section>

      <section className="card">
        <dl className="tiles four">
          <div>
            <dt>可用機台</dt>
            <dd>{up} <small>/ {snapshot.machines.length} 台</small></dd>
          </div>
          <div>
            <dt>未完成工單</dt>
            <dd>{open} <small>張</small></dd>
          </div>
          <div>
            <dt>逾期訂單</dt>
            <dd>{kpis ? kpis.late_work_orders.length : "—"} <small>張{kpis && kpis.total_tardiness_min > 0 ? `・合計 ${duration(kpis.total_tardiness_min)}` : ""}</small></dd>
          </div>
          <div>
            <dt>額外成本</dt>
            <dd>{kpis ? ntd(kpis.cost_ntd) : "—"}</dd>
          </div>
        </dl>
      </section>

      <section className="card">
        <header className="card-head">
          <h2>目前排程</h2>
        </header>
        {!schedule.still_valid && (
          <p className="alert">
            <span aria-hidden>✕</span> 這份排程已無法照原樣執行,請處理等待中的事件。
          </p>
        )}
        <Legend />
        <Gantt snapshot={snapshot} evaluation={schedule.evaluation} overtime={schedule.plan.overtime} axisEnd={axisEnd} />
      </section>

      <details className="card fold">
        <summary>企業政策</summary>
        <div className="policy-grid">
          <div>
            <h3>硬規則(不可突破)</h3>
            <ul className="bullets">
              <li>單機單日加班 ≤ {limits.max_overtime_min_per_machine_day} 分</li>
              <li>加班費 ≤ {ntd(limits.overtime_budget_ntd)}</li>
              <li>模具與機台鎖模力相符;停機不排產</li>
              <li>已開工不轉機;缺料不排產</li>
              <li>不可延誤的訂單不得延誤</li>
            </ul>
          </div>
          <div>
            <h3>比較順位(越少越好)</h3>
            <ol className="policy">
              {policy.ranking.map((c) => (
                <li key={c.metric}>
                  {METRIC_LABEL[c.metric]}
                  {c.threshold > 0 && <small> 差 {num(c.threshold)} 內同等</small>}
                </li>
              ))}
            </ol>
          </div>
        </div>
      </details>

      <details className="card fold" open>
        <summary>機台與生產流程</summary>
        <Machines snapshot={snapshot} schedule={schedule.still_valid ? schedule.evaluation : undefined} />
      </details>
    </>
  );
}

export function Legend() {
  return (
    <div className="legend" aria-hidden>
      <span><i className="swatch tier-normal" /> 一般客戶</span>
      <span><i className="swatch tier-strategic" /> ★ 策略客戶</span>
      <span><i className="swatch is-setup" /> 換模／換色</span>
      <span><i className="swatch overtime" /> 加班時段</span>
      <span><i className="swatch night" /> 非上班時間</span>
    </div>
  );
}
