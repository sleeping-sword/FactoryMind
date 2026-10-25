"""虛擬工廠與治理主線:偵測 → 評估 → 提案 → 模擬評估 → 決策 → 稽核回報。

虛擬工廠持有目前的工廠快照與已生效排程。事件進來後開立事件單,系統產生並試跑候選方案、
依企業政策排序,然後停在「等待主管核准」。核准會綁定確切的方案、快照與排程版本,
套用前重新檢核;任何一項已改變,方案即過期,需重新評估。

狀態只存在記憶體,重啟即重置。尚無登入與權限,核准者名稱由呼叫端提供。
"""

import random
import threading
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from .ai import AiBrief, Advisor, ChatTurn, build_facts, rule_brief
from .candidates import generate_candidates
from .dispatch import dispatch
from .evaluate import evaluate
from .events import FactoryEvent, apply_event, machine_restored
from .models import CustomerTier, FactorySnapshot, MachineStatus, WorkOrder, by_id, is_compatible
from .optimize import SearchResult
from .plan import Evaluation, Plan
from .policy import Policy
from .ranking import RankingResult, rank
from .scenarios.demo import demo_policy, demo_rush_order, demo_snapshot, due


class GovernanceError(Exception):
    """操作被治理規則擋下;訊息直接給主管看。"""


class Stage(str, Enum):
    DETECT = "detect"
    ASSESS = "assess"
    PROPOSE = "propose"
    SIMULATE = "simulate"
    DECIDE = "decide"
    REPORT = "report"


class Status(str, Enum):
    PROCESSING = "processing"
    AWAITING_APPROVAL = "awaiting_approval"
    NO_COMPLIANT_PLAN = "no_compliant_plan"
    REJECTED = "rejected"
    STALE = "stale"
    FAILED = "failed"
    APPLIED_PENDING_TASKS = "applied_pending_tasks"
    CLOSED = "closed"


NEEDS_DECISION = {
    Status.PROCESSING, Status.AWAITING_APPROVAL, Status.NO_COMPLIANT_PLAN, Status.REJECTED, Status.STALE, Status.FAILED,
}


class TraceEntry(BaseModel):
    seq: int
    at: str
    stage: Stage
    actor: str
    message: str


class Task(BaseModel):
    """必要的人工任務。建立任務不等於完成;未回報前事件不可結案。"""

    id: str
    kind: Literal["repair_confirmation"]
    subject: str
    role: str
    description: str
    done: bool = False
    done_by: str | None = None
    done_at: str | None = None


class Decision(BaseModel):
    kind: Literal["approved", "rejected"]
    plan_id: str | None
    actor: str
    at: str
    reason: str = ""
    followed_recommendation: bool | None = None
    snapshot_version: int
    schedule_version: int
    policy_version: str


class Assessment(BaseModel):
    affected_work_orders: list[str]
    if_unhandled: list[str]


class Incident(BaseModel):
    id: str
    title: str
    opened_at: str
    events: list[FactoryEvent]
    status: Status = Status.PROCESSING
    stage: Stage = Stage.DETECT
    snapshot: FactorySnapshot
    schedule_version: int
    policy_version: str
    reference: Evaluation
    assessment: Assessment | None = None
    plans: list[Plan] = []
    evaluations: list[Evaluation] = []
    search: dict[str, SearchResult] = {}
    ranking: RankingResult | None = None
    decisions: list[Decision] = []
    applied_plan_id: str | None = None
    tasks: list[Task] = []
    trace: list[TraceEntry] = []
    error: str | None = None
    ai_brief: AiBrief | None = None
    chat: list[ChatTurn] = []

    def log(self, stage: Stage, actor: str, message: str, *, advance: bool = True) -> None:
        if advance:
            self.stage = stage
        self.trace.append(
            TraceEntry(seq=len(self.trace) + 1, at=_now(), stage=stage, actor=actor, message=message)
        )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


SYSTEM = "系統"
MAX_REASON_CHARS = 200  # 退回理由上限;前端 lib/format.ts 同步


