"""Lori's reply -> (chat message, document). The document becomes a real PDF.

Lori writes her work product as markdown inside one block:

    <document title="Exhibit Index" kind="Reply certification" caption="yes" page_limit="18">
    # Index of Exhibits to Reply Certification
    | Ex. | Description | Bates range | Page |
    ...
    </document>

Everything outside the block is the chat message. Each draft is stored in
data/drafts/<id>/ as draft.md, draft.pdf, page-N.png and meta.json.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .pdf import build_pdf, render_pages
from .settings import DRAFT_DIR, SAVED_DIR, load_matter

_DOC = re.compile(r"<document\b([^>]*)>(.*?)</document\s*>", re.S | re.I)
_DOC_OPEN = re.compile(r"<document\b([^>]*)>(.*)\Z", re.S | re.I)  # reply cut off before the closing tag
_ATTR = re.compile(r'([a-z_]+)\s*=\s*"([^"]*)"', re.I)


def split_reply(raw: str) -> tuple[str, dict | None]:
    """Chat text and the document (or None when Lori only answered in chat)."""
    text = raw or ""
    match = _DOC.search(text) or _DOC_OPEN.search(text)
    if not match:
        return text.strip(), None
    attrs = {key.lower(): value.strip() for key, value in _ATTR.findall(match.group(1))}
    body = match.group(2).strip()
    body = re.sub(r"^```[a-z]*\s*\n", "", body)
    body = re.sub(r"\n```\s*$", "", body).strip()
    chat = (text[: match.start()] + text[match.end() :]).strip()
    if not body:
        return chat, None
    limit = re.sub(r"\D", "", attrs.get("page_limit", ""))
    return chat, {
        "title": attrs.get("title") or "Draft",
        "kind": attrs.get("kind") or "Draft",
        "caption": attrs.get("caption", "yes").lower() not in {"no", "false", "0"},
        "page_limit": int(limit) if limit else None,
        "markdown": body,
    }


def _folder(draft_id: str) -> Path:
    return DRAFT_DIR / re.sub(r"[^a-zA-Z0-9_-]", "", draft_id)


def create_draft(doc: dict, *, draft_id: str | None = None, extra: dict | None = None) -> dict:
    """Build the PDF and its page images. Returns the stored record."""
    draft_id = draft_id or uuid.uuid4().hex[:12]
    folder = _folder(draft_id)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / "draft.md").write_text(doc["markdown"] + "\n", encoding="utf-8")
    info = build_pdf(
        doc["markdown"],
        folder / "draft.pdf",
        matter=load_matter(),
        title=doc["title"],
        kind=doc["kind"],
        caption=doc["caption"],
    )
    pages = render_pages(folder / "draft.pdf", folder)
    meta = {
        "id": draft_id,
        "title": doc["title"],
        "kind": doc["kind"],
        "caption": doc["caption"],
        "page_limit": doc.get("page_limit"),
        "pages": pages,
        "font": info["font"],
        "double_spaced": info["double_spaced"],
        "saved": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def get_draft(draft_id: str | None) -> dict | None:
    if not draft_id:
        return None
    path = _folder(draft_id) / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def draft_file(draft_id: str, name: str) -> Path | None:
    path = _folder(draft_id) / name
    return path if path.is_file() else None


def file_name(meta: dict) -> str:
    stem = re.sub(r"[^A-Za-z0-9 _-]", "", meta.get("title") or "Draft").strip() or "Draft"
    return f"{stem}.pdf"


def save_draft(draft_id: str) -> dict | None:
    """'Save to LEAP'. LEAP is not connected yet, so the PDF is filed in data/saved/."""
    meta = get_draft(draft_id)
    if not meta:
        return None
    target = SAVED_DIR / f"{meta['id']} - {file_name(meta)}"
    shutil.copyfile(_folder(draft_id) / "draft.pdf", target)
    meta["saved"] = True
    meta["saved_to"] = str(target)
    (_folder(draft_id) / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
