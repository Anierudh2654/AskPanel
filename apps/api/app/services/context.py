from dataclasses import dataclass
import math
import re
from typing import Any, Iterable

from app.config import settings
from app.services.prompts import THREAD_FRAMING_V1, base_system
from app.services.llm.embeddings import embed_texts


@dataclass(frozen=True)
class Prompt:
    system: str
    messages: list[dict[str, str]]


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


_STOP_WORDS = {
    "about", "after", "again", "also", "because", "before", "being", "between",
    "could", "does", "from", "have", "into", "just", "more", "most", "other",
    "over", "same", "should", "some", "such", "than", "that", "their", "them",
    "then", "there", "these", "they", "this", "through", "under", "very", "what",
    "when", "where", "which", "while", "with", "would", "your", "you", "the",
    "and", "for", "are", "was", "were", "will", "how", "why", "who", "whose",
}
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)


def estimate_tokens(text: str) -> int:
    """A conservative rough estimate; provider-reported usage remains authoritative."""
    return max(1, (len(text) + 3) // 4) if text else 0


def _terms(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text) if len(token) > 2 and token.lower() not in _STOP_WORDS}


def _turn_groups(rows: list[Any]) -> list[list[Any]]:
    groups: list[list[Any]] = []
    for row in rows:
        role = _get(row, "role")
        if role == "user" or not groups:
            groups.append([row])
        else:
            groups[-1].append(row)
    return groups


def select_relevant_messages(
    rows: list[Any],
    *,
    query: str,
    required_seq: int,
    token_budget: int,
) -> list[Any]:
    """Keep the parent turn and rank earlier complete turns by lexical relevance."""
    groups = _turn_groups(rows)
    parent_group_index = next(
        (index for index, group in enumerate(groups) if any(_get(row, "seq") == required_seq for row in group)),
        None,
    )
    if parent_group_index is None:
        return rows

    query_terms = _terms(query)
    parent_group = groups[parent_group_index]
    selected = {parent_group_index}
    used_tokens = sum(estimate_tokens(_get(row, "content", "")) for row in parent_group)
    ranked: list[tuple[float, int, int]] = []
    for index, group in enumerate(groups[:parent_group_index]):
        group_text = " ".join(_get(row, "content", "") for row in group)
        group_terms = _terms(group_text)
        overlap = len(query_terms & group_terms)
        if overlap == 0:
            continue
        relevance = overlap / max(1, len(group_terms) ** 0.5)
        recency = max(_get(row, "seq", 0) for row in group)
        ranked.append((relevance, recency, index))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for _, _, index in ranked:
        group_tokens = sum(estimate_tokens(_get(row, "content", "")) for row in groups[index])
        if token_budget > 0 and used_tokens + group_tokens > token_budget:
            continue
        selected.add(index)
        used_tokens += group_tokens
    return [row for index, group in enumerate(groups) if index in selected for row in group]


def select_semantic_messages(
    rows: list[Any],
    *,
    required_seq: int,
    token_budget: int,
    similarity_scores: dict[int, float],
    minimum_similarity: float = 0.25,
) -> list[Any]:
    """Keep the parent turn and add earlier turns ranked by embedding cosine similarity."""
    groups = _turn_groups(rows)
    parent_group_index = next(
        (index for index, group in enumerate(groups) if any(_get(row, "seq") == required_seq for row in group)),
        None,
    )
    if parent_group_index is None:
        return rows

    parent_group = groups[parent_group_index]
    selected = {parent_group_index}
    used_tokens = sum(estimate_tokens(_get(row, "content", "")) for row in parent_group)
    ranked: list[tuple[float, int, int]] = []
    for index, group in enumerate(groups[:parent_group_index]):
        first_seq = int(_get(group[0], "seq", 0))
        similarity = similarity_scores.get(first_seq, 0.0)
        if similarity >= minimum_similarity:
            recency = max(_get(row, "seq", 0) for row in group)
            ranked.append((similarity, recency, index))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for _, _, index in ranked:
        group_tokens = sum(estimate_tokens(_get(row, "content", "")) for row in groups[index])
        if token_budget > 0 and used_tokens + group_tokens > token_budget:
            continue
        selected.add(index)
        used_tokens += group_tokens
    return [row for index, group in enumerate(groups) if index in selected for row in group]


