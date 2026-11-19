"""RAG: decide what Lori gets to read this turn, and record exactly what that was.

* Case folders: storage only. The manifest lists what is on disk. File text reaches
  Lori only when the user attaches or drags a file into chat (handled as case_file).
* Knowledge base: best matching passages from sources switched on (rules, statutes, SOPs).
* Firm drafting examples: curated OKF bundle — index catalog + matching example passages.

The record of what was used ("activity") is what the chat shows the user.
"""

from __future__ import annotations

import json
import logging

from . import vectors
from .ingest import bates_ranges, court_volumes, kb_source, kb_stats, library_stats
from .settings import (
    EXAMPLE_PASSAGE_CHARS,
    KB_PASSAGE_CHARS,
    KB_SOURCES,
    PROMPT_PATH,
    RAG_K_EXAMPLES,
    RAG_K_KB,
    load_matter,
)
from .store import get_kv

log = logging.getLogger("lori.rag")

KB_LABELS = {source["id"]: source["label"] for source in KB_SOURCES}


def kb_enabled() -> dict[str, bool]:
    try:
        saved = json.loads(get_kv("kb_enabled") or "{}")
    except ValueError:
        saved = {}
    return {source["id"]: bool(saved.get(source["id"], True)) for source in KB_SOURCES}


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def _search(question: str, k: int, kinds: list[str]) -> list[dict]:
    if not kinds:
        return []
    try:
        return vectors.search(question, k=k, kinds=kinds)
    except Exception as exc:
        log.exception("Qdrant knowledge-base search failed")
        raise RuntimeError(
            f"Knowledge-base search failed ({exc}). Check Qdrant Cloud and try again."
        ) from exc


def _examples_catalog() -> str:
    """Short map of the drafting OKF bundle from its index.md (progressive disclosure)."""
    source = kb_source("drafting_examples")
    if not source:
        return ""
    index = source["dir"] / "index.md"
    if not index.is_file():
        return ""
    try:
        text = index.read_text(encoding="utf-8")
    except OSError:
        return ""
    # Drop YAML fence if present; keep the linked catalog body.
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) >= 3 else text
    return text.strip()[:6_000]


def manifest_text(stats: dict, bates: dict, volumes: dict) -> str:
    """Every file in the case folders: name, size, pages, Bates range. No file text."""
    if not stats["total_files"]:
        return "The case folders are empty. Nothing has been uploaded yet."
    by_name = {row["name"]: row for row in bates["rows"]}
    lines = []
    for folder in stats["folders"]:
        if not folder["count"]:
            lines.append(f"{folder['label']}: empty")
            continue
        size = sum(f["bytes"] for f in folder["files"])
        lines.append(f"{folder['label']}: {folder['count']} file(s), {_mb(size)}")
        for number, item in enumerate(folder["files"], start=1):
            parts = [f"  {number}. {item['name']}", _mb(item["bytes"])]
            parts.append(f"{item['pages']} page(s)" if item["pages"] else "page count not available")
            row = by_name.get(item["name"]) if folder["id"] == "exhibits" else None
            if row and row["bates_start"]:
                parts.append(f"Bates {row['bates_start']} to {row['bates_end']}")
                parts.append(f"starts at page {row['start_page']}")
            lines.append(" | ".join(parts))
    if bates["pages"]:
        lines.append(
            f"Exhibit Bates run: {bates['first']} to {bates['last']} ({bates['pages']} pages). "
            "These ranges are assigned from page counts; the PDFs themselves are not stamped."
        )
    if bates["missing"] and bates["prefix"]:
        lines.append("No Bates range (no page count): " + ", ".join(bates["missing"]))
    if len(volumes["volumes"]) > 1 or volumes["oversize"]:
        lines.append(
            f"Court portal limit is {volumes['limit_mb']} MB per upload. "
            f"The exhibits need {len(volumes['volumes'])} volumes: "
            + "; ".join(
                f"Vol {i}: {v['files'][0]} to {v['files'][-1]} ({_mb(v['bytes'])})"
                for i, v in enumerate(volumes["volumes"], start=1)
            )
        )
        if volumes["oversize"]:
            lines.append("Over the limit on their own: " + ", ".join(volumes["oversize"]))
    return "\n".join(lines)


