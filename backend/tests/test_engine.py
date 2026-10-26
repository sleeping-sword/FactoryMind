"""以手算小情境校準試跑、硬規則與指標。

小工廠:兩台 100 噸機台,每天 08:00–18:00(480–1080 分),共兩天。
所有產品每分鐘生產 1 件,所以數量即加工分鐘數。
"""

from factorymind.evaluate import evaluate
from factorymind.events import add_rush_order, machine_down
from factorymind.models import (
    CostRates,
    Customer,
    CustomerTier,
    FactorySnapshot,
    Machine,
    Material,
    Mold,
    Product,
    SetupTimes,
    ShiftCalendar,
    WorkOrder,
    WorkOrderStatus,
)
from factorymind.plan import EvaluationStatus, OvertimeGrant, Plan
from factorymind.policy import HardLimits, Policy
from factorymind.replay import consume

POLICY = Policy(
    version="test",
    hard_limits=HardLimits(max_overtime_min_per_machine_day=120, overtime_budget_ntd=1500),
    ranking=[],
)


def product(id, mold, shade, material="ABS", outsource=None):
    return Product(
        id=id, mold_id=mold, material_id=material, color_shade=shade,
        units_per_hour=60, material_kg_per_unit=1, outsource_unit_cost_ntd=outsource,
    )


def wo(id, product_id, quantity, due_min=10_000, customer="N", **extra):
    return WorkOrder(id=id, customer_id=customer, product_id=product_id, quantity=quantity, due_min=due_min, **extra)


def factory(work_orders, abs_kg=10_000):
    return FactorySnapshot(
        factory_id="tiny",
        version=1,
        taken_at="D0 00:00",
        source="synthetic",
        machines=[
            Machine(id="M1", tonnage=100, current_mold_id="X", current_material_id="ABS", current_color_shade=1),
            Machine(id="M2", tonnage=100, current_mold_id="Y", current_material_id="ABS", current_color_shade=1),
        ],
        molds=[
            Mold(id="X", min_tonnage=50, max_tonnage=150, fits_machines=["M1", "M2"]),
            Mold(id="Y", min_tonnage=50, max_tonnage=150, fits_machines=["M1", "M2"]),
            Mold(id="Z", min_tonnage=200, max_tonnage=300, fits_machines=[]),
        ],
        materials=[Material(id="ABS", on_hand_kg=abs_kg), Material(id="PP", on_hand_kg=10_000)],
        products=[
            product("PX", "X", 1, outsource=5),
            product("PX-DARK", "X", 5),
            product("PX-PP", "X", 1, material="PP"),
            product("PY", "Y", 1),
            product("PZ", "Z", 1),
        ],
        customers=[
            Customer(id="S", name="策略", tier=CustomerTier.STRATEGIC),
            Customer(id="N", name="一般", tier=CustomerTier.NORMAL),
        ],
        work_orders=work_orders,
        calendar=ShiftCalendar(day_start_min=480, day_end_min=1080, horizon_days=2),
        setup_times=SetupTimes(mold_change_min=120, material_change_min=45, color_darker_min=15, color_lighter_min=40),
        cost_rates=CostRates(overtime_ntd_per_min=10, setup_ntd_per_min=1),
        outsource_lead_time_min=1500,
    )


def plan(m1=(), m2=(), **extra):
    return Plan(id="P", label="test", machine_sequences={"M1": list(m1), "M2": list(m2)}, **extra)


def spans(evaluation):
    return {op.work_order_id: (op.machine_id, op.start_min, op.setup_min, op.end_min) for op in evaluation.operations}


def codes(evaluation):
    return {v.code for v in evaluation.violations}


def test_consume_pauses_overnight():
    windows = [(480, 1080), (1920, 2520)]
    assert consume(windows, 1000, 200) == (1000, 2040)
    assert consume(windows, 1100, 10) == (1920, 1930)
    assert consume(windows, 480, 1201) is None


