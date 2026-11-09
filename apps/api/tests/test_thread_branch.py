import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def _events(text: str) -> set[str]:
    return {line.removeprefix("event: ").strip() for line in text.splitlines() if line.startswith("event:")}


@pytest.mark.parametrize("condition", ["A", "C"])
def test_askpanel_branch_does_not_append_to_main_transcript(monkeypatch, condition):
    monkeypatch.setattr(settings, "llm_provider", "mock")
    with TestClient(app) as client:
        login = client.post("/api/auth/device", json={"device_id": str(uuid.uuid4())})
        assert login.status_code == 200
        conversation = client.post("/api/conversations", json={"title": "Branch test", "condition": condition}).json()
        conversation_id = conversation["id"]
        main_response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Explain a concept."},
        )
        assert main_response.status_code == 200
        main_history = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
        parent = main_history[-1]
        anchor_text = parent["content"][:20]

        branch_response = client.post("/api/threads", json={
            "message_id": parent["id"],
            "anchor": {"text": anchor_text, "start": 0, "end": len(anchor_text), "prefix": "", "suffix": ""},
            "content": "Clarify this selected text.",
        })
        assert branch_response.status_code == 200
        assert "thread_created" in _events(branch_response.text)
        assert "done" in _events(branch_response.text)

        after_branch = client.get(f"/api/conversations/{conversation_id}").json()
        assert [message["id"] for message in after_branch["messages"]] == [message["id"] for message in main_history]
        assert len(after_branch["threads"]) == 1
        thread = client.get(f"/api/threads/{after_branch['threads'][0]['id']}").json()
        assert [message["role"] for message in thread["messages"]] == ["user", "assistant"]
