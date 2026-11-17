# Lori — legal preparation assistant

Three-panel workspace for **Tanya Freeman Law**. Lori reads the case folders, the knowledge base and the case file attached in chat, then writes a first draft that opens on the right as a real PDF for attorney review.

Stack: FastAPI, LangGraph, Gemini (chat only), OKF files on disk, Qdrant (local) with FastEmbed (local embeddings), ReportLab (PDF).

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # put the Gemini key in GOOGLE_API_KEY
python api.py
```

- http://127.0.0.1:8000/ — the live workspace
- http://127.0.0.1:8000/?sample=1 — the design mockup's sample content, drawn by the same page (read-only)

Run one server process only. Local Qdrant locks `data/qdrant`.

## How it works

| Where | What it is | Goes into OKF + RAG? |
| --- | --- | --- |
| Left folders: Exhibits, Pleadings, Discovery, Financials, Correspondence | The case documents. Stored in `knowledge/library/<folder>/` | Yes, automatically on upload |
| Knowledge base: NJ Court Rules, NJ Family Law Statutes, Firm SOPs | Reference material in `knowledge/kb/court-rules/`, `knowledge/kb/statutes/`, `knowledge/firm/` | Yes, on server start |
| Paperclip in chat | The case file for this one message | No. Chunked in memory; if it is large only the most relevant parts are sent |

One chat turn:

1. `app/rag.py` builds the context. If the case folders are small enough (`LIBRARY_FULL_CHARS`) Lori gets every file in full; otherwise she gets the best matching passages plus a manifest of every file. Knowledge-base passages come from the sources that are switched on.
2. Lori (`prompt.md`) answers with a short chat message and, when asked for work product, one `<document ...>` block of markdown.
3. `app/drafts.py` splits the reply. `app/pdf.py` turns the markdown into a PDF with the firm formatting (Times New Roman 12, one-inch margins, page numbers, firm block and caption from `matter.json`).
4. The page shows what was actually used: files read, passages retrieved, parts of the attached case file, real page counts.

## Files worth knowing

- `matter.json` — the selected case (names, docket, court, firm block, Bates prefix). It prints on every caption. **It ships with the sample values from the mockup; replace them.**
- `prompt.md` — Lori's instructions and reply format. Re-read every turn.
- `samples/` — content for the sample view.
- `data/drafts/<id>/` — each draft: `draft.md`, `draft.pdf`, page images, `meta.json`.
- `data/saved/` — where "Save to LEAP" files the PDF until LEAP is connected.

## Not built yet

LEAP sync, Word export, billing entries, building court-upload volumes, attorney sign-in and approval, switching cases, Jim / Boz / Lois. The buttons are on the page and say so when clicked.

Bates ranges are worked out from page counts and shown in the index. Nothing is stamped onto the exhibit PDFs.

## Rebuild the index

```bash
python -m app.ingest             # index anything new or changed (stop the server first)
python -m app.ingest --reindex   # rebuild everything
```

## Docker / Render

```bash
docker build -t lori-bot .
docker run --rm -p 8000:8000 --env-file .env lori-bot
```

`render.yaml` is included. Set `GOOGLE_API_KEY` in the Render dashboard. Times New Roman is not installed on the Linux image; drop the four `.ttf` files into `fonts/` or the PDF falls back to the built-in Times face.

Do not commit `.env`. Case files, reference books and `data/` are git-ignored.
# lawai
# lawai
