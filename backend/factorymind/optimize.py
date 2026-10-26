"""局部搜尋排程器:從派工法則的結果出發,反覆搬移或對調工單,直到鄰近方案都不再更好。

每個鄰近方案都交給同一套試跑與硬規則評估,所以搜尋看到的數字與最後回報的數字一致。
結果是局部最佳,不保證全域最佳。加班與外包是給定的決策,搜尋只調整機台指派與順序。
"""

from itertools import combinations
from typing import Iterator

from pydantic import BaseModel

from .dispatch import Rule, dispatch
from .evaluate import evaluate
from .models import FactorySnapshot, MachineStatus, WorkOrderStatus, by_id, can_run
from .plan import Evaluation, OvertimeGrant, Plan
from .policy import Policy

START_RULES: tuple[Rule, ...] = ("edd", "priority", "fifo")

Sequences = dict[str, list[str]]


class SearchResult(BaseModel):
    plan: Plan
    start_rule: Rule
    moves: int
    evaluations: int
    local_optimum: bool
    """False 表示評估次數用完就停,還可能有更好的鄰近方案。"""


def _key(ev: Evaluation, metrics: list[str]) -> tuple:
    """越小越好:先求可行,再求不違反硬規則,之後依企業順位逐項比較(不套用門檻)。"""
    if not ev.feasible:
        return (len(ev.infeasible_reasons), len(ev.violations), *[0] * (len(metrics) + 2))
    k = ev.kpis
    return (0, len(ev.violations), *[getattr(k, m) for m in metrics], k.total_tardiness_min, k.cost_ntd)


def _neighbors(
    sequences: Sequences, movable: list[str], allowed: dict[str, list[str]], pinned: dict[str, int]
) -> Iterator[Sequences]:
    where = {wo_id: (m, i) for m, seq in sequences.items() for i, wo_id in enumerate(seq)}
    # 搬移:把一張工單(含尚未排入的)插到任一相容機台的任一位置
    for wo_id in movable:
        base = {m: [w for w in seq if w != wo_id] for m, seq in sequences.items()}
        for m in allowed[wo_id]:
            for pos in range(pinned[m], len(base[m]) + 1):
                if where.get(wo_id) != (m, pos):
                    yield {**base, m: base[m][:pos] + [wo_id] + base[m][pos:]}
    # 對調:兩張工單互換位置,相鄰對調已含在搬移內
    for a, b in combinations([w for w in movable if w in where], 2):
        (ma, ia), (mb, ib) = where[a], where[b]
        if ma not in allowed[b] or mb not in allowed[a] or (ma == mb and abs(ia - ib) == 1):
            continue
        swapped = {m: list(seq) for m, seq in sequences.items()}
        swapped[ma][ia], swapped[mb][ib] = b, a
        yield swapped


def optimize(
    snapshot: FactorySnapshot,
    policy: Policy,
    *,
    plan_id: str,
    label: str,
    reference: Evaluation | None = None,
    overtime: list[OvertimeGrant] | None = None,
    outsourced: list[str] | None = None,
    assumptions: list[str] | None = None,
    max_evaluations: int = 20_000,
) -> SearchResult:
    """確定性搜尋:同一份輸入必得到相同方案。沒有對照排程時略過改動工單數這個順位。"""
    overtime = overtime or []
    outsourced = outsourced or []
    metrics = [c.metric for c in policy.ranking if reference is not None or c.metric != "changed_work_orders"]

    def build(sequences: Sequences) -> Plan:
        return Plan(
            id=plan_id,
            label=label,
            machine_sequences=sequences,
            overtime=overtime,
            outsourced=outsourced,
            assumptions=assumptions or [],
        )

    def score(sequences: Sequences) -> tuple:
        return _key(evaluate(snapshot, build(sequences), policy, reference), metrics)

    starts = []
    for rule in START_RULES:
        seqs = dispatch(snapshot, plan_id=plan_id, label=label, rule=rule, overtime=overtime, outsourced=outsourced).machine_sequences
        starts.append((score(seqs), rule, seqs))
    best_key, start_rule, current = min(starts, key=lambda s: s[0])
    used = len(starts)

    molds = by_id(snapshot.molds)
    products = by_id(snapshot.products)
    work_orders = by_id(snapshot.work_orders)
    movable = [
        w.id for w in snapshot.open_work_orders() if w.status != WorkOrderStatus.IN_PROGRESS and w.id not in outsourced
    ]
    allowed = {
        wo_id: [
            m.id
            for m in snapshot.machines
            if m.status == MachineStatus.UP
            and can_run(products[work_orders[wo_id].product_id], molds[products[work_orders[wo_id].product_id].mold_id], m)
        ]
        for wo_id in movable
    }
    # 已開工的工單固定在機台最前面
    pinned = {
        m: int(bool(seq) and work_orders[seq[0]].status == WorkOrderStatus.IN_PROGRESS) for m, seq in current.items()
    }

    moves = 0
    exhausted = False
    while not exhausted:
        step = None
        for candidate in _neighbors(current, movable, allowed, pinned):
            if used >= max_evaluations:
                exhausted = True
                break
            key = score(candidate)
            used += 1
            if key < (step[0] if step else best_key):
                step = (key, candidate)
        if step is None:
            break
        best_key, current = step
        moves += 1

    return SearchResult(
        plan=build(current), start_rule=start_rule, moves=moves, evaluations=used, local_optimum=not exhausted
    )
