"""Public door: reply(message, case=...). Chat and the UI both call this."""

from __future__ import annotations

from .drafts import split_reply
from .graph import get_engine
from .settings import GOOGLE_API_KEY
from .store import set_kv, set_message_meta
from .workspace import build_turn


def reply(message: str, case: dict | None = None) -> dict:
    """One turn with Lori.

    case: the file attached in chat for this turn only, already chunked by
    app.case_file.prepare_case_context (keys: name, context, total_chunks,
    used_chunks). It is sent to the model and never added to OKF / RAG.
    """
    text = (message or "").strip()
    if not GOOGLE_API_KEY:
        raise RuntimeError("Set GOOGLE_API_KEY in .env")
    if not text and not case:
        raise ValueError("Type a message or attach a case file.")

    case_text = ""
    if case:
        sent, total = case["used_chunks"], case["total_chunks"]
        scope = "all of it is below" if sent == total else f"only the {sent} parts most relevant to the request are below"
        case_text = f"File: {case['name']}. Split into {total} parts; {scope}.\n\n{case['context']}"

    result = get_engine().invoke(
        {
            "message": text or "Use the attached case file and prepare the requested work product.",
            "case_file": case_text,
            "case_file_name": (case or {}).get("name") or "",
        }
    )
    raw = result.get("reply") or ""
    activity = (result.get("context") or {}).get("activity") or {}

    # Build the PDF (if Lori wrote a document) and store what this turn used.
    turn = build_turn(raw, activity, case)
    if result.get("assistant_message_id"):
        set_message_meta(result["assistant_message_id"], turn)
    if case and result.get("user_message_id"):
        set_message_meta(
            result["user_message_id"],
            {"case_file": {"name": case["name"], "used": case["used_chunks"], "total": case["total_chunks"]}},
        )
    if turn["draft_id"]:
        set_kv("open_draft", turn["draft_id"])

    chat, _ = split_reply(raw)
    return {
        "reply": chat,
        "control": result.get("control") or "continue",
        "facts": result.get("facts") or [],
        "sources": turn["used"],
        "draft_id": turn["draft_id"],
    }
