import asyncio
import json
import os
import uuid

import httpx


API = os.environ.get("ASKPANEL_API_URL", "http://localhost:8000")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def parse_events(text: str) -> list[tuple[str, dict]]:
    parsed = []
    for frame in text.split("\n\n"):
        event = "message"
        data = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if data:
            parsed.append((event, json.loads(data)))
    return parsed


async def simulate() -> None:
    if not ADMIN_PASSWORD:
        raise SystemExit("Set ADMIN_PASSWORD in the environment before running this synthetic smoke test")
    code = f"synthetic-{uuid.uuid4()}"
    async with httpx.AsyncClient(base_url=API, timeout=90) as client:
        response = await client.post("/api/auth/admin", json={"password": ADMIN_PASSWORD})
        response.raise_for_status()
        response = await client.post("/api/admin/participants/import", json={"codes": [code], "synthetic": True})
        response.raise_for_status()
        response = await client.post("/api/auth/logout")
        response.raise_for_status()
        response = await client.post("/api/auth/participant", json={"code": code})
        response.raise_for_status()
        response = await client.post("/api/study/consent", json={"accepted": True, "demographics": {}})
        response.raise_for_status()
        for condition in ("A", "B", "C"):
            conversation = (await client.post("/api/conversations", json={"title": f"Synthetic {condition}", "condition": condition})).raise_for_status().json()
            response = await client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "Explain the key idea in one sentence."})
            response.raise_for_status()
            events = parse_events(response.text)
            if not any(event == "done" for event, _ in events):
                raise RuntimeError(f"Condition {condition} did not complete its main generation")
            print(f"{condition}: streamed main response")
            if condition == "B":
                history = (await client.get(f"/api/conversations/{conversation['id']}")).raise_for_status().json()
                parent = next(message for message in history["messages"] if message["role"] == "assistant")
                selected = parent["content"][: min(20, len(parent["content"]))]
                thread = await client.post("/api/threads", json={
                    "message_id": parent["id"],
                    "anchor": {"text": selected, "start": 0, "end": len(selected), "prefix": "", "suffix": ""},
                    "content": "Could you clarify this?",
                })
                thread.raise_for_status()
                if not any(event == "done" for event, _ in parse_events(thread.text)):
                    raise RuntimeError("Condition B thread generation did not complete")
                print("B: streamed side-thread response")
            if condition == "C":
                history = (await client.get(f"/api/conversations/{conversation['id']}")).raise_for_status().json()
                parent = next(message for message in history["messages"] if message["role"] == "assistant")
                quote = {"text": parent["content"][:15], "source_message_id": parent["id"], "start": 0, "end": min(15, len(parent["content"]))}
                response = await client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "What does this mean?", "quote": quote})
                response.raise_for_status()
                if not any(event == "done" for event, _ in parse_events(response.text)):
                    raise RuntimeError("Condition C quote-reply generation did not complete")
                print("C: streamed quote-reply")


if __name__ == "__main__":
    asyncio.run(simulate())
