from app.models import Conversation

BASE_SYSTEM_V1 = (
    "You are a helpful assistant. Answer accurately and clearly. Use Markdown where it helps.\n"
    "If you are unsure, say so."
)
THREAD_FRAMING_V1 = (
    "The user selected the following passage from your most recent reply (the last assistant\n"
    "message in the history) and is asking about it specifically:\n<selected_passage>\n"
    "{anchor_text}\n</selected_passage>\n"
    "Answer the question about that passage. Be concise and focused on resolving the doubt."
)


def base_system(conversation: Conversation) -> str:
    system = BASE_SYSTEM_V1
    if conversation.system_context:
        system += f"\n\nReference material for this session:\n{conversation.system_context}"
    return system
