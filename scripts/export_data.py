import argparse
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Event, LLMCall, StudySession


MODELS = {"sessions": StudySession, "events": Event, "llm_calls": LLMCall}
EXCLUDED_PROMPT_FIELDS = {"assembled_prompt", "response_text"}


async def export(dataset: str, output: Path) -> None:
    model = MODELS[dataset]
    async with SessionLocal() as session:
        rows = (await session.scalars(select(model))).all()
        fields = [column.name for column in model.__table__.columns if column.name not in EXCLUDED_PROMPT_FIELDS]
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: str(getattr(row, field)) if getattr(row, field) is not None else "" for field in fields})
    print(f"Exported {len(rows)} {dataset} rows to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export AskPanel study data without raw prompts")
    parser.add_argument("dataset", choices=sorted(MODELS))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    target = args.output or Path(f"askpanel-{args.dataset}.csv")
    asyncio.run(export(args.dataset, target))
