"""LangGraph: retrieve → Lori ⇄ tools → save."""

from __future__ import annotations

import json
import re
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .rag import build_context, load_prompt
from .settings import llm
from .store import add_fact, add_message, all_facts, forget_fact, history, relevant_facts
from .tools import TOOLS


class State(TypedDict, total=False):
    message: str
    case_file: str
    case_file_name: str
    history: list[dict]
    facts: list[str]
    context: dict  # manifest + case-folder text + knowledge-base passages + activity record
    messages: Annotated[list[AnyMessage], add_messages]
    control: str
    reply: str
    user_message_id: str
    assistant_message_id: str


def node_retrieve(state: State) -> dict:
    return {
        "context": build_context(state["message"]),
        "history": history(),
        "facts": relevant_facts(state["message"]),
    }


def node_agent(state: State) -> dict:
    model = llm().bind_tools(TOOLS)
    existing = state.get("messages") or []
    if existing:
        return {"messages": [model.invoke(existing)]}
    prompt = [
        SystemMessage(
            content=load_prompt(
                state.get("context") or {},
                state.get("facts") or [],
                case_file=state.get("case_file") or "",
            )
        )
    ]
    for item in state.get("history") or []:
        if item["role"] == "assistant":
            prompt.append(AIMessage(content=item["content"]))
        else:
            prompt.append(HumanMessage(content=item["content"]))
    user_text = state["message"]
    if state.get("case_file_name"):
        user_text = f"[Attached case file: {state['case_file_name']}]\n\n{user_text}"
    prompt.append(HumanMessage(content=user_text))
    return {"messages": prompt + [model.invoke(prompt)]}


def route_agent(state: State) -> str:
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if last is not None and getattr(last, "tool_calls", None):
        return "tools"
    return "save"


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            elif hasattr(block, "text"):
                parts.append(str(block.text))
        return "".join(parts)
    return str(content or "")


def _user_wants_end(message: str) -> bool:
    """True only when the user message itself is a short farewell — not substring hits in replies."""
    text = re.sub(r"\s+", " ", (message or "").strip().lower()).rstrip(".!")
    return text in {
        "goodbye",
        "bye",
        "bye bye",
        "that's all",
        "thats all",
        "done for now",
        "thanks that's all",
        "thanks thats all",
    }


def node_save(state: State) -> dict:
    messages = state.get("messages") or []
    reply = ""
    # Review only when the request_attorney_review tool was actually called this turn.
    used_review = any(getattr(item, "name", "") == "request_attorney_review" for item in messages)
    for item in reversed(messages):
        if isinstance(item, AIMessage) and item.content and not getattr(item, "tool_calls", None):
            reply = _message_text(item.content)
            break
    control = "continue"
    if used_review:
        control = "review"
    elif _user_wants_end(state.get("message") or ""):
        control = "end"

    user_id = add_message("user", state.get("message", ""))
    assistant_id = add_message("assistant", reply or "I am ready when you want to continue.")

    try:
        extracted = llm().invoke(
            [
                SystemMessage(
                    content=(
                        "Extract durable matter notes from this turn. "
                        "Return JSON only: {\"facts\": [], \"forget\": []}. "
                        "Facts are case facts, deadlines, party names, or open questions. "
                        "Do not invent. Do not store legal conclusions as facts."
                    )
                ),
                HumanMessage(content=f"User: {state.get('message', '')}\nAssistant: {reply[:6000]}"),
            ]
        )
        found = re.search(r"\{.*\}", _message_text(extracted.content), flags=re.S)
        payload = json.loads(found.group(0)) if found else {}
    except Exception:
        payload = {}
    for item in payload.get("forget") or []:
        forget_fact(str(item))
    for item in payload.get("facts") or []:
        add_fact(str(item))
    return {
        "reply": reply or "I am ready when you want to continue.",
        "control": control,
        "facts": all_facts(),
        "user_message_id": user_id,
        "assistant_message_id": assistant_id,
    }


ENGINE = None


def reset_engine():
    global ENGINE
    ENGINE = None


def get_engine():
    global ENGINE
    if ENGINE is None:
        graph = StateGraph(State)
        graph.add_node("retrieve", node_retrieve)
        graph.add_node("agent", node_agent)
        graph.add_node("tools", ToolNode(TOOLS))
        graph.add_node("save", node_save)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "agent")
        graph.add_conditional_edges("agent", route_agent, {"tools": "tools", "save": "save"})
        graph.add_edge("tools", "agent")
        graph.add_edge("save", END)
        ENGINE = graph.compile()
    return ENGINE
