# Researcher-authored task files

Place task JSON files in this directory before using `python scripts/seed_study.py`. The loader accepts either a single task object or a JSON array in each file:

```json
{
  "slug": "paper-v1-a",
  "title": "Paper section A",
  "kind": "paper_section",
  "material_md": "Researcher-authored reading material",
  "quiz": [
    {"id": "q1", "type": "mcq", "prompt": "Question text", "options": ["A", "B"], "answer": "A"},
    {"id": "q2", "type": "free_text", "prompt": "Explain...", "answer": "Reference answer"}
  ],
  "time_limit_s": 900
}
```

Prepare parallel versions and check answer keys before collecting study data. Do not use the example text as real study material.