def test_setup_time_depends_on_previous_work_order():
    snap = factory([wo("W1", "PX", 100), wo("W2", "PX-DARK", 60), wo("W3", "PX", 30), wo("W4", "PX-PP", 20), wo("W5", "PY", 10)])
    ev = evaluate(snap, plan(m1=["W1", "W2", "W3", "W4", "W5"]), POLICY)
    assert spans(ev) == {
        "W1": ("M1", 480, 0, 580),  # 同模同色
        "W2": ("M1", 580, 15, 655),  # 淺到深
        "W3": ("M1", 655, 40, 725),  # 深到淺
        "W4": ("M1", 725, 45, 790),  # 換料
        "W5": ("M1", 790, 120, 920),  # 換模
    }
    assert ev.kpis.setup_min == 220
    assert ev.kpis.mold_changes == 1
    assert ev.kpis.cost_ntd == 220
    assert ev.kpis.machine_load == {"M1": round(440 / 1200, 3), "M2": 0}


def test_one_mold_cannot_run_on_two_machines_at_once():
    snap = factory([wo("W1", "PX", 100), wo("W2", "PX", 50)])
    ev = evaluate(snap, plan(m1=["W1"], m2=["W2"]), POLICY)
    assert spans(ev)["W2"] == ("M2", 580, 120, 750)


def test_tardiness_counts_only_lateness_and_splits_strategic():
    snap = factory([wo("W1", "PX", 100, due_min=500, customer="S"), wo("W2", "PX", 100, due_min=5000), wo("W3", "PY", 100, due_min=560)])
    ev = evaluate(snap, plan(m1=["W1", "W2"], m2=["W3"]), POLICY)
    assert ev.eligible
    assert ev.kpis.strategic_tardiness_min == 80
    assert ev.kpis.total_tardiness_min == 100
    assert ev.kpis.late_work_orders == ["W1", "W3"]


def test_incompatible_mold_is_blocked():
    snap = factory([wo("W1", "PZ", 10)])
    ev = evaluate(snap, plan(m1=["W1"]), POLICY)
    assert codes(ev) == {"MOLD_MACHINE_INCOMPATIBLE"}
    assert not ev.eligible


def test_material_shortage_is_blocked():
    snap = factory([wo("W1", "PX", 100), wo("W2", "PY", 100)], abs_kg=150)
    assert codes(evaluate(snap, plan(m1=["W1"], m2=["W2"]), POLICY)) == {"MATERIAL_SHORTAGE"}


def test_delay_limit_is_a_hard_rule():
    snap = factory([wo("W1", "PX", 100, due_min=500, max_delay_min=60)])
    assert codes(evaluate(snap, plan(m1=["W1"]), POLICY)) == {"DELAY_LIMIT_EXCEEDED"}


def test_overtime_limits():
    snap = factory([wo("W1", "PX", 700)])
    within = evaluate(snap, plan(m1=["W1"], overtime=[OvertimeGrant(machine_id="M1", day=0, minutes=100)]), POLICY)
    assert within.eligible
    assert spans(within)["W1"] == ("M1", 480, 0, 1180)
    assert within.kpis.cost_breakdown_ntd["overtime"] == 1000

    too_long = evaluate(snap, plan(m1=["W1"], overtime=[OvertimeGrant(machine_id="M1", day=0, minutes=130)]), POLICY)
    assert codes(too_long) == {"OVERTIME_DAILY_LIMIT"}

    grants = [OvertimeGrant(machine_id=m, day=0, minutes=100) for m in ("M1", "M2")]
    assert codes(evaluate(snap, plan(m1=["W1"], overtime=grants), POLICY)) == {"OVERTIME_BUDGET"}


