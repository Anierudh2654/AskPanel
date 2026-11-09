import uuid

from fastapi.testclient import TestClient

from app.main import app


def test_device_login_reuses_browser_identity():
    device_id = str(uuid.uuid4())
    with TestClient(app) as client:
        first = client.post("/api/auth/device", json={"device_id": device_id})
        assert first.status_code == 200
        first_identity = first.json()
        assert first_identity["is_synthetic"] is False
        assert first_identity["role"] == "participant"

        client.post("/api/auth/logout")
        second = client.post("/api/auth/device", json={"device_id": device_id})
        assert second.status_code == 200
        assert second.json()["id"] == first_identity["id"]
        assert client.get("/api/auth/me").json()["id"] == first_identity["id"]
