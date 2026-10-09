"""方案排序:硬規則先排除,再依企業設定的順位逐一比較。不使用綜合加權分數。"""

from pydantic import BaseModel

from .plan import Evaluation, EvaluationStatus
from .policy import RankingCriterion


class Elimination(BaseModel):
    plan_id: str
    stage: str
    reason: str
    value: float | None = None
    best: float | None = None
    threshold: float | None = None


class RankingResult(BaseModel):
    winner: str | None
    eliminated: list[Elimination]
    undecided_reason: str | None = None
    tie_break: str | None = None


def rank(evaluations: list[Evaluation], criteria: list[RankingCriterion]) -> RankingResult:
    """沒有合規方案或缺少比較所需指標時不選出方案,交由主管處理。"""
    eliminated: list[Elimination] = []
    remaining: list[Evaluation] = []
    for ev in evaluations:
        if ev.status != EvaluationStatus.OK:
            eliminated.append(Elimination(plan_id=ev.plan_id, stage="計算", reason=";".join(ev.errors)))
        elif not ev.feasible:
            eliminated.append(
                Elimination(plan_id=ev.plan_id, stage="可行性", reason=";".join(ev.infeasible_reasons))
            )
        elif ev.violations:
            eliminated.append(
                Elimination(plan_id=ev.plan_id, stage="硬規則", reason=";".join(v.message for v in ev.violations))
            )
        else:
            remaining.append(ev)

    if not remaining:
        return RankingResult(winner=None, eliminated=eliminated, undecided_reason="沒有合規且可行的方案")

    for criterion in criteria:
        values = {ev.plan_id: getattr(ev.kpis, criterion.metric) for ev in remaining}
        missing = [plan_id for plan_id, value in values.items() if value is None]
        if missing:
            return RankingResult(
                winner=None,
                eliminated=eliminated,
                undecided_reason=f"方案 {', '.join(missing)} 缺少指標 {criterion.metric},無法比較",
            )
        # 一律對照當前最佳值,不串連相鄰方案的小差異
        best = min(values.values())
        kept = []
        for ev in remaining:
            if values[ev.plan_id] - best <= criterion.threshold:
                kept.append(ev)
            else:
                eliminated.append(
                    Elimination(
                        plan_id=ev.plan_id,
                        stage=criterion.metric,
                        reason=f"{criterion.metric} 與最佳值的差距超過門檻",
                        value=values[ev.plan_id],
                        best=best,
                        threshold=criterion.threshold,
                    )
                )
        remaining = kept
        if len(remaining) == 1:
            break

    tie_break = None
    if len(remaining) > 1:
        tie_break = "各順位皆在門檻內,依候選方案的固定順序取第一個:" + ", ".join(ev.plan_id for ev in remaining)
    return RankingResult(winner=remaining[0].plan_id, eliminated=eliminated, tie_break=tie_break)
