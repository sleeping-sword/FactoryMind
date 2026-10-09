"""示範:IM-02 停機同時遇到策略客戶急單。執行方式:python -m factorymind.demo"""

from .dispatch import dispatch
from .evaluate import evaluate
from .events import add_rush_order, machine_down
from .models import MIN_PER_DAY
from .optimize import optimize
from .plan import Evaluation, OvertimeGrant
from .ranking import rank
from .scenarios.demo import demo_policy, demo_rush_order, demo_snapshot


def clock(minute: int) -> str:
    day, rest = divmod(minute, MIN_PER_DAY)
    return f"D{day} {rest // 60:02d}:{rest % 60:02d}"


def print_schedule(ev: Evaluation) -> None:
    by_machine: dict[str, list] = {}
    for op in ev.operations:
        by_machine.setdefault(op.machine_id, []).append(op)
    for machine_id in sorted(by_machine):
        row = "  ".join(
            f"{op.work_order_id}[{clock(op.start_min)}→{clock(op.end_min)}]" for op in by_machine[machine_id]
        )
        print(f"  {machine_id}: {row}")
    if ev.outsourced:
        print(f"  外包: {', '.join(ev.outsourced)}")


def print_summary(ev: Evaluation, label: str) -> None:
    print(f"\n[{ev.plan_id}] {label}")
    if ev.errors:
        print("  計算失敗:", ";".join(ev.errors))
        return
    print(f"  可行: {'是' if ev.feasible else '否'}  硬規則: {'通過' if not ev.violations else '違反'}")
    for reason in ev.infeasible_reasons:
        print("  ✗", reason)
    for violation in ev.violations or []:
        print(f"  ✗ {violation.code}: {violation.message}")
    if ev.kpis:
        k = ev.kpis
        print(
            f"  策略客戶延誤 {k.strategic_tardiness_min} 分 | 總延誤 {k.total_tardiness_min} 分"
            f" | 逾期 {len(k.late_work_orders)} 張 | 改動工單 {k.changed_work_orders}"
            f" | 換模 {k.mold_changes} 次 | 成本 NT${k.cost_ntd:,.0f}"
        )


def main() -> None:
    policy = demo_policy()
    before = demo_snapshot()
    original = evaluate(before, dispatch(before, plan_id="ORIGINAL", label="原始排程"), policy)
    print(f"== 原始工廠(快照 v{before.version},政策 {policy.version})")
    print_summary(original, "原始排程")
    print_schedule(original)

    after = add_rush_order(machine_down(before, "IM-02"), demo_rush_order())
    print(f"\n== 事件:IM-02 停機(復機時間未知)+ 急單 WO-R1 → 快照 v{after.version}")

    two_days = [OvertimeGrant(machine_id=m, day=d, minutes=240) for m in ("IM-03", "IM-05") for d in (0, 1)]
    plans = [
        dispatch(after, plan_id="P1", label="轉相容機台,依交期重排", rule="edd"),
        dispatch(after, plan_id="P2", label="轉相容機台,策略客戶優先", rule="priority"),
        dispatch(after, plan_id="P3", label="策略客戶優先 + IM-03、IM-05 加班兩天", rule="priority", overtime=two_days),
        dispatch(after, plan_id="P4", label="策略客戶優先 + IM-05 當天加班", rule="priority",
                 overtime=[OvertimeGrant(machine_id="IM-05", day=0, minutes=240)]),
        dispatch(after, plan_id="P5", label="依交期重排 + 急單外包", rule="edd", outsourced=["WO-R1"]),
    ]
    search = optimize(after, policy, plan_id="P6", label="局部搜尋重排(不加班、不外包)", reference=original)
    plans.append(search.plan)
    evaluations = [evaluate(after, plan, policy, reference=original) for plan in plans]
    for plan, ev in zip(plans, evaluations):
        print_summary(ev, plan.label)

    print(
        f"  (P6 自 {search.start_rule} 法則出發,改進 {search.moves} 步,試算 {search.evaluations} 個方案,"
        f"{'已達局部最佳' if search.local_optimum else '試算次數用完'})"
    )

    result = rank(evaluations, policy.ranking)
    print("\n== 排序")
    for e in result.eliminated:
        detail = f"(值 {e.value:g},最佳 {e.best:g},門檻 {e.threshold:g})" if e.value is not None else ""
        print(f"  淘汰 {e.plan_id} @ {e.stage}: {e.reason}{detail}")
    if result.tie_break:
        print(" ", result.tie_break)
    if result.winner:
        print(f"  推薦方案: {result.winner}")
        print_schedule(next(ev for ev in evaluations if ev.plan_id == result.winner))
    else:
        print(f"  未選出方案: {result.undecided_reason}")


if __name__ == "__main__":
    main()
