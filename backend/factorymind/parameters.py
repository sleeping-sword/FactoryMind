"""製程參數出處表:示範情境用到的射出限制,各自的值、來源與確認狀態。

全部是合成假設,沒有經過製程專業覆核;拿到業師或廠內確認後才能改狀態,不能自行標成已確認。
"""

from .models import FactorySnapshot

SOURCE = "合成假設(示範情境編寫)"
STATUS = "未經製程覆核"


def _row(name: str, target: str, value: str, unit: str, scope: str) -> dict:
    return {"name": name, "target": target, "value": value, "unit": unit, "source": SOURCE,
            "scope": scope, "assumption": True, "status": STATUS}


def process_parameters(snapshot: FactorySnapshot) -> list[dict]:
    rows = []
    for mold in snapshot.molds:
        rows.append(_row("鎖模力範圍", mold.id, f"{mold.min_tonnage}–{mold.max_tonnage}", "噸", "模具可上的機台噸數"))
        rows.append(_row(
            "相容機台清單", mold.id, "、".join(mold.fits_machines) if mold.fits_machines is not None else "資料不足", "",
            "噸數之外的尺寸、繫桿間距與噴嘴" + ("(MD-F 寬度超過 IM-04 繫桿間距)" if mold.id == "MD-F" else ""),
        ))
    for product in snapshot.products:
        if product.approved_machines is not None:
            rows.append(_row("客戶認可機台", product.id, "、".join(product.approved_machines), "", f"{product.name},只能在這些機台生產"))
        if product.proven_machines is not None:
            rows.append(_row("已試模機台", product.id, "、".join(product.proven_machines), "", f"{product.name},其他機台第一次生產要試模"))
    times = snapshot.setup_times
    rows += [
        _row("換模時間", "全廠", str(times.mold_change_min), "分鐘", "換模具,含清料與換色"),
        _row("換料時間", "全廠", str(times.material_change_min), "分鐘", "同模具換塑料"),
        _row("換深色時間", "全廠", str(times.color_darker_min), "分鐘", "同模同料,由淺換深"),
        _row("換淺色時間", "全廠", str(times.color_lighter_min), "分鐘", "同模同料,由深換淺"),
        _row("試模／首件時間", "全廠", str(times.trial_run_min), "分鐘", "產品第一次上沒試過的機台;費用與換模同費率、分開列"),
        _row("換模費率", "全廠", f"{snapshot.cost_rates.setup_ntd_per_min:g}", "NT$/分鐘", "換模與試模工時"),
    ]
    rows += [_row("每小時產量", p.id, str(p.units_per_hour), "個/小時", f"{p.name},良品率視為 100%") for p in snapshot.products]
    for row in rows:
        row["version"] = f"快照 v{snapshot.version}"
    return rows
