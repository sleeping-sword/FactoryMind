"""工廠快照:某一時刻的工廠資料副本,所有候選方案的共同起點。"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MIN_PER_DAY = 1440


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MachineStatus(str, Enum):
    UP = "up"
    DOWN = "down"


class CustomerTier(str, Enum):
    STRATEGIC = "strategic"
    NORMAL = "normal"


class WorkOrderStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Machine(Model):
    id: str
    role: str = Field(default="", description="這台機台適合做什麼,給人看的說明")
    tonnage: int = Field(gt=0, description="鎖模力(噸)")
    status: MachineStatus = MachineStatus.UP
    # 目前機上的模具、塑料與顏色,決定下一張工單的換模時間
    current_mold_id: str | None = None
    current_material_id: str | None = None
    current_color_shade: int | None = None


class Mold(Model):
    id: str
    min_tonnage: int = Field(gt=0)
    max_tonnage: int = Field(gt=0)
    # 噸數之外,模具尺寸、繫桿間距、噴嘴等也要合;None 表示沒有這份資料,不默認可上
    fits_machines: list[str] | None = Field(default=None, description="相容機台清單")


class Material(Model):
    id: str
    on_hand_kg: float = Field(ge=0)


class Product(Model):
    id: str
    name: str = Field(default="", description="品名,給人看的說明")
    mold_id: str
    material_id: str
    color_shade: int = Field(ge=0, le=9, description="0 最淺、9 最深")
    units_per_hour: int = Field(gt=0)
    material_kg_per_unit: float = Field(gt=0)
    outsource_unit_cost_ntd: float | None = Field(default=None, description="None 表示不可外包")
    approved_machines: list[str] | None = Field(default=None, description="客戶認可機台;None 表示客戶沒有指定")
    proven_machines: list[str] | None = Field(
        default=None, description="已試模、首件確認過的機台;其他機台第一次生產要加試模時間。None 表示不追蹤"
    )


class Customer(Model):
    id: str
    name: str
    tier: CustomerTier


class WorkOrder(Model):
    """一張訂單對應一張工單(射出為單一工序)。"""

    id: str
    customer_id: str
    product_id: str
    quantity: int = Field(gt=0)
    due_min: int = Field(description="承諾交期")
    status: WorkOrderStatus = WorkOrderStatus.NOT_STARTED
    quantity_done: int = Field(default=0, ge=0)
    machine_id: str | None = Field(default=None, description="已開工工單所在機台")
    is_rush: bool = False
    max_delay_min: int | None = Field(default=None, description="容許延誤上限;0 為不可延誤,None 為未設硬底線")

    @property
    def remaining(self) -> int:
        return self.quantity - self.quantity_done


class ShiftCalendar(Model):
    day_start_min: int = Field(ge=0, lt=MIN_PER_DAY)
    day_end_min: int = Field(gt=0, le=MIN_PER_DAY)
    horizon_days: int = Field(gt=0)


class SetupTimes(Model):
    """序列相依的換模時間(分鐘)。換模已包含清料與換色。"""

    mold_change_min: int = Field(ge=0)
    material_change_min: int = Field(ge=0)
    color_darker_min: int = Field(ge=0)
    color_lighter_min: int = Field(ge=0)
    trial_run_min: int = Field(default=0, ge=0, description="產品第一次上沒試過的機台:試模加首件確認")


class CostRates(Model):
    overtime_ntd_per_min: float = Field(ge=0)
    setup_ntd_per_min: float = Field(ge=0)


class FactorySnapshot(Model):
    factory_id: str
    version: int = Field(ge=1)
    taken_at: str = Field(description="快照對應的時間,即第 0 分鐘")
    source: str = Field(description="資料來源,例如 synthetic")
    machines: list[Machine]
    molds: list[Mold]
    materials: list[Material]
    products: list[Product]
    customers: list[Customer]
    work_orders: list[WorkOrder]
    calendar: ShiftCalendar
    setup_times: SetupTimes
    cost_rates: CostRates
    outsource_lead_time_min: int = Field(gt=0)

    def open_work_orders(self) -> list[WorkOrder]:
        return [w for w in self.work_orders if w.status != WorkOrderStatus.DONE]


def by_id(items):
    return {item.id: item for item in items}


def is_compatible(mold: Mold, machine: Machine) -> bool:
    return mold.min_tonnage <= machine.tonnage <= mold.max_tonnage


class Fit(Model):
    """產品能不能上這台機台:可行、不可行、資料不足三種,不把缺資料當成可行。"""

    state: Literal["ok", "blocked", "unknown"]
    code: str = ""
    reason: str = ""


def machine_fit(product: Product, mold: Mold, machine: Machine) -> Fit:
    if not is_compatible(mold, machine):
        return Fit(state="blocked", code="MOLD_MACHINE_INCOMPATIBLE",
                   reason=f"模具 {mold.id}({mold.min_tonnage}–{mold.max_tonnage} 噸)不可上 {machine.id}({machine.tonnage} 噸)")
    if mold.fits_machines is None:
        return Fit(state="unknown", code="FIT_DATA_MISSING",
                   reason=f"資料不足:模具 {mold.id} 沒有相容機台清單,請補模具尺寸與 {machine.id} 繫桿間距、噴嘴規格後再評估")
    if machine.id not in mold.fits_machines:
        return Fit(state="blocked", code="MOLD_NOT_ON_FIT_LIST",
                   reason=f"模具 {mold.id} 的相容機台清單不含 {machine.id}(噸數雖在範圍內)")
    if product.approved_machines is not None and machine.id not in product.approved_machines:
        return Fit(state="blocked", code="MACHINE_NOT_APPROVED",
                   reason=f"{product.name or product.id} 的客戶只認可 {'、'.join(product.approved_machines)},{machine.id} 不在其中")
    return Fit(state="ok")


def can_run(product: Product, mold: Mold, machine: Machine) -> bool:
    return machine_fit(product, mold, machine).state == "ok"
