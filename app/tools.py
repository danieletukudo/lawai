"""Functions Lori is allowed to call."""

from __future__ import annotations

from langchain_core.tools import tool

from .store import add_action, add_fact


@tool
def save_matter_note(note: str) -> str:
    """Save a durable matter note: a fact, deadline, party name, or open question from the case."""
    stored = add_fact(note)
    return f"Already stored: {note}" if stored is None else f"Stored matter note: {stored}"


@tool
def flag_missing_info(what_is_missing: str, why_it_matters: str = "") -> str:
    """Flag information that is missing from the supplied materials and blocks reliable drafting."""
    payload = {"missing": what_is_missing, "why": why_it_matters}
    add_action("missing_info", payload)
    add_fact(f"MISSING: {what_is_missing}" + (f" — {why_it_matters}" if why_it_matters else ""))
    why = f" Why it matters: {why_it_matters}" if why_it_matters else ""
    return f"Flagged missing information: {what_is_missing}.{why} Continue with supported facts only."


@tool
def request_attorney_review(reason: str, draft_summary: str = "") -> str:
    """Hand work to a licensed attorney when strategy, approval, filing, or send authority is required."""
    add_action("attorney_review", {"reason": reason, "draft_summary": draft_summary})
    detail = f" Draft summary: {draft_summary}" if draft_summary else ""
    return (
        f"Marked for attorney review. Reason: {reason}.{detail} "
        "Do not treat this as approval to send, file, or calendar."
    )


TOOLS = [save_matter_note, flag_missing_info, request_attorney_review]
