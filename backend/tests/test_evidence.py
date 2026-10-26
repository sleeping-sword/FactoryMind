"""R01:AI 說明要能逐項對回計算證據(方案、指標、值、單位、版本)。"""

from factorymind.governance import VirtualFactory


def processed():
    factory = VirtualFactory(advisor=None)
    incident = factory.open_incident(factory.source.next(factory.snapshot))
    return factory, factory.process(incident.id)


def test_policy_loser_is_not_called_noncompliant_and_comparison_names_its_pool():
    """R01-A01:P6 合規但在第一順位落選;說明限定比較集合,不說成違規。"""
    _, incident = processed()
    p6 = next(e for e in incident.ranking.eliminated if e.plan_id == "P6")
    assert p6.pool == ["P1", "P2", "P3", "P4", "P6"]  # 該輪參與比較的方案

    reasons = incident.ai_brief.reasons
    p6_reason = next(r for r in reasons if r.startswith("P6"))
    assert "合規" in p6_reason and "不合規" not in p6_reason and "違反" not in p6_reason
    assert "P1、P2、P3、P4、P6" in p6_reason and "策略客戶延誤" in p6_reason
    p5_reason = next(r for r in reasons if r.startswith("P5"))
    assert "硬規則" in p5_reason


def test_every_plan_metric_is_evidence_with_versions_and_unit():
    """契約:事件、方案、快照/政策/引擎版本、指標鍵、值、單位、證據識別碼。"""
    from factorymind.evidence import build_evidence

    factory, incident = processed()
    evidence = build_evidence(incident)
    p3_cost = next(e for e in evidence if e.plan_id == "P3" and e.metric == "cost_ntd")
    assert (p3_cost.value, p3_cost.unit, p3_cost.display) == (19000, "NT$", "NT$19,000")
    assert p3_cost.incident_id == incident.id
    assert p3_cost.snapshot_version == incident.snapshot.version
    assert p3_cost.policy_version == factory.policy.version and p3_cost.engine_version
    assert len({e.id for e in evidence}) == len(evidence)
    p6_changed = next(e for e in evidence if e.plan_id == "P6" and e.metric == "changed_work_orders")
    assert (p6_changed.value, p6_changed.unit) == (8, "張")


def _find(evidence, plan_id, metric):
    return next(e for e in evidence if e.plan_id == plan_id and e.metric == metric)


def test_citation_of_another_plans_cost_is_rejected():
    """R01-A02:P6 的成本不能掛在 P3 名下;正確的同方案引用要通過。"""
    from factorymind.evidence import build_evidence, check_text

    _, incident = processed()
    evidence = build_evidence(incident)
    p3, p6 = _find(evidence, "P3", "cost_ntd"), _find(evidence, "P6", "cost_ntd")

    assert check_text(f"P3 額外成本 [{p3.id}]", evidence) == []
    assert check_text(f"P3 額外成本 [{p6.id}]", evidence)
    assert check_text("P3 額外成本 [E99]", evidence)  # 不存在的編號


def test_marker_described_as_another_metric_is_rejected():
    """R01-A03:數字與單位由系統依證據填入;句子把金額證據講成延誤要拒絕,講對的要通過。"""
    from factorymind.evidence import build_evidence, check_text

    _, incident = processed()
    evidence = build_evidence(incident)
    p2_total = _find(evidence, "P2", "total_tardiness_min")
    p3_strategic, p3_total = _find(evidence, "P3", "strategic_tardiness_min"), _find(evidence, "P3", "total_tardiness_min")
    assert check_text(f"P3 總延誤 [{_find(evidence, 'P3', 'cost_ntd').id}]", evidence)
    # 指標寫在標記後面也要核對:P2 的總延誤被說成策略客戶延誤
    assert check_text(f"P2 [{p2_total.id}] 的策略客戶延誤較高", evidence)
    assert check_text(f"P2 總延誤 [{p2_total.id}] 較高", evidence) == []
    assert check_text(f"P3 策略客戶延誤 [{p3_strategic.id}]、總延誤 [{p3_total.id}] 都最低", evidence) == []
    # 簡稱也認得;後面接的是下一個標記的指標,不能算到這個標記頭上
    p3_changed, p3_cost = _find(evidence, "P3", "changed_work_orders"), _find(evidence, "P3", "cost_ntd")
    assert check_text(f"P3 改動工單 [{p3_changed.id}] 與額外成本 [{p3_cost.id}] 最低", evidence) == []
    assert check_text(f"P3 成本 [{p3_changed.id}]", evidence)
    # 旁邊沒講指標的(「P3 要多花 [E12]」)不擋
    assert check_text(f"P3 要多花 [{_find(evidence, 'P3', 'cost_ntd').id}]", evidence) == []


