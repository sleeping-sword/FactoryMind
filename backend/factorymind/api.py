"""決策介面用的 API。啟動:uvicorn factorymind.api:app --port 8000

虛擬工廠由背景執行緒驅動:運轉中且沒有待決策事件時,會自動發出下一個擾動事件。
"""

import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .governance import GovernanceError, Incident, VirtualFactory

load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # backend/.env:AI 金鑰等設定

factory = VirtualFactory(
    first_event_delay_s=float(os.environ.get("FACTORYMIND_FIRST_EVENT_S", 5)),
    next_event_delay_s=float(os.environ.get("FACTORYMIND_NEXT_EVENT_S", 30)),
)


def _process_in_background(incident: Incident) -> None:
    threading.Thread(target=factory.process, args=(incident.id,), daemon=True).start()


def _auto_events(stop: threading.Event) -> None:
    while not stop.wait(1):
        incident = factory.tick(time.monotonic())
        if incident:
            _process_in_background(incident)


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    threading.Thread(target=_auto_events, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="FactoryMind", lifespan=lifespan)


class Actor(BaseModel):
    actor: str = "生產主管"


class Approval(Actor):
    plan_id: str


class Rejection(Actor):
    reason: str = ""


class Question(Actor):
    question: str


def _guard(action):
    try:
        return action()
    except GovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/state")
def get_state() -> dict:
    return factory.state()


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict:
    return _guard(lambda: factory.incident(incident_id))


@app.post("/api/factory/start")
def start() -> dict:
    factory.start(time.monotonic())
    return factory.state()


@app.post("/api/factory/pause")
def pause() -> dict:
    factory.pause()
    return factory.state()


@app.post("/api/factory/reset")
def reset() -> dict:
    factory.reset()
    return factory.state()


@app.post("/api/factory/emit")
def emit() -> dict:
    incident = _guard(factory.emit_next)
    _process_in_background(incident)
    return {"id": incident.id}


@app.post("/api/incidents/{incident_id}/approve")
def approve(incident_id: str, body: Approval) -> dict:
    return _guard(lambda: factory.approve(incident_id, body.plan_id, body.actor)).model_dump(mode="json")


@app.post("/api/incidents/{incident_id}/reject")
def reject(incident_id: str, body: Rejection) -> dict:
    return _guard(lambda: factory.reject(incident_id, body.actor, body.reason)).model_dump(mode="json")


@app.post("/api/incidents/{incident_id}/reassess")
def reassess(incident_id: str, body: Actor) -> dict:
    incident = _guard(lambda: factory.reassess(incident_id, body.actor))
    _process_in_background(incident)
    return {"id": incident.id}


@app.post("/api/incidents/{incident_id}/tasks/{task_id}/confirm")
def confirm_task(incident_id: str, task_id: str, body: Actor) -> dict:
    body.actor = body.actor if body.actor != "生產主管" else "維修確認者"
    return _guard(lambda: factory.confirm_task(incident_id, task_id, body.actor)).model_dump(mode="json")


@app.post("/api/incidents/{incident_id}/ask")
def ask(incident_id: str, body: Question) -> dict:
    return _guard(lambda: factory.ask(incident_id, body.question, body.actor)).model_dump(mode="json")
