import type { Evaluation, Snapshot } from "@/lib/types";

const FLOW = [
  { step: "接單", note: "訂單、急單" },
  { step: "備料", note: "確認塑料庫存" },
  { step: "換模", note: "掛模具、換料換色" },
  { step: "射出成型", note: "六台擇一" },
  { step: "交貨", note: "對照承諾交期" },
];

/** 機台一覽:先說明一張工單怎麼走,再說每台機台能做什麼、現在在做什麼。 */
export default function Machines({ snapshot, schedule }: { snapshot: Snapshot; schedule?: Evaluation }) {
  const products = new Map(snapshot.products.map((p) => [p.id, p]));
  const workOrders = new Map(snapshot.work_orders.map((w) => [w.id, w]));

  return (
    <>
      <h3>一張工單的流程</h3>
      <ol className="flow">
        {FLOW.map((node, i) => (
          <li key={node.step} className={i === 3 ? "is-core" : ""}>
            <b>{node.step}</b>
            <small>{node.note}</small>
          </li>
        ))}
      </ol>
      <p className="footnote">
        六台射出機是<b>並行</b>的,不是前後相接的產線:每張工單只上其中一台,一次射出就完成。哪台能做取決於模具需要的鎖模力。
      </p>

      <h3 className="spaced">各機台</h3>
      <ul className="machine-cards">
        {snapshot.machines.map((machine) => {
          const molds = snapshot.molds.filter((m) => m.min_tonnage <= machine.tonnage && machine.tonnage <= m.max_tonnage);
          const makeable = snapshot.products.filter((p) => molds.some((m) => m.id === p.mold_id));
          const queue = (schedule?.operations ?? [])
            .filter((op) => op.machine_id === machine.id)
            .sort((a, b) => a.start_min - b.start_min);
          const first = queue[0] && products.get(workOrders.get(queue[0].work_order_id)?.product_id ?? "");
          return (
            <li key={machine.id} className={machine.status === "down" ? "is-down" : ""}>
              <header>
                <b>{machine.id}</b>
                <span className={`chip ${machine.status === "down" ? "critical" : "good"}`}>
                  {machine.status === "down" ? "✕ 停機" : "✓ 運轉"}
                </span>
              </header>
              <p className="role">{machine.role || "射出成型機"}</p>
              <dl>
                <dt>鎖模力</dt>
                <dd>{machine.tonnage} 噸</dd>
                <dt>機上模具</dt>
                <dd>
                  {machine.current_mold_id ?? "無"}
                  {machine.current_material_id && `・${machine.current_material_id}`}
                </dd>
                <dt>排程</dt>
                <dd>
                  {machine.status === "down"
                    ? "不排產"
                    : queue.length
                      ? `${queue.length} 張,先做 ${first?.name || queue[0].work_order_id}`
                      : "目前沒有工單"}
                </dd>
              </dl>
              <details>
                <summary>能做 {makeable.length} 種產品</summary>
                <ul>
                  {makeable.map((p) => (
                    <li key={p.id}>
                      {p.name || p.id} <small>{p.id}・模具 {p.mold_id}</small>
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          );
        })}
      </ul>
    </>
  );
}