def build_context(question: str) -> dict:
    """Knowledge-base + drafting-example passages for this turn, plus case-folder inventory."""
    stats = library_stats()
    files = [item for folder in stats["folders"] for item in folder["files"]]
    exhibits = next(f["files"] for f in stats["folders"] if f["id"] == "exhibits")
    bates = bates_ranges(exhibits)
    volumes = court_volumes(exhibits)

    enabled = kb_enabled()
    kb = kb_stats(enabled)

    ref_kinds = [
        s["id"]
        for s in kb
        if s["chunks"] and s["enabled"] and s["id"] != "drafting_examples"
    ]
    example_on = any(s["id"] == "drafting_examples" and s["chunks"] and s["enabled"] for s in kb)

    kb_hits = _search(question, RAG_K_KB, ref_kinds)
    example_hits = _search(question, RAG_K_EXAMPLES, ["drafting_examples"]) if example_on else []

    kb_blocks, kb_used = [], {}
    for hit in kb_hits:
        kind = hit.get("kind", "")
        title = hit.get("title") or hit.get("source_file", "")
        section = f" | {hit['section']}" if hit.get("section") else ""
        kb_blocks.append(
            f"[{KB_LABELS.get(kind, kind)} / {title}{section}]\n{str(hit.get('body', ''))[:KB_PASSAGE_CHARS]}"
        )
        kb_used.setdefault(kind, [])
        if title not in kb_used[kind]:
            kb_used[kind].append(title)

    example_blocks = []
    if example_on:
        catalog = _examples_catalog()
        if catalog:
            example_blocks.append(
                "[Firm drafting examples / bundle index]\n"
                "Use these curated firm examples for structure and tone when drafting. "
                "Do not copy party names or facts from examples into a new matter.\n\n"
                + catalog
            )
        for hit in example_hits:
            title = hit.get("title") or hit.get("source_file", "")
            section = f" | {hit['section']}" if hit.get("section") else ""
            example_blocks.append(
                f"[Firm drafting example / {title}{section}]\n"
                f"{str(hit.get('body', ''))[:EXAMPLE_PASSAGE_CHARS]}"
            )
            kb_used.setdefault("drafting_examples", [])
            if title not in kb_used["drafting_examples"]:
                kb_used["drafting_examples"].append(title)

    knowledge_parts = []
    if kb_blocks:
        knowledge_parts.append("### Reference passages\n\n" + "\n\n".join(kb_blocks))
    if example_blocks:
        knowledge_parts.append("### Firm drafting examples (OKF)\n\n" + "\n\n".join(example_blocks))

    activity = {
        "mode": "empty" if not files else "inventory",
        "files_total": len(files),
        "files_used": 0,
        "passages": len(kb_hits) + len(example_hits),
        "unreadable": [],
        "folders": [],
        "kb": [{"id": kind, "label": KB_LABELS.get(kind, kind), "titles": titles} for kind, titles in kb_used.items()],
        "bates": {"first": bates["first"], "last": bates["last"], "pages": bates["pages"]},
        "pages": sum(item["pages"] or 0 for item in files),
    }
    return {
        "manifest": manifest_text(stats, bates, volumes),
        "library": (
            "No case-folder text was auto-loaded. "
            "File text arrives only when the user attaches or drags a file into chat this turn."
        ),
        "knowledge": "\n\n".join(knowledge_parts),
        "activity": activity,
    }


def matter_text() -> str:
    matter = load_matter()
    if not matter.get("name"):
        return "No matter record is set."
    lines = [
        f"Matter: {matter['name']}",
        f"Docket: {matter.get('docket') or 'not set'}",
        f"County: {matter.get('county') or 'not set'}",
        f"Plaintiff: {matter.get('plaintiff') or 'not set'}",
        f"Defendant: {matter.get('defendant') or 'not set'}",
        f"Bates prefix: {matter.get('bates_prefix') or 'not set'}",
    ]
    if matter.get("firm_block"):
        lines.append("Firm block: " + " / ".join(matter["firm_block"]))
    return "\n".join(lines)


def load_prompt(context: dict, facts: list[str], case_file: str = "") -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    fill = {
        "{matter}": matter_text(),
        "{manifest}": context.get("manifest") or "The case folders are empty.",
        "{library}": context.get("library") or "No case-folder text was provided this turn.",
        "{knowledge}": context.get("knowledge") or "No knowledge-base passages matched this turn.",
        "{case_file}": (case_file or "").strip() or "No case file attached this turn.",
        "{facts}": "\n".join(f"- {f}" for f in facts) or "No stored matter notes yet.",
    }
    for key, value in fill.items():
        template = template.replace(key, value)
    return template
