"""AI 角色:報告(事件摘要)與主管問答。

模型只負責把系統算好的結果講成白話,不產生數字、不排序、不能核准。方案指標一律以證據標記 [E01]
引用,由系統換成數字並核對方案與指標(見 evidence.py);其他數字必須出自事件資料。不合格就要求重寫,
仍不合格就退回規則產生的文字,並標示來源。
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .evidence import MARKER, METRIC_LABEL, Evidence, check_text, clock, duration, ntd, show
from .llm import LLMClient, LLMError
from .models import CustomerTier, by_id
from .evaluate import changed_work_orders
from .plan import Evaluation
from .policy import Policy

COST_LABEL = {"overtime": "加班", "outsourcing": "外包", "setup": "換模", "trial": "試模"}
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
- 方案的指標(策略客戶延誤、總延誤、改動既有工單數、額外成本)不要直接寫數字,一律寫證據標記,例如「P3 額外成本 [E12]」;
  標記要緊接在它所屬的方案與指標後面。數字由系統填入,不要在標記後面再寫數字或單位。
- 其他數字只能照抄資料中出現的寫法,不可自行換算、加總或估計。
- 比較方案時講清楚是在哪些方案之間比較;「合規但順位落選」不是「違規」。
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
    evidence: list[Evidence] = Field(default=[], description="文字裡 [Exx] 標記對應的證據,保留當時版本")
    at: str


class ChatTurn(BaseModel):
    question: str
    answer: str
    source: Literal["ai", "rules"]
    model: str | None = None
    evidence: list[Evidence] = []
    actor: str
    at: str


class _Brief(BaseModel):
    headline: str = Field(description="一句話,25 字內,說建議主管做什麼")
    reasons: list[str] = Field(max_length=3, description="最多 3 點:為什麼選它、其他方案為何落選")
    watch: list[str] = Field(max_length=3, description="最多 3 點:核准前要注意的事,沒有就給空陣列")


class _Answer(BaseModel):
    answer: str = Field(description="給主管的回答,150 字內")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 事件資料:模型看得到的全部內容 ──────────────────────────────

class _Ref:
    """(方案, 指標) → 證據標記。"""

    def __init__(self, evidence: list[Evidence]):
        self.by_key = {(e.plan_id, e.metric): e for e in evidence}

    def __call__(self, plan_id: str, metric: str) -> str:
        e = self.by_key.get((plan_id, metric))
        return f"[{e.id}]" if e else "(無證據)"


def _outcome(ev: Evaluation, ref: _Ref) -> str:
    k = ev.kpis
    if k is None:
        return "無指標"
    costs = "、".join(f"{COST_LABEL.get(key, key)} {ntd(v)}" for key, v in k.cost_breakdown_ntd.items() if v > 0)
    late = f",逾期工單 {'、'.join(k.late_work_orders)}" if k.late_work_orders else ""
    parts = [
        f"策略客戶延誤 {ref(ev.plan_id, 'strategic_tardiness_min')}",
        f"總延誤 {ref(ev.plan_id, 'total_tardiness_min')}{late}",
        f"改動既有工單 {ref(ev.plan_id, 'changed_work_orders')}" if k.changed_work_orders is not None else "",
        f"額外成本 {ref(ev.plan_id, 'cost_ntd')}" + (f"(含{costs})" if "、" in costs else ""),
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


def _elimination(e, ref: _Ref, winner: str | None = None) -> str:
    """以 e.plan_id 開頭;順位落選寫明該輪比較集合,硬規則淘汰標明不合規。同為最佳時優先舉出系統建議的方案。"""
    if e.stage in METRIC_LABEL and e.value is not None and e.best is not None:
        pool = sorted(e.pool, key=lambda p: p != winner)
        leader = next(p for p in pool if (ev := ref.by_key.get((p, e.stage))) and ev.value == e.best)
        allowed = f",差距超過容許的 {show(e.stage, e.threshold)}" if e.threshold else ""
        return (
            f"{e.plan_id} 合規,但在 {'、'.join(e.pool)} 之間比較「{METRIC_LABEL[e.stage]}」時落選:"
            f"{e.plan_id} 是 {ref(e.plan_id, e.stage)},這輪最好的 {leader} 是 {ref(leader, e.stage)}{allowed}"
        )
    return f"{e.plan_id} {e.stage}不通過(不合規):{e.reason}"


def build_facts(incident, policy: Policy, evidence: list[Evidence]) -> str:
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

    ref = _Ref(evidence)
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
            verdict = _elimination(eliminated[plan.id], ref, incident.ranking.winner)
        lines.append(f"- {plan.id}「{plan.label}」:{_outcome(ev, ref)}" + (f";{','.join(extras)}" if extras else "") + (f"。{verdict}" if verdict else ""))
        # 只列建議方案改動哪些工單;其他方案的清單很長、摘要用不到,只會拖慢模型
        if ev.kpis is not None and incident.ranking and incident.ranking.winner == plan.id:
            lines.append(f"  {_changes(ev, incident.reference)}")
    if incident.ranking:
        if incident.ranking.undecided_reason:
            lines.append(f"系統未選出方案:{incident.ranking.undecided_reason}")
        if incident.ranking.tie_break:
            lines.append(f"同分處理:{incident.ranking.tie_break}")

    if evidence:
        lines += ["", "## 證據表(方案指標只能用這些標記引用)"]
        lines += [f"- [{e.id}] {e.label} = {e.display}" for e in evidence]

    if incident.decisions:
        lines += ["", "## 主管已做的決定"]
        for d in incident.decisions:
            act = "核准" if d.kind == "approved" else "退回"
            lines.append(f"- {d.actor} {act} {d.plan_id or ''}" + (f",理由:{d.reason}" if d.reason else ""))
    pending = [t for t in incident.tasks if not t.done]
    if pending:
        lines.append("待辦:" + "、".join(t.description for t in pending) + "(復機時間未知)")
    return "\n".join(lines)


def _cited(texts: list[str], evidence: list[Evidence]) -> list[Evidence]:
    used = {m for t in texts for m in MARKER.findall(t)}
    return [e for e in evidence if e.id in used]


def rule_brief(incident, evidence: list[Evidence], note: str | None = None) -> AiBrief:
    """不用模型的摘要;AI 關閉或失敗時使用。"""
    ranking = incident.ranking
    plans = {p.id: p for p in incident.plans}
    evaluations = {ev.plan_id: ev for ev in incident.evaluations}
    ref = _Ref(evidence)
    if ranking is None or ranking.winner is None:
        reasons = [_elimination(e, ref) for e in (ranking.eliminated if ranking else [])][:3]
        return AiBrief(headline="沒有合規的方案,需要你調整條件", reasons=reasons, source="rules", note=note,
                       evidence=_cited(reasons, evidence), at=_now())
    plan = plans[ranking.winner]
    reasons = [f"{plan.id}:{_outcome(evaluations[plan.id], ref)}"]
    reasons += [_elimination(e, ref, plan.id) for e in ranking.eliminated][:2]
    watch = []
    if plan.overtime or plan.outsourced:
        watch.append("含加班或外包,核准後要通知人員或下單")
    if any(not t.done for t in incident.tasks):
        watch.append("停機機台的復機時間未知,修好後要回報")
    return AiBrief(headline=f"建議採用 {plan.id}「{plan.label}」", reasons=reasons, watch=watch, source="rules", note=note,
                   evidence=_cited(reasons, evidence), at=_now())


# ── 數字檢查 ─────────────────────────────────────────────────

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "").lstrip("0") or "0" for m in _NUMBER.findall(text)}


def invented_numbers(text: str, facts: str) -> list[str]:
    """回答裡出現、但事件資料裡沒有的數字。個位數(第一、兩點之類)不算。"""
    allowed = _numbers(facts)
    text = MARKER.sub(" ", text)
    return sorted(n for n in _numbers(text) if n not in allowed and not (n.isdigit() and int(n) < 10))


# 系統沒有故障原因資料:問原因一律固定回答;模型自己講出原因也不採用
_CAUSE_QUESTION = re.compile(r"(停機|故障|壞|異常|IM-\d+).{0,10}原因|原因.{0,6}(停機|故障|壞)|為什麼.{0,8}(停機|壞|故障)|怎麼壞|哪裡壞")
_CAUSE_CLAIM = re.compile(
    r"(停機|故障|壞掉?)的?原因(是|為|在於)"
    r"|(老化|磨損|過熱|漏油|漏水|堵塞|斷裂|短路|卡模|螺桿|加熱圈|液壓|電路|感測器).{0,10}(停機|故障|壞|導致)"
)
UNKNOWN_CAUSE = "停機原因目前未知,待維修確認:系統沒有故障原因資料,不會推測原因。"


def _texts(result: BaseModel) -> list[str]:
    if isinstance(result, _Brief):
        return [result.headline, *result.reasons, *result.watch]
    return [result.answer]


def _evidence_check(facts: str, evidence: list[Evidence]):
    def check(result: _Brief | _Answer) -> str | None:
        texts = _texts(result)
        problems = [p for t in texts for p in check_text(t, evidence)]
        if any(_CAUSE_CLAIM.search(t) for t in texts):
            problems.append("資料沒有故障原因,不可推測或描述停機原因")
        if bad := invented_numbers(" ".join(texts), facts):
            problems.append(f"出現事件資料裡沒有的數字 {'、'.join(bad)},只能照抄資料中的數字")
        return ";".join(problems) or None

    return check


class Advisor:
    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    def status(self) -> dict:
        return self.client.status()

    def brief(self, facts: str, evidence: list[Evidence], fallback: AiBrief) -> AiBrief:
        if not self.client.enabled:
            return fallback
        try:
            result = self.client.structured(
                SYSTEM_PROMPT,
                f"事件資料:\n{facts}\n\n請替主管寫這個事件的摘要。",
                _Brief,
                check=_evidence_check(facts, evidence),
            )
        except LLMError as exc:
            return fallback.model_copy(update={"note": str(exc)})
        return AiBrief(
            headline=result.headline, reasons=result.reasons[:3], watch=result.watch[:3],
            source="ai", model=self.client.config.model, evidence=_cited(_texts(result), evidence), at=_now(),
        )

    def answer(self, facts: str, evidence: list[Evidence], history: list[ChatTurn], question: str, actor: str) -> ChatTurn:
        if _CAUSE_QUESTION.search(question):
            return ChatTurn(question=question, answer=UNKNOWN_CAUSE, source="rules", actor=actor, at=_now())
        if not self.client.enabled:
            reason = self.client.note or "後端沒有設定金鑰"
            return ChatTurn(question=question, answer=f"AI 尚未啟用:{reason}。", source="rules", actor=actor, at=_now())
        past = "\n".join(f"主管:{t.question}\n助理:{t.answer}" for t in history[-4:])
        try:
            result = self.client.structured(
                SYSTEM_PROMPT,
                f"事件資料:\n{facts}\n\n" + (f"先前的對話:\n{past}\n\n" if past else "") + f"主管的問題:{question}",
                _Answer,
                check=_evidence_check(f"{facts}\n{question}", evidence),
            )
        except LLMError as exc:
            return ChatTurn(question=question, answer=f"{exc}。", source="rules", actor=actor, at=_now())
        return ChatTurn(
            question=question, answer=result.answer, source="ai", model=self.client.config.model,
            evidence=_cited([result.answer], evidence), actor=actor, at=_now(),
        )