def test_metric_numbers_written_without_marker_are_rejected():
    """不能繞過標記直接寫數字:P6 的 59,900 直接寫在 P3 後面也要被擋。"""
    from factorymind.evidence import build_evidence, check_text

    _, incident = processed()
    evidence = build_evidence(incident)
    assert check_text("P3 額外成本 NT$59,900", evidence)
    assert check_text("P3 改動 8 張工單", evidence)
    # 工單、機台編號與交期時間不是指標數字
    assert check_text("急單 WO-R1 交期 D1 20:00,IM-02 停機,改排 WO-12", evidence) == []


def _model_brief(text):
    import json

    return json.dumps({"headline": "建議核准 P3", "reasons": [text], "watch": []})


def _evidence_of_demo():
    from factorymind.evidence import build_evidence

    return build_evidence(processed()[1])


def test_model_brief_with_misattributed_cost_falls_back_to_rules():
    from tests.test_ai import fake_model, processed as processed_with

    evidence = _evidence_of_demo()
    p6 = _find(evidence, "P6", "cost_ntd")
    wrong = _model_brief(f"P3 額外成本 [{p6.id}]")
    advisor, seen = fake_model(wrong, wrong, wrong)
    _, incident = processed_with(advisor)
    assert len(seen) == 3 and p6.id in seen[1]["messages"][-1]["content"]  # 帶著原因要求重寫
    assert incident.ai_brief.source == "rules"
    assert incident.ai_brief.evidence  # 規則摘要也附上證據,可以點開核對


def test_model_brief_keeps_cited_evidence_with_versions():
    from tests.test_ai import fake_model, processed as processed_with

    evidence = _evidence_of_demo()
    p3 = _find(evidence, "P3", "cost_ntd")
    advisor, seen = fake_model(_model_brief(f"P3 額外成本只有 [{p3.id}]"))
    _, incident = processed_with(advisor)
    brief = incident.ai_brief
    assert brief.source == "ai" and brief.reasons == [f"P3 額外成本只有 [{p3.id}]"]
    assert [(e.id, e.plan_id, e.display) for e in brief.evidence] == [(p3.id, "P3", "NT$19,000")]
    assert brief.evidence[0].snapshot_version == incident.snapshot.version
    assert f"[{p3.id}] P3 額外成本" in seen[0]["messages"][1]["content"]  # 模型看得到證據表


def test_cause_question_without_cause_data_answers_unknown_without_model():
    """R01-A04:沒有故障原因資料,問原因就回答未知/待維修確認,不交給模型猜。"""
    from tests.test_ai import fake_model, processed as processed_with

    advisor, seen = fake_model(_model_brief("建議 P3"))
    factory, incident = processed_with(advisor)
    turn = factory.ask(incident.id, "IM-02 停機的確切原因是什麼?", "王主管")
    assert len(seen) == 1  # 只有摘要呼叫過模型
    assert turn.source == "rules" and "未知" in turn.answer and "維修確認" in turn.answer


def test_model_answer_that_invents_a_cause_is_rejected():
    import json
    from tests.test_ai import fake_model, processed as processed_with

    made_up = json.dumps({"answer": "IM-02 是因為加熱圈老化才停機,建議採用 P3。"})
    advisor, _ = fake_model(_model_brief("建議 P3"), made_up, made_up, made_up)
    factory, incident = processed_with(advisor)
    turn = factory.ask(incident.id, "P3 安全嗎?", "王主管")
    assert turn.source == "rules" and "原因" in turn.answer
