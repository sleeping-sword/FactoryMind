"""硬規則檢核:不允許突破的底線,由程式判定,AI 不得放寬。"""

from collections import defaultdict

from .models import FactorySnapshot, MachineStatus, WorkOrderStatus, by_id, is_compatible
from .plan import Operation, Plan, Violation
from .policy import Policy


def check_hard_rules(
    snapshot: FactorySnapshot, plan: Plan, policy: Policy, operations: list[Operation]
) -> list[Violation]:
    machines = by_id(snapshot.machines)
    molds = by_id(snapshot.molds)
    products = by_id(snapshot.products)
    work_orders = by_id(snapshot.work_orders)
    limits = policy.hard_limits
    violations: list[Violation] = []

    for machine_id, sequence in plan.machine_sequences.items():
        machine = machines[machine_id]
        if sequence and machine.status == MachineStatus.DOWN:
            violations.append(
                Violation(
                    code="MACHINE_UNAVAILABLE",
                    message=f"{machine_id} 停機中,不可安排生產",
                    subjects=[machine_id, *sequence],
                )
            )
        for wo_id in sequence:
            mold = molds[products[work_orders[wo_id].product_id].mold_id]
            if not is_compatible(mold, machine):
                violations.append(
                    Violation(
                        code="MOLD_MACHINE_INCOMPATIBLE",
                        message=f"模具 {mold.id}({mold.min_tonnage}–{mold.max_tonnage} 噸)不可上 {machine_id}({machine.tonnage} 噸)",
                        subjects=[wo_id, mold.id, machine_id],
                    )
                )

    for wo in snapshot.work_orders:
        if wo.status != WorkOrderStatus.IN_PROGRESS:
            continue
        if plan.machine_sequences.get(wo.machine_id, [None])[0] != wo.id:
            violations.append(
                Violation(
                    code="STARTED_WORK_ORDER_MOVED",
                    message=f"{wo.id} 已在 {wo.machine_id} 開工,不可轉機、延後或外包",
                    subjects=[wo.id, wo.machine_id],
                )
            )

    needed_kg: dict[str, float] = defaultdict(float)
    for sequence in plan.machine_sequences.values():
        for wo_id in sequence:
            wo = work_orders[wo_id]
            product = products[wo.product_id]
            needed_kg[product.material_id] += wo.remaining * product.material_kg_per_unit
    for material in snapshot.materials:
        if needed_kg[material.id] > material.on_hand_kg:
            violations.append(
                Violation(
                    code="MATERIAL_SHORTAGE",
                    message=f"{material.id} 需求 {needed_kg[material.id]:.1f} kg,庫存 {material.on_hand_kg:.1f} kg",
                    subjects=[material.id],
                )
            )

    for wo_id in plan.outsourced:
        if products[work_orders[wo_id].product_id].outsource_unit_cost_ntd is None:
            violations.append(
                Violation(code="OUTSOURCE_NOT_ALLOWED", message=f"{wo_id} 的產品不可外包", subjects=[wo_id])
            )

    overtime_by_slot: dict[tuple[str, int], int] = defaultdict(int)
    for grant in plan.overtime:
        overtime_by_slot[(grant.machine_id, grant.day)] += grant.minutes
    for (machine_id, day), minutes in sorted(overtime_by_slot.items()):
        if minutes > limits.max_overtime_min_per_machine_day:
            violations.append(
                Violation(
                    code="OVERTIME_DAILY_LIMIT",
                    message=f"{machine_id} 第 {day} 天加班 {minutes} 分鐘,超過單日上限 {limits.max_overtime_min_per_machine_day} 分鐘",
                    subjects=[machine_id],
                )
            )
    overtime_cost = sum(overtime_by_slot.values()) * snapshot.cost_rates.overtime_ntd_per_min
    if overtime_cost > limits.overtime_budget_ntd:
        violations.append(
            Violation(
                code="OVERTIME_BUDGET",
                message=f"加班費 NT${overtime_cost:,.0f} 超過預算上限 NT${limits.overtime_budget_ntd:,.0f}",
            )
        )

    completion = {op.work_order_id: op.end_min for op in operations}
    completion.update({wo_id: snapshot.outsource_lead_time_min for wo_id in plan.outsourced})
    for wo_id, end in completion.items():
        wo = work_orders[wo_id]
        if wo.max_delay_min is not None and end - wo.due_min > wo.max_delay_min:
            violations.append(
                Violation(
                    code="DELAY_LIMIT_EXCEEDED",
                    message=f"{wo_id} 延誤 {end - wo.due_min} 分鐘,超過容許上限 {wo.max_delay_min} 分鐘",
                    subjects=[wo_id],
                )
            )

    return violations
