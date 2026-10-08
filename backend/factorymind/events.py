"""擾動事件:套用後產生新版本的快照,不修改原快照。事件發生時間即快照第 0 分鐘。"""

from .models import FactorySnapshot, MachineStatus, WorkOrder, WorkOrderStatus, by_id


def machine_down(snapshot: FactorySnapshot, machine_id: str) -> FactorySnapshot:
    """機台停機,復機時間未知。機上已開工的工單中斷,剩餘數量需重新安排。"""
    if machine_id not in by_id(snapshot.machines):
        raise ValueError(f"不存在的機台 {machine_id}")
    machines = [
        m.model_copy(update={"status": MachineStatus.DOWN}) if m.id == machine_id else m for m in snapshot.machines
    ]
    work_orders = [
        w.model_copy(update={"status": WorkOrderStatus.NOT_STARTED, "machine_id": None})
        if w.status == WorkOrderStatus.IN_PROGRESS and w.machine_id == machine_id
        else w
        for w in snapshot.work_orders
    ]
    return snapshot.model_copy(
        update={"machines": machines, "work_orders": work_orders, "version": snapshot.version + 1}
    )


def add_rush_order(snapshot: FactorySnapshot, work_order: WorkOrder) -> FactorySnapshot:
    if work_order.id in by_id(snapshot.work_orders):
        raise ValueError(f"工單編號 {work_order.id} 已存在")
    if work_order.product_id not in by_id(snapshot.products):
        raise ValueError(f"不存在的產品 {work_order.product_id}")
    if work_order.customer_id not in by_id(snapshot.customers):
        raise ValueError(f"不存在的客戶 {work_order.customer_id}")
    return snapshot.model_copy(
        update={"work_orders": [*snapshot.work_orders, work_order], "version": snapshot.version + 1}
    )
