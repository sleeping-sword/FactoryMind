"""派工法則排程器:不用最佳化的規則基準,也是對照實驗的現況組。"""

from typing import Literal

from .models import CustomerTier, FactorySnapshot, MachineStatus, WorkOrderStatus, by_id, is_compatible
from .plan import OvertimeGrant, Plan
from .replay import replay

Rule = Literal["fifo", "edd", "priority"]


def dispatch(
    snapshot: FactorySnapshot,
    *,
    plan_id: str,
    label: str,
    rule: Rule = "edd",
    overtime: list[OvertimeGrant] | None = None,
    outsourced: list[str] | None = None,
    assumptions: list[str] | None = None,
) -> Plan:
    """依法則排序工單,逐張放到能最早完成的相容機台。排不進去的工單留空,由評估回報不可行。"""
    overtime = overtime or []
    outsourced = outsourced or []
    molds = by_id(snapshot.molds)
    products = by_id(snapshot.products)
    customers = by_id(snapshot.customers)
    sequences: dict[str, list[str]] = {m.id: [] for m in snapshot.machines}

    pending = []
    for wo in snapshot.open_work_orders():
        if wo.id in outsourced:
            continue
        if wo.status == WorkOrderStatus.IN_PROGRESS:
            sequences[wo.machine_id].append(wo.id)
        else:
            pending.append(wo)

    if rule == "edd":
        pending.sort(key=lambda w: (w.due_min, w.id))
    elif rule == "priority":
        pending.sort(
            key=lambda w: (customers[w.customer_id].tier != CustomerTier.STRATEGIC, w.due_min, w.id)
        )

    def trial(machine_id: str, wo_id: str) -> Plan:
        seqs = {m: seq + [wo_id] if m == machine_id else seq for m, seq in sequences.items()}
        return Plan(id=plan_id, label=label, machine_sequences=seqs, overtime=overtime)

    for wo in pending:
        mold = molds[products[wo.product_id].mold_id]
        best = None
        for machine in snapshot.machines:
            if machine.status != MachineStatus.UP or not is_compatible(mold, machine):
                continue
            ops = {op.work_order_id: op for op in replay(snapshot, trial(machine.id, wo.id)).operations}
            if wo.id not in ops:
                continue
            key = (ops[wo.id].end_min, ops[wo.id].setup_min, machine.id)
            if best is None or key < best:
                best = key
        if best:
            sequences[best[2]].append(wo.id)

    return Plan(
        id=plan_id,
        label=label,
        machine_sequences=sequences,
        overtime=overtime,
        outsourced=outsourced,
        assumptions=assumptions or [],
    )
