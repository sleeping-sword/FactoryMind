"""AI 角色:報告(事件摘要)與主管問答。

模型只負責把系統算好的結果講成白話,不產生數字、不排序、不能核准。所有回答都先經過數字檢查:
出現資料裡沒有的數字就視為捏造,要求重寫;仍不合格就退回規則產生的文字,並標示來源。
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .llm import LLMClient, LLMError
from .models import MIN_PER_DAY, CustomerTier, by_id
from .evaluate import changed_work_orders
from .plan import Evaluation
from .policy import Policy

METRIC_LABEL = {
    "strategic_tardiness_min": "策略客戶延誤",
    "total_tardiness_min": "總延誤",
    "changed_work_orders": "改動工單數",
    "cost_ntd": "額外成本",
}
COST_LABEL = {"overtime": "加班", "outsourcing": "外包", "setup": "換模"}
STATUS_LABEL = {
    "processing": "系統處理中",
    "awaiting_approval": "等主管決定",
    "no_compliant_plan": "沒有合規方案",
    "rejected": "主管已退回建議",
    "stale": "方案已過期,需重新評估",
    "failed": "處理失敗",
    "applied_pending_tasks": "排程已生效,設備維修中",
    "closed": "已結案",
}

SYSTEM_PROMPT = """你是 FactoryMind 的報告助理,對象是塑膠射出廠的生產主管,不是工程師。
- 用繁體中文、白話、短句,避免指標欄位名與英文代碼(方案、工單、機台編號可以用)。
- 只根據提供的「事件資料」回答;資料裡沒有的就直說不知道,不要推測故障原因或復機時間。
- 數字只能照抄資料中出現的寫法(包含單位與時間格式),不可自行換算、加總或估計。
- 方案的計算、硬規則檢核與排序都由系統完成;你不能核准、退回或修改方案,也不能放寬規則。
- 主管要採取行動時,提醒他用畫面上的按鈕。"""


class AiBrief(BaseModel):
    """事件摘要。source 標示是模型寫的還是規則產生的。"""

    headline: str
    reasons: list[str] = []
    watch: list[str] = []
    source: Literal["ai", "rules"]
    model: str | None = None
    note: str | None = Field(default=None, description="沒用到 AI 的原因")
    at: str


class ChatTurn(BaseModel):
    question: str
    answer: str
    source: Literal["ai", "rules"]
    model: str | None = None
    actor: str
    at: str


class _Brief(BaseModel):
    headline: str = Field(description="一句話,25 字內,說建議主管做什麼")
    reasons: list[str] = Field(max_length=3, description="最多 3 點:為什麼選它、其他方案為何落選")
    watch: list[str] = Field(max_length=3, description="最多 3 點:核准前要注意的事,沒有就給空陣列")


class _Answer(BaseModel):
    answer: str = Field(description="給主管的回答,150 字內")


# ── 給人看的格式,與前端 lib/format.ts 一致 ─────────────────────

def clock(minute: int) -> str:
    day, rest = divmod(minute, MIN_PER_DAY)
    return f"D{day} {rest // 60:02d}:{rest % 60:02d}"


def duration(minutes: float) -> str:
    minutes = int(minutes)
    if minutes == 0:
        return "0 分"
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} 分"
    return f"{hours:,} 小時 {rest} 分" if rest else f"{hours:,} 小時"


def ntd(value: float) -> str:
    return f"NT${round(value):,}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 事件資料:模型看得到的全部內容 ──────────────────────────────

def _outcome(ev: Evaluation) -> str:
    k = ev.kpis
    if k is None:
        return "無指標"
    costs = "、".join(f"{COST_LABEL.get(key, key)} {ntd(v)}" for key, v in k.cost_breakdown_ntd.items() if v > 0)
    parts = [
        "策略客戶全部準時" if k.strategic_tardiness_min == 0 else f"策略客戶延誤 {duration(k.strategic_tardiness_min)}",
        "沒有逾期訂單" if not k.late_work_orders
        else f"逾期 {len(k.late_work_orders)} 張({'、'.join(k.late_work_orders)}),合計延誤 {duration(k.total_tardiness_min)}",
        f"改動既有工單 {k.changed_work_orders} 張" if k.changed_work_orders is not None else "",
        f"額外成本 {ntd(k.cost_ntd)}" + (f"({costs})" if costs else ""),
    ]
    return ";".join(p for p in parts if p)


def _changes(ev: Evaluation, reference: Evaluation) -> str:
    """這個方案改動了哪些既有工單、怎麼改。"""
    before = {op.work_order_id: op.machine_id for op in reference.operations}
    after = {op.work_order_id: op.machine_id for op in ev.operations}
    parts = []
    for wo_id in changed_work_orders(ev.outsourced, ev.operations, reference):
        if wo_id in ev.outsourced:
            parts.append(f"{wo_id}(改外包)")
        elif wo_id not in after:
            parts.append(f"{wo_id}(取消外包)")
        elif before.get(wo_id) != after[wo_id]:
            parts.append(f"{wo_id}(換機台 {before.get(wo_id)}→{after[wo_id]})")
        else:
            parts.append(f"{wo_id}(同機台改時間)")
    return "改動的工單:" + "、".join(parts) if parts else "不改動既有工單"


def _elimination(e) -> str:
    if e.stage in METRIC_LABEL and e.value is not None and e.best is not None:
        fmt = ntd if e.stage == "cost_ntd" else (lambda v: f"{int(v)} 張") if e.stage == "changed_work_orders" else duration
        allowed = f",容許差距 {fmt(e.threshold)}" if e.threshold else ""
        return f"比較「{METRIC_LABEL[e.stage]}」時落選:它是 {fmt(e.value)},最好的方案是 {fmt(e.best)}{allowed}"
    return f"{e.stage}不通過:{e.reason}"


def build_facts(incident, policy: Policy) -> str:
    """把事件單整理成白話的事件資料。incident 為 governance.Incident;呼叫端需持有鎖。"""
    snap = incident.snapshot
    machines, products, customers = by_id(snap.machines), by_id(snap.products), by_id(snap.customers)
    lines = [f"事件 {incident.id}:{incident.title}(目前狀態:{STATUS_LABEL[incident.status.value]})", "", "## 發生什麼事"]
    for e in incident.events:
        if e.type == "machine_down":
            m = machines.get(e.subject)
            lines.append(f"- 射出機 {e.subject} 停機({m.role if m else ''}),{e.note}")
        elif e.work_order:
            w = e.work_order
            c, p = customers.get(w.customer_id), products.get(w.product_id)
            tier = "策略客戶" if c and c.tier == CustomerTier.STRATEGIC else "一般客戶"
            lines.append(
                f"- 急單 {w.id}:{c.name if c else w.customer_id}({tier}),{p.name if p else w.product_id} "
                f"{w.quantity:,} 個,交期 {clock(w.due_min)}。{e.note}"
            )
    if incident.assessment:
        lines.append(f"- 直接受影響的工單:{'、'.join(incident.assessment.affected_work_orders) or '無'}")
        for reason in incident.assessment.if_unhandled[:6]:
            lines.append(f"- 不處理的話:{reason}")

    lines += ["", f"## 企業政策 {policy.version}", "比較順位(由先到後):"]
    for n, c in enumerate(policy.ranking, 1):
        unit = ntd(c.threshold) if c.metric == "cost_ntd" else f"{int(c.threshold)} 張" if c.metric == "changed_work_orders" else duration(c.threshold)
        lines.append(f"{n}. {METRIC_LABEL[c.metric]}越少越好" + (f"(差距在 {unit} 內視為相同)" if c.threshold else ""))
    lines.append(
        f"硬規則:每台每天加班最多 {duration(policy.hard_limits.max_overtime_min_per_machine_day)},"
        f"加班預算 {ntd(policy.hard_limits.overtime_budget_ntd)};模具需符合機台噸數、停機機台不可排、缺料不可排。"
    )

    lines += ["", f"## 候選方案(共 {len(incident.plans)} 個,已在虛擬工廠試跑)"]
    eliminated = {e.plan_id: e for e in (incident.ranking.eliminated if incident.ranking else [])}
    evaluations = {ev.plan_id: ev for ev in incident.evaluations}
    for plan in incident.plans:
        ev = evaluations.get(plan.id)
        if ev is None:
            continue
        extras = []
        if plan.overtime:
            extras.append("加班 " + "、".join(f"{g.machine_id} 第 {g.day} 天 {duration(g.minutes)}" for g in plan.overtime))
        if plan.outsourced:
            extras.append("外包 " + "、".join(plan.outsourced))
        verdict = "系統建議" if incident.ranking and incident.ranking.winner == plan.id else ""
        if plan.id in eliminated:
            verdict = _elimination(eliminated[plan.id])
        lines.append(f"- {plan.id}「{plan.label}」:{_outcome(ev)}" + (f";{','.join(extras)}" if extras else "") + (f"。{verdict}" if verdict else ""))
        if ev.kpis is not None:
            lines.append(f"  {_changes(ev, incident.reference)}")
    if incident.ranking:
        if incident.ranking.undecided_reason:
            lines.append(f"系統未選出方案:{incident.ranking.undecided_reason}")
        if incident.ranking.tie_break:
            lines.append(f"同分處理:{incident.ranking.tie_break}")

    if incident.decisions:
        lines += ["", "## 主管已做的決定"]
        for d in incident.decisions:
            act = "核准" if d.kind == "approved" else "退回"
            lines.append(f"- {d.actor} {act} {d.plan_id or ''}" + (f",理由:{d.reason}" if d.reason else ""))
    pending = [t for t in incident.tasks if not t.done]
    if pending:
        lines.append("待辦:" + "、".join(t.description for t in pending) + "(復機時間未知)")
    return "\n".join(lines)


def rule_brief(incident, note: str | None = None) -> AiBrief:
    """不用模型的摘要;AI 關閉或失敗時使用。"""
    ranking = incident.ranking
    plans = {p.id: p for p in incident.plans}
    evaluations = {ev.plan_id: ev for ev in incident.evaluations}
    if ranking is None or ranking.winner is None:
        reasons = [f"{e.plan_id}:{_elimination(e)}" for e in (ranking.eliminated if ranking else [])][:3]
        return AiBrief(headline="沒有合規的方案,需要你調整條件", reasons=reasons, source="rules", note=note, at=_now())
    plan = plans[ranking.winner]
    reasons = [_outcome(evaluations[plan.id])]
    reasons += [f"{e.plan_id}:{_elimination(e)}" for e in ranking.eliminated][:2]
    watch = []
    if plan.overtime or plan.outsourced:
        watch.append("含加班或外包,核准後要通知人員或下單")
    if any(not t.done for t in incident.tasks):
        watch.append("停機機台的復機時間未知,修好後要回報")
    return AiBrief(headline=f"建議採用 {plan.id}「{plan.label}」", reasons=reasons, watch=watch, source="rules", note=note, at=_now())


# ── 數字檢查 ─────────────────────────────────────────────────

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "").lstrip("0") or "0" for m in _NUMBER.findall(text)}


def invented_numbers(text: str, facts: str) -> list[str]:
    """回答裡出現、但事件資料裡沒有的數字。個位數(第一、兩點之類)不算。"""
    allowed = _numbers(facts)
    return sorted(n for n in _numbers(text) if n not in allowed and not (n.isdigit() and int(n) < 10))


def _number_check(facts: str):
    def check(result: BaseModel) -> str | None:
        text = " ".join(str(v) for v in result.model_dump().values())
        bad = invented_numbers(text, facts)
        return f"出現事件資料裡沒有的數字 {'、'.join(bad)},只能照抄資料中的數字" if bad else None

    return check


class Advisor:
    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    def status(self) -> dict:
        return self.client.status()

    def brief(self, facts: str, fallback: AiBrief) -> AiBrief:
        if not self.client.enabled:
            return fallback
        try:
            result = self.client.structured(
                SYSTEM_PROMPT,
                f"事件資料:\n{facts}\n\n請替主管寫這個事件的摘要。",
                _Brief,
                check=_number_check(facts),
            )
        except LLMError as exc:
            return fallback.model_copy(update={"note": str(exc)})
        return AiBrief(
            headline=result.headline, reasons=result.reasons[:3], watch=result.watch[:3],
            source="ai", model=self.client.config.model, at=_now(),
        )

    def answer(self, facts: str, history: list[ChatTurn], question: str, actor: str) -> ChatTurn:
        if not self.client.enabled:
            return ChatTurn(question=question, answer="AI 尚未啟用:後端沒有設定金鑰。", source="rules", actor=actor, at=_now())
        past = "\n".join(f"主管:{t.question}\n助理:{t.answer}" for t in history[-4:])
        try:
            result = self.client.structured(
                SYSTEM_PROMPT,
                f"事件資料:\n{facts}\n\n" + (f"先前的對話:\n{past}\n\n" if past else "") + f"主管的問題:{question}",
                _Answer,
                check=_number_check(f"{facts}\n{question}"),
            )
        except LLMError as exc:
            return ChatTurn(question=question, answer=f"{exc}。", source="rules", actor=actor, at=_now())
        return ChatTurn(question=question, answer=result.answer, source="ai", model=self.client.config.model, actor=actor, at=_now())
