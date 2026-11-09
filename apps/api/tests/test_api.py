import uuid

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_participant_chat_stream_persists_messages_and_call(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "admin_password", "test-admin-password")
    with TestClient(app) as client:
        code = f"test-{uuid.uuid4()}"
        assert client.post("/api/auth/participant", json={"code": code}).status_code == 401
        admin_login = client.post("/api/auth/admin", json={"password": "test-admin-password"})
        assert admin_login.status_code == 200
        imported = client.post("/api/admin/participants/import", json={"codes": [code]})
        assert imported.status_code == 200
        client.post("/api/auth/logout")
        login = client.post("/api/auth/participant", json={"code": code})
        assert login.status_code == 200
        created = client.post("/api/conversations", json={"condition": "A"})
        assert created.status_code == 200
        conversation_id = created.json()["id"]
        response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Explain one useful idea."},
        )
        assert response.status_code == 200
        assert "event: meta" in response.text
        assert "event: delta" in response.text
        assert "event: done" in response.text
        history = client.get(f"/api/conversations/{conversation_id}")
        assert history.status_code == 200
        data = history.json()
        assert [message["role"] for message in data["messages"]] == ["user", "assistant"]
        assert data["messages"][1]["status"] == "complete"
        assert "local development response" in data["messages"][1]["content"]
        first_user_id = data["messages"][0]["id"]
        follow_up = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Add one more detail."},
        )
        assert follow_up.status_code == 200
        edited = client.put(f"/api/messages/{first_user_id}", json={"content": "Explain the central idea instead."})
        assert edited.status_code == 200
        assert "event: truncated" in edited.text
        final_history = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
        assert [message["role"] for message in final_history] == ["user", "assistant"]
        assert final_history[0]["content"] == "Explain the central idea instead."
        assert final_history[1]["status"] == "complete"
        client.post("/api/auth/logout")
        assert client.post("/api/auth/admin", json={"password": "test-admin-password"}).status_code == 200
        calls = client.get("/api/admin/llm-calls?include_prompt=true").json()
        main_call = next(
            call for call in calls
            if call["conversation_id"] == conversation_id
            and call["purpose"] == "main"
            and call["assembled_prompt"]["messages"][-1]["content"] == "Explain the central idea instead."
        )
        assert main_call["input_tokens"] > 0
        assert main_call["output_tokens"] > 0
        assert main_call["ttft_ms"] >= 0
        assert main_call["latency_ms"] >= main_call["ttft_ms"]
        assert main_call["prompt_version"] == "v1"
