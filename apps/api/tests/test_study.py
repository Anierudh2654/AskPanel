import uuid

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_consent_then_server_assigned_study_conversation(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "test-admin-password")
    with TestClient(app) as client:
        admin = client.post("/api/auth/admin", json={"password": "test-admin-password"})
        assert admin.status_code == 200
        code = f"study-{uuid.uuid4()}"
        assert client.post("/api/admin/participants/import", json={"codes": [code]}).status_code == 200
        task_ids = []
        for index in range(3):
            task = client.post("/api/admin/tasks", json={
                "slug": f"{code}-{index}",
                "title": f"Study task {index}",
                "kind": "paper_section",
                "material_md": f"Reference material {index}",
                "quiz": [],
                "time_limit_s": 600,
            })
            assert task.status_code == 200
            task_ids.append(task.json()["id"])
        assigned = client.post("/api/admin/study/assign", json={"code": code, "task_ids": task_ids, "order_group": "ABC"})
        assert assigned.status_code == 200
        first_session = assigned.json()["sessions"][0]
        client.post("/api/auth/logout")
        assert client.post("/api/auth/participant", json={"code": code}).status_code == 200
        assert client.post(f"/api/study/session/{first_session['id']}/conversation").status_code == 404
        consent = client.post("/api/study/consent", json={"accepted": True, "demographics": {}})
        assert consent.status_code == 200
        state = client.get("/api/study/state").json()
        assert state["consented"] is True
        assert len(state["sessions"]) == 3
        assert client.post(f"/api/study/session/{first_session['id']}/start").status_code == 200
        conversation = client.post(f"/api/study/session/{first_session['id']}/conversation")
        assert conversation.status_code == 200
        assert conversation.json()["condition"] == "A"
        detail = client.get(f"/api/conversations/{conversation.json()['id']}").json()
        assert detail["system_context"] == "Reference material 0"