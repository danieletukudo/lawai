"""Everything the page shows, as one payload. The page only draws what it is given.

Every number here is measured: file counts, sizes, page counts, passages
retrieved, pages in the generated PDF. Nothing is typed in by hand, except in
the sample view (samples/workspace.json), which reproduces the design mockup.
"""

from __future__ import annotations

import json

from .drafts import create_draft, get_draft, split_reply
from .ingest import bates_ranges, court_volumes, kb_stats, library_stats
from .rag import kb_enabled
from .settings import SAMPLE_DIR, UPLOAD_BATCH_MB, USER_NAME, load_matter
from .store import get_kv, thread


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def _size(size: int) -> str:
    if size >= 1024 * 1024:
        return _mb(size)
    return f"{max(1, round(size / 1024))} KB"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


# --- one chat turn -----------------------------------------------------------


def turn_cards(activity: dict, case: dict | None) -> list[dict]:
    """Cards for what Lori actually received this turn (attach + knowledge base)."""
    cards = []
    if case:
        whole = case["used_chunks"] == case["total_chunks"]
        cards.append(
            {
                "title": "Read attached case file",
                "count": f"{case['used_chunks']} of {case['total_chunks']} parts",
                "pct": round(100 * case["used_chunks"] / max(1, case["total_chunks"])),
                "sub": (
                    f"{case['name']}, read in full."
                    if whole
                    else f"{case['name']}, the parts most relevant to your request."
                )
                + " Sent for this turn only.",
            }
        )
    return cards


def turn_chips(activity: dict, case: dict | None) -> list[str]:
    """The 'Used' chips under a reply."""
    chips = []
    if case:
        chips.append(f"Case file: {case['name']}")
    for source in activity["kb"]:
        if source["id"] == "firm":
            chips.extend(f"Firm SOP: {title}" for title in source["titles"][:2])
        else:
            chips.append(source["label"])
    return chips


def build_turn(raw_reply: str, activity: dict, case: dict | None) -> dict:
    """Display data stored with Lori's message. Builds the PDF when she wrote a document."""
    _, doc = split_reply(raw_reply)
    warnings: list[str] = []
    draft_id = None
    if doc:
        bates = activity["bates"]
        prefix = str(load_matter().get("bates_prefix") or "")
        extra = {}
        if bates["pages"] and prefix and prefix in doc["markdown"]:
            extra["bates"] = f"Bates {bates['first']} to {bates['last'].replace(prefix, '').strip()}"
        try:
            meta = create_draft(doc, extra=extra)
            draft_id = meta["id"]
            if meta["page_limit"] and meta["pages"] > meta["page_limit"]:
                warnings.append(
                    f"This draft runs {meta['pages']} pages, over the {meta['page_limit']}-page limit."
                )
        except Exception as exc:  # the chat reply still stands if the PDF fails
            warnings.append(f"The PDF could not be built: {exc}")
    return {
        "cards": turn_cards(activity, case),
        "used": turn_chips(activity, case),
        "warnings": warnings,
        "draft_id": draft_id,
    }


# --- the whole page ----------------------------------------------------------


def _doc_card(meta: dict) -> dict:
    return {
        "id": meta["id"],
        "title": meta["title"],
        "sub": f"{meta['kind']} \u00b7 {_plural(meta['pages'], 'page')} \u00b7 Draft",
        "saved": bool(meta.get("saved")),
    }


def _draft_panel(meta: dict) -> dict:
    pages, limit = meta["pages"], meta.get("page_limit")
    stamp = "".join(ch for ch in meta["created_at"] if ch.isdigit())
    return {
        "id": meta["id"],
        "title": meta["title"],
        "tag": "In review",
        "approve_label": "Approve index" if "index" in meta["title"].lower() else "Approve draft",
        "strong": meta.get("bates") or meta["kind"],
        "note": "Draft for attorney review. Not filed or sent.",
        "kind": meta["kind"],
        "pages_label": f"{pages} of {limit} pages" if limit else _plural(pages, "page"),
        "pct": min(100, round(100 * pages / limit)) if limit else None,
        "over": bool(limit and pages > limit),
        "formatting": "Firm formatting applied",
        "page_urls": [f"/drafts/{meta['id']}/page/{n}.png?v={stamp}" for n in range(1, pages + 1)],
        "pdf_url": f"/drafts/{meta['id']}/pdf",
        "saved": bool(meta.get("saved")),
    }


def _thread() -> list[dict]:
    name = USER_NAME or "You"
    out = []
    for row in thread():
        meta = row["meta"] or {}
        if row["role"] == "user":
            out.append(
                {
                    "role": "user",
                    "text": row["content"],
                    "name": name,
                    "time": row["created_at"],
                    "case_file": meta.get("case_file"),
                }
            )
            continue
        chat, doc = split_reply(row["content"])
        draft = get_draft(meta.get("draft_id"))
        if not chat:
            chat = (
                "The draft is ready for your review in the Drafting Table."
                if draft or doc
                else "I am ready when you want to continue."
            )
        out.append(
            {
                "role": "assistant",
                "text": chat,
                "time": row["created_at"],
                "cards": meta.get("cards") or [],
                "warnings": meta.get("warnings") or [],
                "used": meta.get("used") or [],
                "doc": _doc_card(draft) if draft else None,
            }
        )
    return out


