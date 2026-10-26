import pytest

from factorymind.events import FactoryEvent
from factorymind.governance import MAX_REASON_CHARS, EventSource, GovernanceError, Stage, Status, VirtualFactory
from factorymind.scenarios.demo import demo_rush_order, demo_snapshot


def main_scenario(factory: VirtualFactory):
    incident = factory.open_incident(factory.source.next(factory.snapshot))
    return factory.process(incident.id)


def rush_only(order_id="WO-X1"):
    order = demo_rush_order().model_copy(update={"id": order_id, "quantity": 500})
    return [FactoryEvent(type="rush_order", subject=order.id, work_order=order)]


def test_incident_stops_at_approval_without_touching_the_factory():
    factory = VirtualFactory()
    before = factory.evaluation
    incident = main_scenario(factory)
    assert incident.title == "射出機 IM-02 停機、急單 WO-R1"
    assert incident.status == Status.AWAITING_APPROVAL
    assert incident.assessment.affected_work_orders == ["WO-01", "WO-07", "WO-16", "WO-R1"]
    assert incident.assessment.if_unhandled  # 原排程用到停機機台、急單未排入
    assert incident.ranking.winner == "P3"
    assert any(not ev.eligible for ev in incident.evaluations)  # 至少一個方案被硬規則擋下
    assert [t.stage for t in incident.trace] == [Stage.DETECT, Stage.ASSESS, Stage.PROPOSE, Stage.SIMULATE, Stage.DECIDE]
    assert factory.evaluation is before and factory.schedule_version == 1
    assert not factory.state()["schedule"]["still_valid"]


def test_approval_applies_plan_and_waits_for_repair_before_closing():
    factory = VirtualFactory()
    incident = main_scenario(factory)
    factory.approve(incident.id, "P3", "王主管")
    assert incident.status == Status.APPLIED_PENDING_TASKS
    assert factory.plan.id == "P3" and factory.schedule_version == 2
    assert factory.state()["schedule"]["still_valid"]
    assert incident.decisions[-1].followed_recommendation is True
    assert incident.decisions[-1].actor == "王主管"

    factory.confirm_task(incident.id, "T1", "維修確認者")
    assert incident.status == Status.CLOSED
    assert {m.id: m.status.value for m in factory.snapshot.machines}["IM-02"] == "up"
    with pytest.raises(GovernanceError):
        factory.confirm_task(incident.id, "T1", "維修確認者")


def test_plan_blocked_by_hard_rules_cannot_be_approved():
    factory = VirtualFactory()
    incident = main_scenario(factory)
    blocked = next(ev.plan_id for ev in incident.evaluations if not ev.eligible)
    with pytest.raises(GovernanceError, match="不能核准"):
        factory.approve(incident.id, blocked, "王主管")
    assert factory.schedule_version == 1


def test_rejection_is_recorded_and_another_plan_can_still_be_chosen():
    factory = VirtualFactory()
    incident = main_scenario(factory)
    factory.reject(incident.id, "王主管", "不想動這麼多工單")
    assert incident.status == Status.REJECTED
    assert factory.schedule_version == 1
    other = next(ev.plan_id for ev in incident.evaluations if ev.eligible and ev.plan_id != "P3")
    factory.approve(incident.id, other, "王主管")
    assert [d.kind for d in incident.decisions] == ["rejected", "approved"]
    assert incident.decisions[-1].followed_recommendation is False
    assert factory.plan.id == other


def test_factory_change_makes_pending_plans_stale_until_reassessed():
    factory = VirtualFactory()
    first = main_scenario(factory)
    factory.approve(first.id, "P3", "王主管")
    second = factory.process(factory.open_incident(rush_only()).id)
    assert second.status == Status.AWAITING_APPROVAL

    factory.confirm_task(first.id, "T1", "維修確認者")  # 復機改變了工廠狀態
    assert second.status == Status.STALE
    with pytest.raises(GovernanceError):
        factory.approve(second.id, second.ranking.winner, "王主管")

    factory.reassess(second.id, "王主管")
    factory.process(second.id)
    assert second.status == Status.AWAITING_APPROVAL
    assert second.snapshot.version == factory.snapshot.version
    factory.approve(second.id, second.ranking.winner, "王主管")
    assert second.status == Status.CLOSED  # 沒有維修任務,套用即結案


