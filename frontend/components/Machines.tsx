import type { Snapshot } from "@/lib/types";

/** 機台一覽:每台射出機的規格、現況,以及依鎖模力範圍可以上的模具與產品。 */
export default function Machines({ snapshot }: { snapshot: Snapshot }) {
  return (
    <section className="card">
      <header className="card-head">
        <h2>機台一覽</h2>
        <p>
          本廠 {snapshot.machines.length} 台皆為塑膠射出成型機,以鎖模力(噸)區分大小。模具只能上鎖模力落在其適用範圍內的機台,所以停機時能接手的機台有限。
        </p>
      </header>
      <div className="table-scroll">
        <table className="machines">
          <thead>
            <tr>
              <th>機台</th>
              <th>機型</th>
              <th className="num">鎖模力(噸)</th>
              <th>狀態</th>
              <th>目前機上</th>
              <th>可上的模具與產品</th>
            </tr>
          </thead>
          <tbody>
            {snapshot.machines.map((machine) => {
              const molds = snapshot.molds.filter(
                (m) => m.min_tonnage <= machine.tonnage && machine.tonnage <= m.max_tonnage,
              );
              return (
                <tr key={machine.id}>
                  <th scope="row">{machine.id}</th>
                  <td>射出成型機</td>
                  <td className="num">{machine.tonnage}</td>
                  <td>
                    {machine.status === "down" ? (
                      <span className="chip critical">✕ 停機</span>
                    ) : (
                      <span className="chip good">✓ 運轉中</span>
                    )}
                  </td>
                  <td>
                    {machine.current_mold_id
                      ? `模具 ${machine.current_mold_id} · 塑料 ${machine.current_material_id ?? "—"}`
                      : "未掛模具"}
                  </td>
                  <td>
                    <div className="molds">
                      {molds.map((mold) => (
                        <span key={mold.id}>
                          {mold.id}
                          <small>
                            {mold.min_tonnage}–{mold.max_tonnage} 噸 · 產品{" "}
                            {snapshot.products
                              .filter((p) => p.mold_id === mold.id)
                              .map((p) => p.id)
                              .join("、")}
                          </small>
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