def workspace() -> dict:
    matter = load_matter()
    stats = library_stats()
    exhibits = next(f["files"] for f in stats["folders"] if f["id"] == "exhibits")
    bates = bates_ranges(exhibits)
    volumes = court_volumes(exhibits)

    by_name = {row["name"]: row for row in bates["rows"]}
    folders = []
    for folder in stats["folders"]:
        files = []
        for item in folder["files"]:
            row = by_name.get(item["name"]) if folder["id"] == "exhibits" else None
            hint = [_size(item["bytes"])]
            if item["pages"]:
                hint.append(_plural(item["pages"], "page"))
            if row and row["bates_start"]:
                hint.append(f"Bates {row['bates_start']} to {row['bates_end']}")
            files.append(
                {
                    "name": item["name"],
                    "size": _size(item["bytes"]),
                    "hint": ", ".join(hint),
                    "readable": True,
                }
            )
        folders.append({"id": folder["id"], "label": folder["label"], "count": folder["count"], "files": files})

    raw = kb_stats(kb_enabled())
    by_id = {s["id"]: s for s in raw}

    def book_ready(source: dict) -> bool:
        return source["files"] > 0 and not source["waiting"] and not source["indexing"]

    books_ready = book_ready(by_id.get("court_rules") or {"files": 0, "waiting": 1, "indexing": False}) and book_ready(
        by_id.get("statutes") or {"files": 0, "waiting": 1, "indexing": False}
    )

    sources = []
    for source in raw:
        has_files = source["files"] > 0
        is_firm = source["id"] == "firm"
        # Firm SOPs are built-in. No upload. Marked On only when Court Rules + Statutes are fully indexed.
        if is_firm:
            if books_ready:
                status, tone, checked, disabled = "On", "ok", True, False
                hint = "On when NJ Court Rules and NJ Family Law Statutes are fully indexed."
            else:
                status, tone, checked, disabled = "Waiting", "muted", False, True
                hint = "Turns on automatically when Court Rules and Family Law Statutes finish indexing."
            file_list = []
            uploadable = False
        else:
            if source["indexing"]:
                status, tone = f"Indexing {source['percent']}%", "busy"
            elif source["waiting"]:
                status, tone = "Queued", "busy"
            elif not has_files:
                status, tone = "Empty", "muted"
            elif source["enabled"]:
                status, tone = "On", "ok"
            else:
                status, tone = "Off", "muted"
            checked = source["enabled"] and has_files and not source["waiting"] and not source["indexing"]
            disabled = not has_files
            hint = (
                f"{_plural(source['files'], 'file')}, {source['vectors']} of {_plural(source['chunks'], 'passage')} indexed"
                if has_files
                else "Upload the markdown for this source."
            )
            file_list = [
                {
                    "name": row["name"],
                    "size": _size(row["bytes"]),
                    "readable": True,
                    "indexed": bool(row.get("indexed")),
                }
                for row in (source.get("file_rows") or [])
            ]
            uploadable = True
        sources.append(
            {
                "id": source["id"],
                "label": source["label"],
                "checked": checked,
                "disabled": disabled,
                "status": status,
                "tone": tone,
                "hint": hint,
                "files": file_list,
                "uploadable": uploadable,
                "builtin": is_firm,
            }
        )
    active = sum(1 for s in sources if s["checked"])

    if not exhibits:
        split = "No exhibits uploaded yet, so there is nothing to split."
    elif len(volumes["volumes"]) == 1 and not volumes["oversize"]:
        split = (
            f"All {_plural(len(exhibits), 'exhibit')} fit in one upload "
            f"({_mb(volumes['volumes'][0]['bytes'])} of {volumes['limit_mb']} MB)."
        )
    else:
        split = f"Needs {len(volumes['volumes'])} volumes under {volumes['limit_mb']} MB: " + "; ".join(
            f"Vol {i}, {v['files'][0]} to {v['files'][-1]} ({_mb(v['bytes'])})"
            for i, v in enumerate(volumes["volumes"], start=1)
        )

    draft = get_draft(get_kv("open_draft"))
    return {
        "sample": False,
        "user": {"name": USER_NAME or "You", "initials": "".join(w[0] for w in USER_NAME.split()[:2]).upper()},
        "matter": {
            "name": matter.get("name") or "No case selected",
            "line": " \u00b7 ".join(x for x in (matter.get("docket"), matter.get("county")) if x),
        },
        "leap": {"text": "LEAP not connected", "ok": False},
        "files_label": _plural(stats["total_files"], "file"),
        "files_heading": "Case file",
        "folders": folders,
        "upload": {"batch_mb": UPLOAD_BATCH_MB},
        "kb": {"label": f"{active} of {len(sources)} active", "composer": f"{_plural(active, 'source')} on", "sources": sources},
        "thread": _thread(),
        "draft": _draft_panel(draft) if draft else None,
        "court_split": split,
    }


def sample_workspace() -> dict:
    """The design mockup's content, drawn by the same page. The PDF is built for real."""
    data = json.loads((SAMPLE_DIR / "workspace.json").read_text(encoding="utf-8"))
    markdown = (SAMPLE_DIR / "exhibit-index.md").read_text(encoding="utf-8")
    meta = create_draft(
        {"title": "Exhibit Index", "kind": "Reply certification", "caption": True, "page_limit": 18, "markdown": markdown},
        draft_id="sample",
    )
    panel = _draft_panel(meta)
    panel.update(data.pop("draft_overrides", {}))
    data["draft"] = panel
    return data
