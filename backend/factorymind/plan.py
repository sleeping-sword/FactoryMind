"""候選方案與評估結果的資料契約。"""

from enum import Enum

from pydantic import Field

from .models import Model


class OvertimeGrant(Model):
    machine_id: str
    day: int = Field(ge=0, description="第幾天,0 為快照當天")
    minutes: int = Field(gt=0, description="下班後延長的分鐘數")


class Plan(Model):
    """候選方案:各機台的工單順序、加班與外包。時間由試跑決定,不由方案指定。"""

    id: str
    label: str
    machine_sequences: dict[str, list[str]]
    overtime: list[OvertimeGrant] = []
    outsourced: list[str] = []
    assumptions: list[str] = []


class Operation(Model):
    work_order_id: str
    machine_id: str
    start_min: int = Field(description="含換模的開始時間")
    setup_min: int
    end_min: int
    mold_changed: bool


class Violation(Model):
    code: str
    message: str
    subjects: list[str] = []


class Kpis(Model):
    strategic_tardiness_min: int
    total_tardiness_min: int
    late_work_orders: list[str]
    changed_work_orders: int | None = Field(description="相對原排程改動的既有工單數;無對照排程時為 None")
    cost_ntd: float
    cost_breakdown_ntd: dict[str, float]
    mold_changes: int
    setup_min: int
    machine_load: dict[str, float] = Field(description="各可用機台:工作分鐘 / 正常班可用分鐘")


class EvaluationStatus(str, Enum):
    OK = "ok"
    INVALID_PLAN = "invalid_plan"


class Evaluation(Model):
    """四個判定分開呈現:計算是否成功、是否可行、是否通過硬規則;授權不在此判定。"""

    plan_id: str
    factory_id: str
    snapshot_version: int
    policy_version: str
    engine_version: str
    status: EvaluationStatus
    errors: list[str] = []
    feasible: bool | None = None
    infeasible_reasons: list[str] = []
    violations: list[Violation] | None = None
    kpis: Kpis | None = None
    operations: list[Operation] = []
    outsourced: list[str] = []

    @property
    def eligible(self) -> bool:
        """計算成功、可行且無硬規則違反,才可進入方案排序。"""
        return self.status == EvaluationStatus.OK and self.feasible is True and self.violations == []
