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


def test_parameter_sources_are_served():
    rows = TestClient(api.app).get("/api/parameters").json()
    assert any(r["name"] == "客戶認可機台" and r["target"] == "C-MED" for r in rows)


def test_approval_over_http_checks_screen_versions_and_dedupes():
    api.factory.reset()
    client = TestClient(api.app)
    incident_id = client.post("/api/factory/emit").json()["id"]
    api.factory.process(incident_id)
    detail = client.get(f"/api/incidents/{incident_id}").json()
    seen = {"snapshot_version": detail["snapshot"]["version"], "schedule_version": detail["schedule_version"],
            "policy_version": detail["policy_version"]}
    body = {"plan_id": detail["ranking"]["winner"], "request_id": "r-1"}

    stale = client.post(f"/api/incidents/{incident_id}/approve", json={**body, "expected": {**seen, "schedule_version": 0}})
    assert stale.status_code == 409 and "重新整理" in stale.json()["detail"]
    assert client.get(f"/api/incidents/{incident_id}").json()["status"] == "awaiting_approval"

    first = client.post(f"/api/incidents/{incident_id}/approve", json={**body, "expected": seen})
    again = client.post(f"/api/incidents/{incident_id}/approve", json={**body, "expected": seen})
    assert first.status_code == again.status_code == 200
    assert client.get("/api/state").json()["schedule"]["version"] == 2
    api.factory.reset()
