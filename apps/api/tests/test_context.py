from types import SimpleNamespace

from app.services.context import build_main_context, build_thread_context


def test_main_context_never_includes_thread_messages():
    conversation = SimpleNamespace(condition="B", system_context=None)
    messages = [
        SimpleNamespace(seq=1, role="user", content="main question", active=True),
        SimpleNamespace(seq=2, role="assistant", content="main answer", active=True),
    ]
    prompt = build_main_context(conversation, messages, 2)
    assert "thread-only secret" not in str(prompt.messages)


def test_thread_context_stops_at_parent_and_appends_thread_history():
    conversation = SimpleNamespace(condition="B", system_context=None)
    messages = [
        SimpleNamespace(id="m1", seq=1, role="user", content="question", active=True),
        SimpleNamespace(id="m2", seq=2, role="assistant", content="parent answer", active=True),
        SimpleNamespace(id="m3", seq=3, role="user", content="later main question", active=True),
    ]
    thread = SimpleNamespace(parent_message_id="m2", anchor_text="parent")
    side_messages = [SimpleNamespace(seq=1, role="user", content="thread question")]
    prompt = build_thread_context(thread, conversation, messages, side_messages)
    contents = [message["content"] for message in prompt.messages]
    assert contents == ["question", "parent answer", "thread question"]
    assert "later main question" not in str(prompt.messages)
    assert prompt.messages[-2]["role"] == "assistant"
    assert prompt.messages[-1]["role"] == "user"


def test_main_prompt_has_parity_between_a_and_b_and_c_without_quote():
    messages = [
        SimpleNamespace(seq=1, role="user", content="same", quote=None, active=True),
        SimpleNamespace(seq=2, role="assistant", content="answer", quote=None, active=True),
    ]
    prompts = [
        build_main_context(SimpleNamespace(condition=condition, system_context=None), messages, 2)
        for condition in ("A", "B", "C")
    ]
    assert prompts[0] == prompts[1] == prompts[2]


def test_quote_prefix_is_only_added_in_condition_c():
    message = SimpleNamespace(
        seq=1, role="user", content="follow up", quote={"text": "quoted span"}, active=True
    )
    prompt_a = build_main_context(SimpleNamespace(condition="A", system_context=None), [message], 1)
    prompt_c = build_main_context(SimpleNamespace(condition="C", system_context=None), [message], 1)
    assert prompt_a.messages[0]["content"] == "follow up"
    assert prompt_c.messages[0]["content"].startswith('[Regarding this passage from your earlier reply: "quoted span"]')


def test_relevant_thread_context_keeps_relevant_turn_and_parent_not_unrelated_turns():
    conversation = SimpleNamespace(condition="local", system_context=None)
    messages = [
        SimpleNamespace(id="u1", seq=1, role="user", content="We are discussing ATP in cellular respiration.", active=True),
        SimpleNamespace(id="a1", seq=2, role="assistant", content="ATP carries energy for cellular reactions.", active=True),
        SimpleNamespace(id="u2", seq=3, role="user", content="Tell me about the French Revolution.", active=True),
        SimpleNamespace(id="a2", seq=4, role="assistant", content="It began in 1789 and changed France's political system.", active=True),
        SimpleNamespace(id="u3", seq=5, role="user", content="What does energy transfer mean for ATP?", active=True),
        SimpleNamespace(id="a3", seq=6, role="assistant", content="ATP hydrolysis can power cellular work.", active=True),
        SimpleNamespace(id="u4", seq=7, role="user", content="Later main-chat message must be excluded.", active=True),
    ]
    thread = SimpleNamespace(parent_message_id="a3", anchor_text="ATP hydrolysis")
    branch = [SimpleNamespace(seq=1, role="user", content="How does that transfer energy?")]

    prompt = build_thread_context(
        thread,
        conversation,
        messages,
        branch,
        context_strategy="relevant",
        context_token_budget=1000,
    )
    content = " ".join(item["content"] for item in prompt.messages)

    assert "ATP carries energy" in content
    assert "ATP hydrolysis can power" in content
    assert "French Revolution" not in content
    assert "Later main-chat message" not in content
    assert prompt.messages[-1] == {"role": "user", "content": "How does that transfer energy?"}


def test_relevant_context_obeys_budget_except_required_parent_turn():
    conversation = SimpleNamespace(condition="local", system_context=None)
    messages = [
        SimpleNamespace(id="u1", seq=1, role="user", content="unrelated " * 100, active=True),
        SimpleNamespace(id="a1", seq=2, role="assistant", content="unrelated reply " * 100, active=True),
        SimpleNamespace(id="u2", seq=3, role="user", content="anchor topic", active=True),
        SimpleNamespace(id="a2", seq=4, role="assistant", content="parent answer " * 20, active=True),
    ]
    thread = SimpleNamespace(parent_message_id="a2", anchor_text="parent answer")
    prompt = build_thread_context(
        thread,
        conversation,
        messages,
        [SimpleNamespace(seq=1, role="user", content="explain")],
        context_strategy="relevant",
        context_token_budget=30,
    )
    contents = [item["content"] for item in prompt.messages]

    assert not any("unrelated" in content for content in contents)
    assert "parent answer" in " ".join(contents)
