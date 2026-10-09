from fastapi.testclient import TestClient

from factorymind import api


def test_decision_flow_over_http():
    api.factory.reset()
    client = TestClient(api.app)
    assert client.get("/api/state").json()["incidents"] == []

    incident_id = client.post("/api/factory/emit").json()["id"]
    api.factory.process(incident_id)  # 背景也會算一次;這裡同步算完以便檢查
    detail = client.get(f"/api/incidents/{incident_id}").json()
    assert detail["status"] == "awaiting_approval"

    blocked = next(e["plan_id"] for e in detail["evaluations"] if e["violations"])
    refused = client.post(f"/api/incidents/{incident_id}/approve", json={"plan_id": blocked})
    assert refused.status_code == 409 and "不能核准" in refused.json()["detail"]

    approved = client.post(f"/api/incidents/{incident_id}/approve", json={"plan_id": detail["ranking"]["winner"]})
    assert approved.json()["status"] == "applied_pending_tasks"
    closed = client.post(f"/api/incidents/{incident_id}/tasks/T1/confirm", json={})
    assert closed.json()["status"] == "closed"
    assert client.get("/api/state").json()["schedule"]["version"] == 2
    assert client.get("/api/incidents/NOPE").status_code == 409
    api.factory.reset()