def build_main_context(
    conversation: Any,
    messages: Iterable[Any],
    upto_seq: int,
    *,
    context_strategy: str = "full",
    snapshots: Iterable[Any] = (),
    selection_query: str = "",
    required_seq: int | None = None,
    context_token_budget: int = 0,
    selection_scores: dict[int, float] | None = None,
) -> Prompt:
    rows = [
        message for message in messages
        if _get(message, "active", True) and _get(message, "seq", 0) <= upto_seq
    ]
    rows.sort(key=lambda message: _get(message, "seq", 0))
    summary_prefix: list[dict[str, str]] = []
    if context_strategy == "summary_checkpoint":
        valid = [
            snapshot for snapshot in snapshots
            if _get(snapshot, "valid", True) and _get(snapshot, "upto_seq", 0) <= upto_seq
        ]
        if valid:
            snapshot = max(valid, key=lambda item: _get(item, "upto_seq", 0))
            summary_prefix = [{"role": "assistant", "content": f"Conversation summary: {_get(snapshot, 'summary', '')}"}]
            rows = [row for row in rows if _get(row, "seq", 0) > _get(snapshot, "upto_seq", 0)]

    if context_strategy == "relevant" and required_seq is not None:
        rows = select_relevant_messages(
            rows,
            query=selection_query,
            required_seq=required_seq,
            token_budget=context_token_budget,
        )
    elif context_strategy == "semantic" and required_seq is not None:
        rows = select_semantic_messages(
            rows,
            required_seq=required_seq,
            token_budget=context_token_budget,
            similarity_scores=selection_scores or {},
            minimum_similarity=settings.semantic_similarity_threshold,
        )

    prompt_messages: list[dict[str, str]] = summary_prefix
    for row in rows:
        content = _get(row, "content", "")
        quote = _get(row, "quote")
        if _get(conversation, "condition", "A") == "C" and quote:
            content = f'[Regarding this passage from your earlier reply: "{quote["text"]}"]\n\n{content}'
        prompt_messages.append({"role": _get(row, "role"), "content": content})
    return Prompt(system=base_system(conversation), messages=prompt_messages)


def build_thread_context(
    thread: Any,
    conversation: Any,
    messages: Iterable[Any],
    thread_messages: Iterable[Any],
    *,
    context_strategy: str = "full",
    snapshots: Iterable[Any] = (),
    context_token_budget: int = 0,
    selection_scores: dict[int, float] | None = None,
) -> Prompt:
    parent_id = _get(thread, "parent_message_id")
    parent = next((message for message in messages if _get(message, "id") == parent_id), None)
    if parent is None:
        raise ValueError("Thread parent message was not found")
    ordered_thread_messages = sorted(thread_messages, key=lambda item: _get(item, "seq", 0))
    latest_user_text = next(
        (_get(item, "content", "") for item in reversed(ordered_thread_messages) if _get(item, "role") == "user"),
        "",
    )
    prompt = build_main_context(
        conversation, messages, _get(parent, "seq"),
        context_strategy=context_strategy,
        snapshots=snapshots,
        selection_query=f"{_get(thread, 'anchor_text', '')}\n{latest_user_text}",
        required_seq=_get(parent, "seq"),
        context_token_budget=context_token_budget,
        selection_scores=selection_scores,
    )
    if ordered_thread_messages and _get(ordered_thread_messages[0], "role") != "user":
        raise ValueError("Thread history must begin with a user message")
    return Prompt(
        system=prompt.system + "\n\n" + THREAD_FRAMING_V1.format(anchor_text=_get(thread, "anchor_text", "")),
        messages=prompt.messages + [
            {"role": _get(message, "role"), "content": _get(message, "content", "")}
            for message in ordered_thread_messages
        ],
    )


async def build_semantic_thread_context(
    thread: Any,
    conversation: Any,
    messages: Iterable[Any],
    thread_messages: Iterable[Any],
    *,
    context_token_budget: int = 0,
) -> tuple[Prompt, int]:
    """Build branch context using embeddings for earlier-turn relevance ranking."""
    messages = list(messages)
    parent_id = _get(thread, "parent_message_id")
    parent = next((message for message in messages if _get(message, "id") == parent_id), None)
    if parent is None:
        raise ValueError("Thread parent message was not found")

    main_rows = [
        message for message in messages
        if _get(message, "active", True) and _get(message, "seq", 0) <= _get(parent, "seq")
    ]
    main_rows.sort(key=lambda message: _get(message, "seq", 0))
    groups = _turn_groups(main_rows)
    parent_group_index = next(
        (index for index, group in enumerate(groups) if any(_get(row, "seq") == _get(parent, "seq") for row in group)),
        None,
    )
    ordered_thread_messages = sorted(thread_messages, key=lambda item: _get(item, "seq", 0))
    if ordered_thread_messages and _get(ordered_thread_messages[0], "role") != "user":
        raise ValueError("Thread history must begin with a user message")
    latest_user_text = next(
        (_get(item, "content", "") for item in reversed(ordered_thread_messages) if _get(item, "role") == "user"),
        "",
    )
    similarity_scores: dict[int, float] = {}
    embedding_input_tokens = 0
    if parent_group_index is not None and parent_group_index > 0:
        candidate_groups = groups[:parent_group_index]
        query = f"{_get(thread, 'anchor_text', '')}\n{latest_user_text}"
        texts = [query, *["\n".join(_get(row, "content", "") for row in group) for group in candidate_groups]]
        vectors, embedding_input_tokens = await embed_texts(texts)
        query_vector = vectors[0]
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        for group, vector in zip(candidate_groups, vectors[1:], strict=True):
            vector_norm = math.sqrt(sum(value * value for value in vector))
            denominator = query_norm * vector_norm
            similarity = sum(left * right for left, right in zip(query_vector, vector, strict=True)) / denominator if denominator else 0.0
            similarity_scores[int(_get(group[0], "seq", 0))] = similarity

    prompt = build_thread_context(
        thread,
        conversation,
        main_rows,
        ordered_thread_messages,
        context_strategy="semantic",
        context_token_budget=context_token_budget,
        selection_scores=similarity_scores,
    )
    return prompt, embedding_input_tokens