class EventSource:
    """虛擬工廠的擾動來源。第一個事件是主情境(停機同時遇到急單),之後由固定種子的亂數產生。"""

    def __init__(self, seed: int = 7):
        self.random = random.Random(seed)
        self.emitted = 0

    def next(self, snapshot: FactorySnapshot) -> list[FactoryEvent]:
        self.emitted += 1
        machines = by_id(snapshot.machines)
        if self.emitted == 1 and machines["IM-02"].status == MachineStatus.UP:
            rush = demo_rush_order()
            return [
                FactoryEvent(type="machine_down", subject="IM-02", note="設備異常警報,復機時間未知"),
                FactoryEvent(type="rush_order", subject=rush.id, note="策略客戶的隔日醫療件急單", work_order=rush),
            ]
        up = [m for m in snapshot.machines if m.status == MachineStatus.UP]
        if len(up) >= 5 and self.random.random() < 0.35:
            machine = self.random.choice(up)
            return [FactoryEvent(type="machine_down", subject=machine.id, note="設備異常警報,復機時間未知")]
        return [self._rush_order(snapshot, up)]

    def _rush_order(self, snapshot: FactorySnapshot, up: list) -> FactoryEvent:
        molds = by_id(snapshot.molds)
        makeable = [p for p in snapshot.products if any(is_compatible(molds[p.mold_id], m) for m in up)]
        product = self.random.choice(makeable or snapshot.products)
        customer = self.random.choice(snapshot.customers)
        number = 1 + sum(1 for w in snapshot.work_orders if w.is_rush)
        order = WorkOrder(
            id=f"WO-R{number}",
            customer_id=customer.id,
            product_id=product.id,
            quantity=product.units_per_hour * self.random.randint(3, 8),
            due_min=due(self.random.randint(1, 3)),
            is_rush=True,
        )
        kind = "策略客戶" if customer.tier == CustomerTier.STRATEGIC else "一般客戶"
        return FactoryEvent(type="rush_order", subject=order.id, note=f"{kind}臨時追加的急單", work_order=order)


