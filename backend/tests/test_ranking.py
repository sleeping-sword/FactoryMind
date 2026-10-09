from factorymind.dispatch import dispatch
from factorymind.evaluate import evaluate
from factorymind.events import add_rush_order, machine_down
from factorymind.plan import Evaluation, EvaluationStatus, Kpis, OvertimeGrant, Violation
from factorymind.policy import RankingCriterion
from factorymind.ranking import rank
from factorymind.scenarios.demo import demo_policy, demo_rush_order, demo_snapshot


def ev(plan_id, *, total=0, strategic=0, changed=0, cost=0.0, violations=(), feasible=True):
    return Evaluation(
        plan_id=plan_id, factory_id="f", snapshot_version=1, policy_version="p", engine_version="e",
        status=EvaluationStatus.OK, feasible=feasible, violations=list(violations),
        kpis=Kpis(
            strategic_tardiness_min=strategic, total_tardiness_min=total, late_work_orders=[],
            changed_work_orders=changed, cost_ntd=cost, cost_breakdown_ntd={}, mold_changes=0, setup_min=0,
            machine_load={},
        ),
    )


TOTAL_THEN_COST = [RankingCriterion(metric="total_tardiness_min", threshold=60), RankingCriterion(metric="cost_ntd")]


def test_threshold_compares_against_best_not_neighbours():
    # 0→50→100 相鄰差距都在門檻內,但 100 與最佳值差 100,必須淘汰
    result = rank([ev("A", total=0, cost=300), ev("B", total=50, cost=100), ev("C", total=100, cost=1)], TOTAL_THEN_COST)
    assert result.winner == "B"
    assert [(e.plan_id, e.stage) for e in result.eliminated] == [("C", "total_tardiness_min"), ("A", "cost_ntd")]


def test_hard_rule_violation_never_wins_even_with_best_numbers():
    blocked = ev("A", total=0, cost=0, violations=[Violation(code="OVERTIME_BUDGET", message="超過預算")])
    result = rank([blocked, ev("B", total=500, cost=900)], TOTAL_THEN_COST)
    assert result.winner == "B"
    assert result.eliminated[0].stage == "硬規則"


def test_no_winner_when_every_plan_is_blocked():
    blocked = ev("A", violations=[Violation(code="X", message="違規")])
    result = rank([blocked, ev("B", feasible=False)], TOTAL_THEN_COST)
    assert result.winner is None
    assert result.undecided_reason == "沒有合規且可行的方案"


def test_missing_metric_is_not_guessed():
    result = rank([ev("A", changed=None), ev("B")], [RankingCriterion(metric="changed_work_orders")])
    assert result.winner is None
    assert "changed_work_orders" in result.undecided_reason


def test_full_tie_uses_fixed_candidate_order():
    result = rank([ev("A"), ev("B")], TOTAL_THEN_COST)
    assert result.winner == "A"
    assert result.tie_break is not None


def test_demo_scenario_end_to_end():
    policy = demo_policy()
    before = demo_snapshot()
    original = evaluate(before, dispatch(before, plan_id="ORIGINAL", label="原始排程"), policy)
    assert original.eligible and original.kpis.total_tardiness_min == 0

    after = add_rush_order(machine_down(before, "IM-02"), demo_rush_order())
    overtime = [OvertimeGrant(machine_id=m, day=d, minutes=240) for m in ("IM-03", "IM-05") for d in (0, 1)]
    plans = [
        dispatch(after, plan_id="P1", label="依交期重排", rule="edd"),
        dispatch(after, plan_id="P3", label="加班兩天", rule="priority", overtime=overtime),
    ]
    evaluations = [evaluate(after, p, policy, reference=original) for p in plans]
    assert all(not seq for p in plans for seq in [p.machine_sequences["IM-02"]])
    assert [v.code for v in evaluations[1].violations] == ["OVERTIME_BUDGET"]
    assert rank(evaluations, policy.ranking).winner == "P1"
    # 同一份快照與方案重跑,結果必須完全相同
    assert evaluate(after, plans[0], policy, reference=original) == evaluations[0]
