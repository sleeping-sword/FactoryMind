"""合成的塑膠射出廠與示範政策。全部為合成資料,不是實測值。"""

from ..models import (
    CostRates,
    Customer,
    CustomerTier,
    FactorySnapshot,
    Machine,
    Material,
    Mold,
    Product,
    SetupTimes,
    ShiftCalendar,
    WorkOrder,
    WorkOrderStatus,
)
from ..policy import HardLimits, Policy, RankingCriterion

DAY = 1440
SHIFT_END = 20 * 60


def due(day: int) -> int:
    """第 day 天下班時交貨。"""
    return day * DAY + SHIFT_END


def demo_snapshot() -> FactorySnapshot:
    machines = [
        Machine(id="IM-01", role="小型機:按鍵、小外殼等小件", tonnage=80, current_mold_id="MD-E", current_material_id="PP", current_color_shade=1),
        Machine(id="IM-02", role="中小型機:外殼、上蓋、醫療盒", tonnage=120, current_mold_id="MD-B", current_material_id="PC", current_color_shade=0),
        Machine(id="IM-03", role="中小型機:外殼、上蓋、醫療盒", tonnage=120, current_mold_id="MD-G", current_material_id="ABS", current_color_shade=1),
        Machine(id="IM-04", role="中大型機:車用飾板、上蓋、醫療盒", tonnage=200, current_mold_id="MD-D", current_material_id="ABS", current_color_shade=5),
        Machine(id="IM-05", role="中型機:外殼、車用飾板、上蓋", tonnage=150, current_mold_id="MD-A", current_material_id="ABS", current_color_shade=1),
        Machine(id="IM-06", role="大型機:車用儀表框等大件", tonnage=300, current_mold_id="MD-F", current_material_id="PC", current_color_shade=8),
    ]
    molds = [
        Mold(id="MD-A", min_tonnage=80, max_tonnage=150),
        Mold(id="MD-B", min_tonnage=100, max_tonnage=200),
        Mold(id="MD-C", min_tonnage=100, max_tonnage=200),
        Mold(id="MD-D", min_tonnage=150, max_tonnage=300),
        Mold(id="MD-E", min_tonnage=60, max_tonnage=120),
        Mold(id="MD-F", min_tonnage=200, max_tonnage=300),
        Mold(id="MD-G", min_tonnage=100, max_tonnage=150),
    ]
    materials = [
        Material(id="ABS", on_hand_kg=2500),
        Material(id="PC", on_hand_kg=1500),
        Material(id="PP", on_hand_kg=1200),
    ]
    products = [
        Product(id="A-WHT", name="電子產品外殼(白)", mold_id="MD-A", material_id="ABS", color_shade=1, units_per_hour=400, material_kg_per_unit=0.05, outsource_unit_cost_ntd=9),
        Product(id="A-BLK", name="電子產品外殼(黑)", mold_id="MD-A", material_id="ABS", color_shade=8, units_per_hour=400, material_kg_per_unit=0.05, outsource_unit_cost_ntd=9),
        Product(id="B-CLR", name="透明上蓋", mold_id="MD-B", material_id="PC", color_shade=0, units_per_hour=300, material_kg_per_unit=0.08, outsource_unit_cost_ntd=14),
        Product(id="C-MED", name="醫療耗材盒", mold_id="MD-C", material_id="PP", color_shade=0, units_per_hour=500, material_kg_per_unit=0.03, outsource_unit_cost_ntd=11),
        Product(id="D-GRY", name="車用內裝飾板(灰)", mold_id="MD-D", material_id="ABS", color_shade=5, units_per_hour=200, material_kg_per_unit=0.15, outsource_unit_cost_ntd=26),
        Product(id="D-BLK", name="車用內裝飾板(黑)", mold_id="MD-D", material_id="ABS", color_shade=8, units_per_hour=200, material_kg_per_unit=0.15, outsource_unit_cost_ntd=26),
        Product(id="E-WHT", name="按鍵(白)", mold_id="MD-E", material_id="PP", color_shade=1, units_per_hour=600, material_kg_per_unit=0.02, outsource_unit_cost_ntd=5),
        Product(id="E-BLU", name="按鍵(藍)", mold_id="MD-E", material_id="PP", color_shade=5, units_per_hour=600, material_kg_per_unit=0.02, outsource_unit_cost_ntd=5),
        Product(id="F-BLK", name="車用儀表框(黑)", mold_id="MD-F", material_id="PC", color_shade=8, units_per_hour=150, material_kg_per_unit=0.25),
        Product(id="G-WHT", name="充電座外殼(白)", mold_id="MD-G", material_id="ABS", color_shade=1, units_per_hour=350, material_kg_per_unit=0.06, outsource_unit_cost_ntd=10),
    ]
    customers = [
        Customer(id="C-A", name="策略客戶 A(醫療)", tier=CustomerTier.STRATEGIC),
        Customer(id="C-B", name="一般客戶 B(消費電子)", tier=CustomerTier.NORMAL),
        Customer(id="C-C", name="一般客戶 C(車用)", tier=CustomerTier.NORMAL),
    ]

    def wo(id, customer, product, quantity, due_day, **extra):
        return WorkOrder(id=id, customer_id=customer, product_id=product, quantity=quantity, due_min=due(due_day), **extra)

    work_orders = [
        wo("WO-01", "C-B", "B-CLR", 2400, 0, status=WorkOrderStatus.IN_PROGRESS, quantity_done=900, machine_id="IM-02"),
        wo("WO-02", "C-C", "D-GRY", 1600, 0, status=WorkOrderStatus.IN_PROGRESS, quantity_done=400, machine_id="IM-04"),
        wo("WO-03", "C-B", "E-WHT", 3600, 0),
        wo("WO-04", "C-B", "G-WHT", 2100, 0),
        wo("WO-05", "C-B", "A-WHT", 2400, 1),
        wo("WO-06", "C-C", "F-BLK", 1050, 1),
        wo("WO-07", "C-A", "C-MED", 3500, 1, max_delay_min=0),
        wo("WO-08", "C-B", "A-BLK", 2800, 2),
        wo("WO-09", "C-C", "D-BLK", 1400, 1),
        wo("WO-10", "C-B", "E-BLU", 4200, 1),
        wo("WO-11", "C-B", "B-CLR", 2100, 2),
        wo("WO-12", "C-B", "G-WHT", 2450, 2),
        wo("WO-13", "C-C", "F-BLK", 1200, 2),
        wo("WO-14", "C-C", "D-GRY", 1600, 3),
        wo("WO-15", "C-B", "A-WHT", 3200, 3),
        wo("WO-16", "C-A", "C-MED", 4000, 3),
        wo("WO-17", "C-B", "E-WHT", 4800, 3),
        wo("WO-18", "C-B", "B-CLR", 2400, 4),
    ]
    return FactorySnapshot(
        factory_id="demo-injection-plant",
        version=1,
        taken_at="D0 00:00",
        source="synthetic",
        machines=machines,
        molds=molds,
        materials=materials,
        products=products,
        customers=customers,
        work_orders=work_orders,
        calendar=ShiftCalendar(day_start_min=8 * 60, day_end_min=SHIFT_END, horizon_days=5),
        setup_times=SetupTimes(mold_change_min=120, material_change_min=45, color_darker_min=15, color_lighter_min=40),
        cost_rates=CostRates(overtime_ntd_per_min=60, setup_ntd_per_min=20),
        outsource_lead_time_min=2 * DAY + 12 * 60,
    )


def demo_rush_order() -> WorkOrder:
    """策略客戶的隔日醫療件急單。"""
    return WorkOrder(
        id="WO-R1", customer_id="C-A", product_id="C-MED", quantity=3500, due_min=due(1), is_rush=True
    )


def demo_policy() -> Policy:
    return Policy(
        version="demo-policy-v1",
        hard_limits=HardLimits(max_overtime_min_per_machine_day=240, overtime_budget_ntd=30_000),
        ranking=[
            RankingCriterion(metric="strategic_tardiness_min"),
            RankingCriterion(metric="total_tardiness_min", threshold=60),
            RankingCriterion(metric="changed_work_orders", threshold=1),
            RankingCriterion(metric="cost_ntd"),
        ],
    )
