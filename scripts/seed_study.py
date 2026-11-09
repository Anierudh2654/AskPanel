import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Task


TASK_DIR = Path(__file__).parent / "tasks"


async def seed() -> None:
    files = sorted(TASK_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"No researcher-authored task JSON files found in {TASK_DIR}")
    async with SessionLocal() as session:
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data if isinstance(data, list) else [data]:
                task = await session.scalar(select(Task).where(Task.slug == item["slug"]))
                if task is None:
                    task = Task(slug=item["slug"])
                    session.add(task)
                task.title = item["title"]
                task.kind = item["kind"]
                task.material_md = item["material_md"]
                task.quiz = item.get("quiz", [])
                task.time_limit_s = int(item.get("time_limit_s", 900))
                if task.version is None:
                    task.version = 1
                await session.flush()
                print(f"{task.slug}\t{task.id}\tv{task.version}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
