"""R05:示範情境用到的射出製程限制。參數都是合成假設,出處見 factorymind/parameters.py。"""

from factorymind.dispatch import dispatch
from factorymind.evaluate import evaluate
from factorymind.models import by_id
from factorymind.plan import Plan
from factorymind.scenarios.demo import demo_policy, demo_snapshot


def _moved(snapshot, wo_id, machine_id):
    """原始排程,只把一張工單搬到指定機台最後面。"""
    plan = dispatch(snapshot, plan_id="S1", label="原始排程")
    sequences = {m: [w for w in seq if w != wo_id] for m, seq in plan.machine_sequences.items()}
    sequences.setdefault(machine_id, []).append(wo_id)
    return Plan(id="T", label="測試", machine_sequences=sequences)


def _codes(evaluation):
    return {v.code for v in evaluation.violations}


def test_mold_outside_its_fit_list_is_refused_even_within_tonnage():
    """R05-A01:MD-F 噸數範圍 200–300,IM-04 是 200 噸,但相容清單不含 IM-04。"""
    snapshot = demo_snapshot()
    evaluation = evaluate(snapshot, _moved(snapshot, "WO-06", "IM-04"), demo_policy())
    violation = next(v for v in evaluation.violations if v.code == "MOLD_NOT_ON_FIT_LIST")
    assert "MD-F" in violation.message and "IM-04" in violation.message
    assert "IM-04" not in {m for m, seq in dispatch(snapshot, plan_id="X", label="x").machine_sequences.items()
                           if any(by_id(snapshot.work_orders)[w].product_id == "F-BLK" for w in seq)}


def test_medical_part_on_a_machine_the_customer_has_not_approved_is_blocked():
    """R05-A02:醫療耗材盒只認可 IM-02/03/04;IM-05 噸數與模具都相容也不行。"""
    snapshot = demo_snapshot()
    evaluation = evaluate(snapshot, _moved(snapshot, "WO-07", "IM-05"), demo_policy())
    assert "MACHINE_NOT_APPROVED" in _codes(evaluation)
    assert not evaluation.eligible
    placed = dispatch(snapshot, plan_id="X", label="x", rule="priority").machine_sequences
    assert "WO-07" not in placed.get("IM-05", [])


def test_missing_fit_data_is_reported_as_insufficient_not_compliant():
    """R05-A03:模具沒有相容清單時不默認可行,指出要補什麼。"""
    snapshot = demo_snapshot()
    molds = [m.model_copy(update={"fits_machines": None}) if m.id == "MD-C" else m for m in snapshot.molds]
    snapshot = snapshot.model_copy(update={"molds": molds})
    evaluation = evaluate(snapshot, _moved(snapshot, "WO-07", "IM-03"), demo_policy())
    violation = next(v for v in evaluation.violations if v.code == "FIT_DATA_MISSING")
    assert "資料不足" in violation.message and "MD-C" in violation.message and "補" in violation.message
    assert not evaluation.eligible


def test_first_run_on_an_untried_machine_adds_trial_time_once():
    """R05-A04:C-MED 沒在 IM-03 試模過;第一張要加試模/首件時間,費用另列,不重複算進換模費。"""
    snapshot = demo_snapshot()
    trial = snapshot.setup_times.trial_run_min
    plan = _moved(snapshot, "WO-07", "IM-03")
    plan = Plan(id="T", label="測試", machine_sequences={
        m: [w for w in seq if w != "WO-16"] + (["WO-16"] if m == "IM-03" else []) for m, seq in plan.machine_sequences.items()
    })
    evaluation = evaluate(snapshot, plan, demo_policy())
    ops = {op.work_order_id: op for op in evaluation.operations}
    assert ops["WO-07"].trial_min == trial and ops["WO-16"].trial_min == 0  # 試過一次就不再試
    assert ops["WO-07"].end_min - ops["WO-07"].start_min >= ops["WO-07"].setup_min + trial
    rate = snapshot.cost_rates.setup_ntd_per_min
    costs = evaluation.kpis.cost_breakdown_ntd
    assert costs["trial"] == trial * rate
    assert costs["setup"] == sum(op.setup_min for op in evaluation.operations) * rate


def test_every_process_parameter_has_a_source_row():
    """參數出處表:名稱、對象、值、單位、來源、版本、適用範圍、假設標記、確認狀態。"""
    from factorymind.parameters import process_parameters

    rows = process_parameters(demo_snapshot())
    fields = {"name", "target", "value", "unit", "source", "version", "scope", "assumption", "status"}
    assert rows and all(fields <= row.keys() for row in rows)
    named = {(r["name"], r["target"]) for r in rows}
    assert ("相容機台清單", "MD-F") in named and ("客戶認可機台", "C-MED") in named and ("試模／首件時間", "全廠") in named
    assert all(r["status"] == "未經製程覆核" for r in rows)  # 沒有真人覆核就不能寫已確認
