"""計算證據:每個方案指標一筆,帶方案、指標、值、單位與版本。

AI 說明裡的方案指標數字一律以證據標記 [E01] 引用,由系統換成證據上的數字;
引用前先核對方案、指標、值與單位都和證據相同,對不上就不採用。
"""

import re
from typing import Literal

from pydantic import BaseModel

from .models import MIN_PER_DAY

METRIC_LABEL = {
    "strategic_tardiness_min": "策略客戶延誤",
    "total_tardiness_min": "總延誤",
    "changed_work_orders": "改動既有工單數",
    "cost_ntd": "額外成本",
}
UNIT = {"strategic_tardiness_min": "分鐘", "total_tardiness_min": "分鐘", "changed_work_orders": "張", "cost_ntd": "NT$"}


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


def show(metric: str, value: float) -> str:
    unit = UNIT[metric]
    return ntd(value) if unit == "NT$" else f"{int(value)} 張" if unit == "張" else duration(value)


class Evidence(BaseModel):
    id: str
    incident_id: str
    plan_id: str
    metric: Literal["strategic_tardiness_min", "total_tardiness_min", "changed_work_orders", "cost_ntd"]
    value: float
    unit: Literal["分鐘", "張", "NT$"]
    display: str
    snapshot_version: int
    policy_version: str
    engine_version: str

    @property
    def label(self) -> str:
        return f"{self.plan_id} {METRIC_LABEL[self.metric]}"


def build_evidence(incident) -> list[Evidence]:
    """incident 為 governance.Incident;呼叫端需持有鎖。只收計算成功、有指標的方案。"""
    evidence = []
    for ev in incident.evaluations:
        if ev.kpis is None:
            continue
        for metric in METRIC_LABEL:
            value = getattr(ev.kpis, metric)
            if value is None:
                continue
            evidence.append(Evidence(
                id=f"E{len(evidence) + 1:02d}", incident_id=incident.id, plan_id=ev.plan_id, metric=metric,
                value=value, unit=UNIT[metric], display=show(metric, value),
                snapshot_version=ev.snapshot_version, policy_version=ev.policy_version, engine_version=ev.engine_version,
            ))
    return evidence


class Citation(BaseModel):
    """模型對一個 [Exx] 標記的宣告:它說這是哪個方案、哪個指標、多少、什麼單位。"""

    evidence_id: str
    plan_id: str
    metric: str
    value: float
    unit: str


MARKER = re.compile(r"\[(E\d+)\]")
_PLAN = re.compile(r"P\d+")
# 編號與時刻裡的數字不是指標:方案、工單、機台、事件、快照版本、D1 20:00
_IDS = re.compile(r"\[E\d+\]|\b(?:P|E|v)\d+\b|\b(?:WO|IM|INC)-[\w-]+|D\d+ \d{2}:\d{2}")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "").lstrip("0") or "0" for m in _NUMBER.findall(text)}


def unmarked_metric_numbers(text: str, evidence: list[Evidence]) -> list[str]:
    """沒加標記、但和某個方案指標相同的數字。這種數字無法核對是哪個方案的,一律要求改用標記。"""
    metric_numbers = set().union(*(_numbers(f"{e.value:g} {e.display}") for e in evidence)) if evidence else set()
    return sorted(_numbers(_IDS.sub(" ", text)) & metric_numbers)


def check_text(text: str, citations: list[Citation], evidence: list[Evidence]) -> list[str]:
    """一段說明裡的引用問題;空清單表示每個標記都對得回同方案、同指標、同值、同單位的證據。"""
    known = {e.id: e for e in evidence}
    declared = {c.evidence_id: c for c in citations}
    problems = []
    for match in MARKER.finditer(text):
        marker = match.group(1)
        e, c = known.get(marker), declared.get(marker)
        if e is None:
            problems.append(f"[{marker}] 不是證據表裡的編號")
            continue
        if c is None:
            problems.append(f"[{marker}] 沒有列在 citations")
            continue
        if (c.plan_id, c.metric, c.unit) != (e.plan_id, e.metric, e.unit) or abs(c.value - e.value) > 1e-6:
            problems.append(f"[{marker}] 是 {e.label} {e.value:g} {e.unit},宣告卻是 {c.plan_id} {c.metric} {c.value:g} {c.unit}")
            continue
        named = _PLAN.findall(text[: match.start()])
        if not named or named[-1] != e.plan_id:
            problems.append(f"[{marker}] 是 {e.plan_id} 的數字,句子卻放在 {named[-1] if named else '沒寫方案'} 後面")
    if bare := unmarked_metric_numbers(text, evidence):
        problems.append(f"方案指標數字 {'、'.join(bare)} 要用證據標記,不可直接寫")
    return problems
