"""虛擬工廠試跑:把方案的機台順序展開成實際時間。

確定性重播:同一份快照與方案必得到相同結果。試跑只反映物理行為
(班別、換模、同一副模具不能同時在兩台機上),不判斷規則是否合規。
"""

from collections import defaultdict
from math import ceil

from pydantic import BaseModel

from .models import MIN_PER_DAY, FactorySnapshot, Machine, MachineStatus, Product, by_id
from .plan import Operation, OvertimeGrant, Plan


class ReplayResult(BaseModel):
    operations: list[Operation]
    unfinished: list[str]


def working_windows(
    snapshot: FactorySnapshot, machine: Machine, overtime: list[OvertimeGrant]
) -> list[tuple[int, int]]:
    """機台可工作的時間區間;停機機台沒有任何區間。"""
    if machine.status == MachineStatus.DOWN:
        return []
    extra: dict[int, int] = defaultdict(int)
    for grant in overtime:
        if grant.machine_id == machine.id:
            extra[grant.day] += grant.minutes
    cal = snapshot.calendar
    windows: list[tuple[int, int]] = []
    for day in range(cal.horizon_days):
        start = day * MIN_PER_DAY + cal.day_start_min
        end = day * MIN_PER_DAY + cal.day_end_min + extra[day]
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def consume(windows: list[tuple[int, int]], earliest: int, work_min: int) -> tuple[int, int] | None:
    """自 earliest 起耗用 work_min 分鐘工作時間,回傳 (開始, 結束);排不完回傳 None。"""
    start = None
    left = work_min
    for win_start, win_end in windows:
        if win_end <= earliest:
            continue
        cursor = max(win_start, earliest)
        if start is None:
            start = cursor
        if win_end - cursor >= left:
            return start, cursor + left
        left -= win_end - cursor
    return None


def run_minutes(quantity: int, product: Product) -> int:
    return ceil(quantity * 60 / product.units_per_hour)


class _MachineState:
    def __init__(self, machine: Machine, windows: list[tuple[int, int]], queue: list[str]):
        self.windows = windows
        self.queue = list(queue)
        self.free_at = 0
        self.mold_id = machine.current_mold_id
        self.material_id = machine.current_material_id
        self.shade = machine.current_color_shade


def _setup_minutes(snapshot: FactorySnapshot, state: _MachineState, product: Product) -> int:
    times = snapshot.setup_times
    if state.mold_id != product.mold_id:
        return times.mold_change_min
    if state.material_id != product.material_id:
        return times.material_change_min
    if state.shade is None or product.color_shade > state.shade:
        return times.color_darker_min
    if product.color_shade < state.shade:
        return times.color_lighter_min
    return 0


def replay(snapshot: FactorySnapshot, plan: Plan) -> ReplayResult:
    products = by_id(snapshot.products)
    work_orders = by_id(snapshot.work_orders)
    states = {
        m.id: _MachineState(m, working_windows(snapshot, m, plan.overtime), plan.machine_sequences.get(m.id, []))
        for m in snapshot.machines
    }
    mold_free_at: dict[str, int] = defaultdict(int)
    # 產品已試模過的機台;試過一次,同一份排程後面的工單就不用再試
    proven = {p.id: set(p.proven_machines) for p in snapshot.products if p.proven_machines is not None}
    mold_holder = {m.current_mold_id: m.id for m in snapshot.machines if m.current_mold_id}
    operations: list[Operation] = []
    unfinished: list[str] = []

    while True:
        # 每一輪只確定「最早能開始」的那張工單,讓跨機台的模具等待關係保持時間順序
        best = None
        for machine_id in sorted(states):
            state = states[machine_id]
            if not state.queue:
                continue
            wo = work_orders[state.queue[0]]
            product = products[wo.product_id]
            setup = _setup_minutes(snapshot, state, product)
            trial = snapshot.setup_times.trial_run_min if machine_id not in proven.get(product.id, {machine_id}) else 0
            earliest = max(state.free_at, mold_free_at[product.mold_id])
            span = consume(state.windows, earliest, setup + trial + run_minutes(wo.remaining, product))
            if span is None:
                unfinished.extend(state.queue)
                state.queue.clear()
                continue
            if best is None or span[0] < best[0][0]:
                best = (span, machine_id, wo, product, setup, trial)
        if best is None:
            break

        (start, end), machine_id, wo, product, setup, trial = best
        if product.id in proven:
            proven[product.id].add(machine_id)
        state = states[machine_id]
        mold_changed = state.mold_id != product.mold_id
        if mold_changed and state.mold_id and mold_holder.get(state.mold_id) == machine_id:
            del mold_holder[state.mold_id]  # 換下來的模具已不在這台機上,別台再拿走它時不能把這台現在的模具清掉
        previous_holder = mold_holder.get(product.mold_id)
        if previous_holder and previous_holder != machine_id:
            states[previous_holder].mold_id = None
        mold_holder[product.mold_id] = machine_id
        mold_free_at[product.mold_id] = end
        state.queue.pop(0)
        state.free_at = end
        state.mold_id = product.mold_id
        state.material_id = product.material_id
        state.shade = product.color_shade
        operations.append(
            Operation(
                work_order_id=wo.id,
                machine_id=machine_id,
                start_min=start,
                setup_min=setup,
                trial_min=trial,
                end_min=end,
                mold_changed=mold_changed,
            )
        )

    return ReplayResult(operations=operations, unfinished=unfinished)