def test_events_queue_while_waiting_and_open_right_after_the_decision():
    """事件照間隔持續發生;還有事件等決定時先排隊,處理完立刻開下一件。"""
    factory = VirtualFactory(first_event_delay_s=5, next_event_choices_s=(30,))
    assert factory.tick(100) is None  # 尚未運轉
    factory.start(now=100)
    assert factory.tick(104) is None
    first = factory.tick(105)
    assert first is not None and first.title.startswith("射出機 IM-02 停機")
    factory.process(first.id)
    assert factory.tick(135) is None and factory.state()["backlog"] == 1  # 發生了,但先排隊
    assert factory.tick(165) is None and factory.state()["backlog"] == 2
    assert first.snapshot.version == factory.snapshot.version  # 排隊中的事件不會讓眼前的方案過期
    factory.approve(first.id, "P3", "王主管")
    second = factory.tick(166)
    assert second is not None and second.id == "INC-0002" and factory.state()["backlog"] == 1
    assert "排隊" in second.trace[0].message


def test_event_interval_is_picked_from_30_second_steps_up_to_4_minutes():
    import random

    factory = VirtualFactory(rng=random.Random(1))
    assert factory.next_event_choices_s == (30, 60, 90, 120, 150, 180, 210, 240)
    factory.start(now=0)
    factory.tick(5)  # 第一個事件
    gaps = []
    for _ in range(20):
        at = factory.next_event_at
        factory.tick(at)
        gaps.append(factory.next_event_at - at)
    assert set(gaps) <= set(factory.next_event_choices_s) and len(set(gaps)) > 1


def test_manual_event_queues_when_a_decision_is_pending():
    factory = VirtualFactory()
    first = factory.emit_next()
    factory.process(first.id)
    assert factory.emit_next() is None and factory.state()["backlog"] == 1


def test_event_source_is_deterministic():
    def titles():
        source, snapshot = EventSource(seed=3), demo_snapshot()
        return [[(e.type, e.subject) for e in source.next(snapshot)] for _ in range(6)]

    assert titles() == titles()


@pytest.mark.parametrize("reason", ["", "   ", "x" * (MAX_REASON_CHARS + 1)])
def test_rejection_needs_a_reason_within_the_limit(reason):
    factory = VirtualFactory()
    incident = main_scenario(factory)
    with pytest.raises(GovernanceError, match="理由"):
        factory.reject(incident.id, "王主管", reason)
    assert incident.status == Status.AWAITING_APPROVAL and incident.decisions == []


def test_rejection_needs_an_actor_and_keeps_the_trimmed_reason():
    factory = VirtualFactory()
    incident = main_scenario(factory)
    with pytest.raises(GovernanceError, match="操作者"):
        factory.reject(incident.id, "  ", "交期不能接受")
    factory.reject(incident.id, "王主管", "  交期不能接受  ")
    assert (incident.decisions[-1].actor, incident.decisions[-1].reason) == ("王主管", "交期不能接受")


def test_schedule_metrics_say_which_snapshot_they_were_computed_on():
    factory = VirtualFactory()
    incident = main_scenario(factory)
    schedule = factory.state()["schedule"]
    assert schedule["computed_on_snapshot"] == 1 < factory.snapshot.version
    assert schedule["computed_at"] and not schedule["still_valid"]

    factory.approve(incident.id, "P3", "王主管")
    schedule = factory.state()["schedule"]
    assert schedule["computed_on_snapshot"] == factory.snapshot.version and schedule["still_valid"]


def _seen(incident):
    """主管畫面上看到的版本;核准請求帶著它,後端才知道畫面是不是舊的。"""
    return {"snapshot_version": incident.snapshot.version, "schedule_version": incident.schedule_version,
            "policy_version": incident.policy_version}


def test_approval_from_an_outdated_screen_is_refused_without_changes():
    """R04-A05/R06-A02:畫面版本對不上就拒絕,先不改任何狀態,提示重新整理。"""
    factory = VirtualFactory()
    incident = main_scenario(factory)
    old_screen = {**_seen(incident), "snapshot_version": incident.snapshot.version - 1}
    trace_before = len(incident.trace)
    with pytest.raises(GovernanceError, match="重新整理"):
        factory.approve(incident.id, "P3", "王主管", expected=old_screen)
    assert incident.status == Status.AWAITING_APPROVAL and incident.decisions == []
    assert factory.schedule_version == 1 and len(incident.trace) == trace_before
    factory.approve(incident.id, "P3", "王主管", expected=_seen(incident))
    assert factory.schedule_version == 2


def test_resending_the_same_approval_applies_the_schedule_once():
    """R06-A03:回應遺失後重送同一個請求,回傳原結果,排程只套用一次。"""
    factory = VirtualFactory()
    incident = main_scenario(factory)
    first = factory.approve(incident.id, "P3", "王主管", request_id="req-1")
    again = factory.approve(incident.id, "P3", "王主管", request_id="req-1")
    assert again is first and factory.schedule_version == 2
    assert [d.request_id for d in incident.decisions] == ["req-1"]
    with pytest.raises(GovernanceError):  # 不同的請求還是照規則擋下
        factory.approve(incident.id, "P3", "王主管", request_id="req-2")
