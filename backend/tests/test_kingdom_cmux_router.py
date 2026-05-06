from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import kingdom


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(kingdom.router)
    return TestClient(app)


def test_cmux_status_returns_data_wrapper(monkeypatch):
    async def fake_status():
        return {"1": {"state": "IDLE", "model": "claude", "lastUpdate": None}}

    monkeypatch.setattr(kingdom, "_get_surface_status", fake_status)

    response = make_client().get("/api/kingdom/cmux/status")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["1"]["state"] == "IDLE"
    assert body["surfaces"] == body["data"]


def test_cmux_preview_returns_intent_plan():
    response = make_client().post("/api/kingdom/cmux/preview", json={"prompt": "배포 후 검증"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["riskLevel"] == "high"
    assert "health-check" in body["data"]["tools"]


def test_cmux_dispatch_wraps_task_result(monkeypatch):
    calls = {}

    async def fake_update(surface, state, model):
        calls["update"] = (surface, state, model)

    async def fake_broadcast(event, data):
        calls.setdefault("events", []).append((event, data))

    def fake_dispatch(surface, prompt):
        calls["dispatch"] = (surface, prompt)
        return True

    monkeypatch.setattr(kingdom, "_update_surface_state", fake_update)
    monkeypatch.setattr(kingdom, "_broadcast_sse", fake_broadcast)
    monkeypatch.setattr(kingdom, "_dispatch_prompt_to_surface", fake_dispatch)

    response = make_client().post(
        "/api/kingdom/cmux/dispatch",
        json={"prompt": "상태 확인", "surface": 2, "model": "minimax"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "dispatched"
    assert calls["update"] == ("2", "WORKING", "minimax")
    assert calls["dispatch"] == ("2", "상태 확인")


def test_cmux_dispatch_rejects_empty_prompt():
    response = make_client().post("/api/kingdom/cmux/dispatch", json={"prompt": ""})

    assert response.status_code == 400
    assert response.json()["error"] == "prompt is required"
