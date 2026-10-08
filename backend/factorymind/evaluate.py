"""方案評估介面:接收候選方案,回傳有版本的計算結果。"""

from collections import Counter

from . import ENGINE_VERSION
from .models import CustomerTier, FactorySnapshot, MachineStatus, WorkOrderStatus, by_id
from .plan import Evaluation, EvaluationStatus, Kpis, Operation, Plan
from .policy import Policy
from .replay import replay, working_windows
from .rules import check_hard_rules


def _validate(snapshot: FactorySnapshot, plan: Plan) -> list[str]:
    machines = by_id(snapshot.machines)
    work_orders = by_id(snapshot.work_orders)
    errors = [f"方案引用不存在的機台 {m}" for m in plan.machine_sequences if m not in machines]
    errors += [f"加班引用不存在的機台 {g.machine_id}" for g in plan.overtime if g.machine_id not in machines]
    errors += [
        f"加班日 {g.day} 超出排程範圍({snapshot.calendar.horizon_days} 天)"
        for g in plan.overtime
        if g.day >= snapshot.calendar.horizon_days
    ]
    placed = [wo_id for seq in plan.machine_sequences.values() for wo_id in seq] + plan.outsourced
    for wo_id, count in Counter(placed).items():
        if wo_id not in work_orders:
            errors.append(f"方案引用不存在的工單 {wo_id}")
        elif work_orders[wo_id].status == WorkOrderStatus.DONE:
            errors.append(f"工單 {wo_id} 已完成,不可再安排")
        if count > 1:
            errors.append(f"工單 {wo_id} 被安排 {count} 次")
    return errors


def _changed_count(plan: Plan, operations: list[Operation], reference: Evaluation) -> int:
    """改動既有工單數:機台或排定時間改變、或改為外包,各工單計一次。新急單不在對照排程內,不計。"""
    now = {op.work_order_id: (op.machine_id, op.start_min) for op in operations}
    changed = 0
    for ref in reference.operations:
        if ref.work_order_id in plan.outsourced or now.get(ref.work_order_id) != (ref.machine_id, ref.start_min):
            changed += 1
    return changed + sum(1 for wo_id in reference.outsourced if wo_id not in plan.outsourced)


def evaluate(
    snapshot: FactorySnapshot, plan: Plan, policy: Policy, reference: Evaluation | None = None
) -> Evaluation:
    """reference 為異常前有效排程的評估結果,用來計算改動工單數。"""
    base = dict(
        plan_id=plan.id,
        factory_id=snapshot.factory_id,
        snapshot_version=snapshot.version,
        policy_version=policy.version,
        engine_version=ENGINE_VERSION,
    )
    errors = _validate(snapshot, plan)
    if errors:
        return Evaluation(**base, status=EvaluationStatus.INVALID_PLAN, errors=errors)

    result = replay(snapshot, plan)
    operations = result.operations
    violations = check_hard_rules(snapshot, plan, policy, operations)

    placed = {op.work_order_id for op in operations} | set(result.unfinished) | set(plan.outsourced)
    reasons = [f"工單 {w.id} 未被安排" for w in snapshot.open_work_orders() if w.id not in placed]
    reasons += [f"工單 {wo_id} 無法在排程範圍內完成" for wo_id in result.unfinished]
    if reasons:
        # 不可行時不產出指標,避免以零延誤、零成本代替
        return Evaluation(
            **base,
            status=EvaluationStatus.OK,
            feasible=False,
            infeasible_reasons=reasons,
            violations=violations,
            operations=operations,
            outsourced=plan.outsourced,
        )

    work_orders = by_id(snapshot.work_orders)
    products = by_id(snapshot.products)
    customers = by_id(snapshot.customers)
    completion = {op.work_order_id: op.end_min for op in operations}
    completion.update({wo_id: snapshot.outsource_lead_time_min for wo_id in plan.outsourced})
    tardiness = {wo_id: max(0, end - work_orders[wo_id].due_min) for wo_id, end in completion.items()}

    rates = snapshot.cost_rates
    setup_min = sum(op.setup_min for op in operations)
    costs = {
        "overtime": sum(g.minutes for g in plan.overtime) * rates.overtime_ntd_per_min,
        "outsourcing": sum(
            work_orders[w].remaining * products[work_orders[w].product_id].outsource_unit_cost_ntd
            for w in plan.outsourced
            if products[work_orders[w].product_id].outsource_unit_cost_ntd is not None
        ),
        "setup": setup_min * rates.setup_ntd_per_min,
    }

    busy: Counter[str] = Counter()
    for op in operations:
        busy[op.machine_id] += op.end_min - op.start_min
    load = {}
    for machine in snapshot.machines:
        if machine.status == MachineStatus.UP:
            regular = sum(end - start for start, end in working_windows(snapshot, machine, []))
            load[machine.id] = round(busy[machine.id] / regular, 3)

    kpis = Kpis(
        strategic_tardiness_min=sum(
            t for w, t in tardiness.items() if customers[work_orders[w].customer_id].tier == CustomerTier.STRATEGIC
        ),
        total_tardiness_min=sum(tardiness.values()),
        late_work_orders=sorted(w for w, t in tardiness.items() if t > 0),
        changed_work_orders=_changed_count(plan, operations, reference) if reference else None,
        cost_ntd=sum(costs.values()),
        cost_breakdown_ntd=costs,
        mold_changes=sum(1 for op in operations if op.mold_changed),
        setup_min=setup_min,
        machine_load=load,
    )
    return Evaluation(
        **base,
        status=EvaluationStatus.OK,
        feasible=True,
        violations=violations,
        kpis=kpis,
        operations=operations,
        outsourced=plan.outsourced,
    )
