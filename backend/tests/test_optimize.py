from factorymind.dispatch import dispatch
from factorymind.evaluate import evaluate
from factorymind.events import add_rush_order, machine_down
from factorymind.models import WorkOrderStatus
from factorymind.optimize import START_RULES, _key, optimize
from factorymind.policy import HardLimits, Policy, RankingCriterion
from factorymind.scenarios.demo import demo_policy, demo_rush_order, demo_snapshot
from test_engine import factory, wo

TARDINESS_THEN_COST = Policy(
    version="test",
    hard_limits=HardLimits(max_overtime_min_per_machine_day=120, overtime_budget_ntd=1500),
    ranking=[RankingCriterion(metric="total_tardiness_min"), RankingCriterion(metric="cost_ntd")],
)


def disrupted():
    policy = demo_policy()
    before = demo_snapshot()
    original = evaluate(before, dispatch(before, plan_id="ORIGINAL", label="原始排程"), policy)
    return add_rush_order(machine_down(before, "IM-02"), demo_rush_order()), policy, original


def test_search_reorders_colors_to_save_setups():
    # 依交期會排成深→淺→深(換色 15+40+15 分);淺色先做只需換色一次
    snap = factory([wo("W1", "PX-DARK", 100, due_min=1000), wo("W2", "PX", 100, due_min=2000), wo("W3", "PX-DARK", 100, due_min=2500)])
    baseline = evaluate(snap, dispatch(snap, plan_id="D", label="d"), TARDINESS_THEN_COST)
    result = optimize(snap, TARDINESS_THEN_COST, plan_id="S", label="s")
    ev = evaluate(snap, result.plan, TARDINESS_THEN_COST)
    assert baseline.kpis.setup_min == 70
    assert ev.eligible
    assert result.plan.machine_sequences == {"M1": ["W2", "W1", "W3"], "M2": []}
    assert (ev.kpis.total_tardiness_min, ev.kpis.setup_min) == (0, 15)
    assert result.local_optimum


def test_search_is_never_worse_than_dispatch_rules():
    snap, policy, original = disrupted()
    metrics = [c.metric for c in policy.ranking]
    result = optimize(snap, policy, plan_id="S", label="s", reference=original)
    found = evaluate(snap, result.plan, policy, original)
    assert found.eligible
    for rule in START_RULES:
        baseline = evaluate(snap, dispatch(snap, plan_id="D", label="d", rule=rule), policy, original)
        assert _key(found, metrics) <= _key(baseline, metrics)
    assert found.kpis.cost_ntd < 30_000


def test_search_is_deterministic():
    snap, policy, original = disrupted()
    first = optimize(snap, policy, plan_id="S", label="s", reference=original)
    assert first == optimize(snap, policy, plan_id="S", label="s", reference=original)


def test_started_work_order_stays_first_on_its_machine():
    snap = demo_snapshot()
    result = optimize(snap, demo_policy(), plan_id="S", label="s")
    for w in snap.work_orders:
        if w.status == WorkOrderStatus.IN_PROGRESS:
            assert result.plan.machine_sequences[w.machine_id][0] == w.id
    assert evaluate(snap, result.plan, demo_policy()).eligible


def test_budget_stops_search_and_reports_it():
    snap, policy, original = disrupted()
    result = optimize(snap, policy, plan_id="S", label="s", reference=original, max_evaluations=50)
    assert result.evaluations == 50
    assert not result.local_optimum
    assert evaluate(snap, result.plan, policy, original).eligible


def test_outsourced_work_orders_are_left_alone():
    snap, policy, original = disrupted()
    result = optimize(snap, policy, plan_id="S", label="s", reference=original, outsourced=["WO-R1"], max_evaluations=300)
    assert result.plan.outsourced == ["WO-R1"]
    assert all("WO-R1" not in seq for seq in result.plan.machine_sequences.values())
