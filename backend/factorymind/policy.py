"""企業政策:硬底線與方案排序。由有權人員設定,帶版本。"""

from typing import Literal

from pydantic import Field

from .models import Model

Metric = Literal["strategic_tardiness_min", "total_tardiness_min", "changed_work_orders", "cost_ntd"]


class HardLimits(Model):
    max_overtime_min_per_machine_day: int = Field(ge=0)
    overtime_budget_ntd: float = Field(ge=0)


class RankingCriterion(Model):
    """一個比較順位。指標越小越好;差異在門檻內視為同等,交給下一順位。"""

    metric: Metric
    threshold: float = Field(default=0, ge=0)


class Policy(Model):
    version: str
    hard_limits: HardLimits
    ranking: list[RankingCriterion]
