"""候選方案產生器:對任一份快照提出一組可比較的處理方式。

只提出引擎支援的動作:轉相容機台與重排、加班、外包。加班與外包建立在目前已生效的決定之上。
方案是否合規由評估與排序決定,這裡不預先篩掉,所以被硬規則擋下的方案也會留在比較表裡。
"""

from .dispatch import dispatch
from .evaluate import evaluate
from .models import FactorySnapshot, MachineStatus, by_id
from .optimize import SearchResult, optimize
from .plan import Evaluation, OvertimeGrant, Plan
from .policy import Policy

SEARCH_BUDGET = 8_000


def _busiest_machines(snapshot: FactorySnapshot, evaluation: Evaluation) -> list[str]:
    up = [m.id for m in snapshot.machines if m.status == MachineStatus.UP]
    if evaluation.kpis is None:
        return up
    return sorted(up, key=lambda m: (-evaluation.kpis.machine_load.get(m, 0), m))


def _with_overtime(base: list[OvertimeGrant], machines: list[str], days: range, minutes: int) -> list[OvertimeGrant]:
    taken = {(g.machine_id, g.day) for g in base}
    extra = [
        OvertimeGrant(machine_id=m, day=d, minutes=minutes) for m in machines for d in days if (m, d) not in taken
    ]
    return [*base, *extra]


def generate_candidates(
    snapshot: FactorySnapshot,
    policy: Policy,
    reference: Evaluation | None,
    *,
    base_overtime: list[OvertimeGrant] | None = None,
    base_outsourced: list[str] | None = None,
    rush_ids: list[str] | None = None,
) -> tuple[list[Plan], dict[str, SearchResult]]:
    overtime = base_overtime or []
    outsourced = base_outsourced or []
    products = by_id(snapshot.products)
    work_orders = by_id(snapshot.work_orders)
    daily_limit = policy.hard_limits.max_overtime_min_per_machine_day
    searches: dict[str, SearchResult] = {}

    def search(plan_id: str, label: str, **levers) -> Plan:
        result = optimize(
            snapshot, policy, plan_id=plan_id, label=label, reference=reference, max_evaluations=SEARCH_BUDGET,
            overtime=levers.get("overtime", overtime), outsourced=levers.get("outsourced", outsourced),
        )
        searches[plan_id] = result
        return result.plan

    plans = [
        dispatch(snapshot, plan_id="P1", label="依交期重排", rule="edd", overtime=overtime, outsourced=outsourced),
        dispatch(snapshot, plan_id="P2", label="策略客戶優先", rule="priority", overtime=overtime, outsourced=outsourced),
        search("P3", "合併換模重排"),
    ]
    busiest = _busiest_machines(snapshot, evaluate(snapshot, plans[-1], policy, reference))

    if daily_limit > 0 and busiest:
        one = _with_overtime(overtime, busiest[:1], range(1), daily_limit)
        if len(one) > len(overtime):
            plans.append(search(f"P{len(plans) + 1}", f"合併換模重排 + {busiest[0]} 加班", overtime=one))
        two = _with_overtime(overtime, busiest[:2], range(min(2, snapshot.calendar.horizon_days)), daily_limit)
        if len(two) > len(one):
            plans.append(
                dispatch(
                    snapshot, plan_id=f"P{len(plans) + 1}", label=f"策略客戶優先 + {'、'.join(busiest[:2])} 加班兩天",
                    rule="priority", overtime=two, outsourced=outsourced,
                )
            )

    can_outsource = [
        w for w in rush_ids or []
        if w not in outsourced and products[work_orders[w].product_id].outsource_unit_cost_ntd is not None
    ]
    if can_outsource:
        plans.append(
            search(
                f"P{len(plans) + 1}", f"急單 {'、'.join(can_outsource)} 外包",
                outsourced=[*outsourced, *can_outsource],
            )
        )
    return plans, searches