def test_outsourcing_cost_and_permission():
    snap = factory([wo("W1", "PX", 100, due_min=1400), wo("W2", "PY", 100)])
    ev = evaluate(snap, plan(outsourced=["W1"], m2=["W2"]), POLICY)
    assert ev.eligible
    assert ev.kpis.cost_breakdown_ntd["outsourcing"] == 500
    assert ev.kpis.total_tardiness_min == 100
    assert codes(evaluate(snap, plan(m1=["W1"], outsourced=["W2"]), POLICY)) == {"OUTSOURCE_NOT_ALLOWED"}


def test_invalid_plan_reports_no_numbers():
    snap = factory([wo("W1", "PX", 100)])
    ev = evaluate(snap, plan(m1=["W1", "GHOST"], m2=["W1"]), POLICY)
    assert ev.status == EvaluationStatus.INVALID_PLAN
    assert len(ev.errors) == 2
    assert ev.feasible is None and ev.violations is None and ev.kpis is None
    assert not ev.eligible


def test_unplaced_or_unfinished_work_is_infeasible_without_kpis():
    snap = factory([wo("W1", "PX", 100), wo("W2", "PY", 1300)])
    missing = evaluate(snap, plan(m1=["W1"]), POLICY)
    assert missing.feasible is False and missing.kpis is None
    too_long = evaluate(snap, plan(m1=["W1"], m2=["W2"]), POLICY)
    assert too_long.feasible is False and too_long.kpis is None
    assert too_long.infeasible_reasons == ["工單 W2 無法在排程範圍內完成"]


def test_machine_down_interrupts_started_work_and_keeps_original_snapshot():
    started = wo("W1", "PX", 100, status=WorkOrderStatus.IN_PROGRESS, quantity_done=40, machine_id="M1")
    before = factory([started])
    after = machine_down(before, "M1")
    assert (before.version, after.version) == (1, 2)
    assert before.work_orders[0].status == WorkOrderStatus.IN_PROGRESS
    assert after.work_orders[0].status == WorkOrderStatus.NOT_STARTED

    stale = evaluate(after, plan(m1=["W1"]), POLICY)
    assert codes(stale) == {"MACHINE_UNAVAILABLE"}
    assert stale.feasible is False

    moved = evaluate(after, plan(m2=["W1"]), POLICY)
    assert moved.eligible
    assert spans(moved)["W1"] == ("M2", 480, 120, 660)  # 換模 120 + 剩餘 60 件


def test_started_work_order_cannot_be_moved():
    started = wo("W1", "PX", 100, status=WorkOrderStatus.IN_PROGRESS, quantity_done=40, machine_id="M1")
    snap = factory([started, wo("W2", "PX", 10)])
    assert evaluate(snap, plan(m1=["W1", "W2"]), POLICY).eligible
    assert codes(evaluate(snap, plan(m1=["W2", "W1"]), POLICY)) == {"STARTED_WORK_ORDER_MOVED"}
    assert codes(evaluate(snap, plan(m1=["W2"], m2=["W1"]), POLICY)) == {"STARTED_WORK_ORDER_MOVED"}


def test_changed_count_ignores_new_rush_order():
    before = factory([wo("W1", "PX", 100), wo("W2", "PY", 100)])
    original = evaluate(before, plan(m1=["W1"], m2=["W2"]), POLICY)
    assert original.kpis.changed_work_orders is None

    after = add_rush_order(before, wo("R1", "PX", 50, is_rush=True))
    appended = evaluate(after, plan(m1=["W1", "R1"], m2=["W2"]), POLICY, reference=original)
    assert appended.kpis.changed_work_orders == 0
    inserted = evaluate(after, plan(m1=["R1", "W1"], m2=["W2"]), POLICY, reference=original)
    assert inserted.kpis.changed_work_orders == 1


def test_machine_load_excludes_overnight_pause():
    # 700 分鐘的工單第一天做 600 分、隔天再做 100 分,中間停工不算負荷
    ev = evaluate(factory([wo("W1", "PX", 700)]), plan(m1=["W1"]), POLICY)
    assert spans(ev)["W1"] == ("M1", 480, 0, 2020)
    assert ev.kpis.machine_load["M1"] == round(700 / 1200, 3)