class VirtualFactory:
    def __init__(self, *, first_event_delay_s: float = 5, next_event_delay_s: float = 30, advisor: Advisor | None = None):
        self._lock = threading.RLock()
        self.advisor = advisor or Advisor()
        self.first_event_delay_s = first_event_delay_s
        self.next_event_delay_s = next_event_delay_s
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.policy: Policy = demo_policy()
            self.snapshot: FactorySnapshot = demo_snapshot()
            self.plan: Plan = dispatch(self.snapshot, plan_id="S1", label="原始排程(依交期派工)")
            self.evaluation: Evaluation = evaluate(self.snapshot, self.plan, self.policy)
            self.schedule_version = 1
            self.schedule_basis = (self.snapshot.version, _now())  # 目前排程指標是用哪版快照、何時算的
            self.incidents: list[Incident] = []
            self.source = EventSource()
            self.running = False
            self.next_event_at: float | None = None
            self.revision = getattr(self, "revision", 0) + 1

    # ── 虛擬工廠的運轉與自動事件 ───────────────────────────────

    def start(self, now: float) -> None:
        with self._lock:
            if not self.running:
                self.running = True
                self.next_event_at = now + (self.first_event_delay_s if not self.incidents else self.next_event_delay_s)
                self.revision += 1

    def pause(self) -> None:
        with self._lock:
            self.running = False
            self.next_event_at = None
            self.revision += 1

    def ready_for_event(self) -> bool:
        """有事件還在等生產決策時不再丟新事件,讓主管一次處理一件。"""
        return not any(i.status in NEEDS_DECISION for i in self.incidents)

    def tick(self, now: float) -> Incident | None:
        """由背景迴圈定期呼叫;時間到且可受理時,虛擬工廠自動發出下一個事件。"""
        with self._lock:
            if not self.running or not self.ready_for_event():
                return None
            if self.next_event_at is None:
                self.next_event_at = now + self.next_event_delay_s
                return None
            if now < self.next_event_at:
                return None
            self.next_event_at = None
            return self.open_incident(self.source.next(self.snapshot))

    def emit_next(self) -> Incident:
        with self._lock:
            if not self.ready_for_event():
                raise GovernanceError("還有事件在等待決策,處理完再產生下一個事件")
            self.next_event_at = None
            return self.open_incident(self.source.next(self.snapshot))

    # ── 治理主線 ────────────────────────────────────────────

    def open_incident(self, events: list[FactoryEvent]) -> Incident:
        """偵測:受理事件、更新工廠快照、開立事件單。後續步驟由 process() 完成。"""
        with self._lock:
            snapshot = self.snapshot
            for event in events:
                snapshot = apply_event(snapshot, event)
            self.snapshot = snapshot
            parts = [
                f"射出機 {e.subject} 停機" if e.type == "machine_down" else f"急單 {e.subject}" for e in events
            ]
            incident = Incident(
                id=f"INC-{len(self.incidents) + 1:04d}",
                title="、".join(parts),
                opened_at=_now(),
                events=events,
                snapshot=snapshot,
                schedule_version=self.schedule_version,
                policy_version=self.policy.version,
                reference=self.evaluation,
                tasks=[
                    Task(
                        id=f"T{n + 1}",
                        kind="repair_confirmation",
                        subject=e.subject,
                        role="維修確認者",
                        description=f"回報 {e.subject} 維修結果並確認復機",
                    )
                    for n, e in enumerate(e for e in events if e.type == "machine_down")
                ],
            )
            incident.log(
                Stage.DETECT, "虛擬工廠",
                f"收到事件:{'、'.join(parts)}。工廠快照更新為 v{snapshot.version},相關事件合併為同一件共同評估。",
            )
            self.incidents.append(incident)
            self.revision += 1
            return incident

    def process(self, incident_id: str) -> Incident:
        """評估 → 提案 → 模擬評估 → 排序,停在等待主管決定。計算在鎖外進行,不擋住查詢。"""
        with self._lock:
            incident = self._get(incident_id)
            snapshot, policy, reference, current_plan = incident.snapshot, self.policy, incident.reference, self.plan
        try:
            down = {e.subject for e in incident.events if e.type == "machine_down"}
            rush = [e.subject for e in incident.events if e.type == "rush_order"]
            affected = [op.work_order_id for op in reference.operations if op.machine_id in down] + rush
            unhandled = evaluate(snapshot, current_plan, policy)
            assessment = Assessment(
                affected_work_orders=affected,
                if_unhandled=[*unhandled.infeasible_reasons, *[v.message for v in unhandled.violations or []]],
            )
            with self._lock:
                incident.assessment = assessment
                incident.log(
                    Stage.ASSESS, SYSTEM,
                    f"直接受影響的工單 {len(affected)} 張:{'、'.join(affected) or '無'}。"
                    + ("現有排程已無法照原樣執行。" if assessment.if_unhandled else "現有排程仍可執行。"),
                )
                self.revision += 1

            plans, searches = generate_candidates(
                snapshot, policy, reference,
                base_overtime=current_plan.overtime, base_outsourced=current_plan.outsourced, rush_ids=rush,
            )
            with self._lock:
                incident.plans, incident.search = plans, searches
                incident.log(Stage.PROPOSE, SYSTEM, f"提出 {len(plans)} 個候選方案,全部以快照 v{snapshot.version} 為共同起點。")
                self.revision += 1

            evaluations = [evaluate(snapshot, plan, policy, reference) for plan in plans]
            blocked = [ev.plan_id for ev in evaluations if not ev.eligible]
            ranking = rank(evaluations, policy.ranking)
            with self._lock:
                incident.evaluations = evaluations
                incident.log(
                    Stage.SIMULATE, SYSTEM,
                    f"已在虛擬工廠試跑 {len(evaluations)} 個方案並檢核硬規則;"
                    + (f"{'、'.join(blocked)} 不合規或不可行,已排除。" if blocked else "全部合規且可行。"),
                )
                incident.ranking = ranking
                if ranking.winner:
                    incident.status = Status.AWAITING_APPROVAL
                    incident.log(
                        Stage.DECIDE, SYSTEM,
                        f"依政策 {policy.version} 的比較順位,建議採用 {ranking.winner}。等待生產主管核准,尚未改動工廠。",
                    )
                else:
                    incident.status = Status.NO_COMPLIANT_PLAN
                    incident.log(Stage.DECIDE, SYSTEM, f"未選出方案:{ranking.undecided_reason}。交由生產主管處理。")
                incident.ai_brief = rule_brief(incident)
                facts, fallback = build_facts(incident, policy), incident.ai_brief
                self.revision += 1
        except Exception as exc:  # 計算失敗要留下紀錄,不可顯示成完成
            with self._lock:
                incident.status = Status.FAILED
                incident.error = f"{type(exc).__name__}: {exc}"
                incident.log(incident.stage, SYSTEM, f"處理失敗:{incident.error}")
                self.revision += 1
            return incident

        # 先用規則摘要讓主管可以決定;AI 摘要寫好再換上,不擋住決策
        if self.advisor.status()["enabled"]:
            brief = self.advisor.brief(facts, fallback)
            with self._lock:
                if incident.ranking is ranking:  # 期間被重新評估就丟掉舊的摘要
                    incident.ai_brief = brief
                    incident.log(
                        Stage.DECIDE, "報告角色",
                        f"由 {brief.model} 撰寫主管摘要;摘要中的數字皆已核對出自系統計算。" if brief.source == "ai"
                        else f"AI 摘要未完成({brief.note}),改用規則產生的摘要。",
                        advance=False,
                    )
                    self.revision += 1
        return incident

    def ask(self, incident_id: str, question: str, actor: str) -> ChatTurn:
        """主管針對事件提問。模型只看得到這個事件的資料,也不能改動任何東西。"""
        question = question.strip()
        if not question:
            raise GovernanceError("請輸入問題")
        if len(question) > 300:
            raise GovernanceError("問題太長,請精簡到 300 字內")
        with self._lock:
            incident = self._get(incident_id)
            facts, history = build_facts(incident, self.policy), list(incident.chat)
        turn = self.advisor.answer(facts, history, question, actor)
        with self._lock:
            incident.chat.append(turn)
            self.revision += 1
        return turn

    def reassess(self, incident_id: str, actor: str) -> Incident:
        """方案過期、被退回或計算失敗後,以目前的工廠狀態重新評估。"""
        with self._lock:
            incident = self._get(incident_id)
            if incident.status not in {Status.STALE, Status.REJECTED, Status.NO_COMPLIANT_PLAN, Status.FAILED}:
                raise GovernanceError("這個事件目前不需要重新評估")
            incident.snapshot = self.snapshot
            incident.schedule_version = self.schedule_version
            incident.policy_version = self.policy.version
            incident.reference = self.evaluation
            incident.status = Status.PROCESSING
            incident.error = None
            incident.plans, incident.evaluations, incident.search, incident.ranking = [], [], {}, None
            incident.ai_brief = None
            incident.log(Stage.ASSESS, actor, f"要求以目前狀態(快照 v{self.snapshot.version})重新評估。")
            self.revision += 1
            return incident

    def approve(self, incident_id: str, plan_id: str, actor: str) -> Incident:
        with self._lock:
            incident = self._get(incident_id)
            if incident.status not in {Status.AWAITING_APPROVAL, Status.REJECTED}:
                raise GovernanceError("這個事件目前不在可核准的狀態")
            plan = next((p for p in incident.plans if p.id == plan_id), None)
            evaluation = next((e for e in incident.evaluations if e.plan_id == plan_id), None)
            if plan is None or evaluation is None:
                raise GovernanceError(f"找不到方案 {plan_id}")
            if not evaluation.eligible:
                raise GovernanceError(f"方案 {plan_id} 不可行或違反硬規則,不能核准")

            # 執行前重驗:核准綁定的快照、排程與政策版本必須仍然有效
            fresh = evaluate(self.snapshot, plan, self.policy, incident.reference)
            if (
                incident.snapshot.version != self.snapshot.version
                or incident.schedule_version != self.schedule_version
                or incident.policy_version != self.policy.version
                or not fresh.eligible
                or fresh.operations != evaluation.operations
            ):
                incident.status = Status.STALE
                incident.log(Stage.DECIDE, SYSTEM, f"{actor} 嘗試核准 {plan_id},但工廠狀態已改變,方案過期,未執行。")
                self.revision += 1
                raise GovernanceError("工廠狀態已改變,這批方案已過期,請重新評估")

            incident.decisions.append(
                Decision(
                    kind="approved", plan_id=plan_id, actor=actor, at=_now(),
                    followed_recommendation=incident.ranking is not None and incident.ranking.winner == plan_id,
                    snapshot_version=self.snapshot.version, schedule_version=self.schedule_version,
                    policy_version=self.policy.version,
                )
            )
            incident.log(
                Stage.DECIDE, actor,
                f"核准方案 {plan_id}「{plan.label}」(快照 v{self.snapshot.version}、政策 {self.policy.version})。",
            )
            self.plan, self.evaluation = plan, fresh
            self.schedule_version += 1
            self.schedule_basis = (self.snapshot.version, _now())
            incident.applied_plan_id = plan_id
            incident.log(
                Stage.REPORT, SYSTEM,
                f"執行前重驗通過,已套用到虛擬工廠並核對:排程 v{self.schedule_version} 生效,"
                f"改動既有工單 {fresh.kpis.changed_work_orders} 張。",
            )
            self._settle(incident)
            self._invalidate_others(incident.id, f"排程已因 {incident.id} 更新為 v{self.schedule_version}")
            self.revision += 1
            return incident

    def reject(self, incident_id: str, actor: str, reason: str) -> Incident:
        with self._lock:
            incident = self._get(incident_id)
            if incident.status != Status.AWAITING_APPROVAL:
                raise GovernanceError("這個事件目前沒有等待核准的建議")
            actor, reason = actor.strip(), reason.strip()
            if not actor:
                raise GovernanceError("請填寫操作者")
            if not reason:
                raise GovernanceError("請填寫退回理由")
            if len(reason) > MAX_REASON_CHARS:
                raise GovernanceError(f"退回理由請在 {MAX_REASON_CHARS} 字內")
            incident.decisions.append(
                Decision(
                    kind="rejected", plan_id=incident.ranking.winner if incident.ranking else None, actor=actor,
                    at=_now(), reason=reason, snapshot_version=self.snapshot.version,
                    schedule_version=self.schedule_version, policy_version=self.policy.version,
                )
            )
            incident.status = Status.REJECTED
            incident.log(Stage.DECIDE, actor, f"退回系統建議,理由:{reason}。工廠未被改動。")
            self.revision += 1
            return incident

    def confirm_task(self, incident_id: str, task_id: str, actor: str) -> Incident:
        with self._lock:
            incident = self._get(incident_id)
            task = next((t for t in incident.tasks if t.id == task_id), None)
            if task is None:
                raise GovernanceError(f"找不到任務 {task_id}")
            if task.done:
                raise GovernanceError("這項任務已經回報過了")
            task.done, task.done_by, task.done_at = True, actor, _now()
            self.snapshot = machine_restored(self.snapshot, task.subject)
            incident.log(
                Stage.REPORT, actor,
                f"回報 {task.subject} 維修完成並確認復機。工廠快照更新為 v{self.snapshot.version};已生效的排程不變。",
            )
            self._settle(incident)
            self._invalidate_others(incident.id, f"{task.subject} 已復機")
            self.revision += 1
            return incident

    def _settle(self, incident: Incident) -> None:
        """生產調整已生效且必要任務都回報後才結案;否則顯示還在等什麼。"""
        if incident.applied_plan_id is None:
            return
        pending = [t for t in incident.tasks if not t.done]
        if pending:
            incident.status = Status.APPLIED_PENDING_TASKS
            return
        incident.status = Status.CLOSED
        incident.log(Stage.REPORT, SYSTEM, "生產調整已生效,必要任務皆已回報,事件結案。實際交付與成本另行追蹤。")
        if self.running:
            self.next_event_at = None

    def _invalidate_others(self, except_id: str, why: str) -> None:
        for other in self.incidents:
            if other.id != except_id and other.status in {Status.AWAITING_APPROVAL, Status.REJECTED}:
                other.status = Status.STALE
                other.log(Stage.DECIDE, SYSTEM, f"{why},原候選方案過期,需重新評估。")

    def _get(self, incident_id: str) -> Incident:
        for incident in self.incidents:
            if incident.id == incident_id:
                return incident
        raise GovernanceError(f"找不到事件 {incident_id}")

    # ── 查詢 ────────────────────────────────────────────────

    def state(self) -> dict:
        with self._lock:
            return {
                "revision": self.revision,
                "running": self.running,
                "ai": self.advisor.status(),
                "snapshot": self.snapshot.model_dump(mode="json"),
                "policy": self.policy.model_dump(mode="json"),
                "schedule": {
                    "version": self.schedule_version,
                    "plan": self.plan.model_dump(mode="json"),
                    "evaluation": self.evaluation.model_dump(mode="json"),
                    "still_valid": evaluate(self.snapshot, self.plan, self.policy).eligible,
                    "computed_on_snapshot": self.schedule_basis[0],
                    "computed_at": self.schedule_basis[1],
                },
                "incidents": [
                    {
                        "id": i.id, "title": i.title, "opened_at": i.opened_at, "status": i.status.value,
                        "stage": i.stage.value, "trace_length": len(i.trace),
                    }
                    for i in self.incidents
                ],
            }

    def incident(self, incident_id: str) -> dict:
        with self._lock:
            return self._get(incident_id).model_dump(mode="json")
