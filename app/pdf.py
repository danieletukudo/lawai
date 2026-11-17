"""Lori's markdown -> a real PDF laid out to the firm's formatting rules.

Firm rules applied here, not left to the model:
  * Times New Roman, 12 point
  * one-inch margins on Letter paper
  * page numbers on every page
  * certifications: double-spaced block paragraphs, no first-line indent,
    paragraph numbers that run continuously through the document
  * the firm block and court caption come from the matter record

The right-hand Drafting Table shows images of the PDF pages (render_pages).
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily, stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .settings import ROOT

MARGIN = 1 * inch
SIZE = 12
SINGLE = 14.4
DOUBLE = 24
RULE = colors.Color(0.78, 0.78, 0.78)

# Times New Roman ships with macOS and Windows. On a bare Linux server drop the
# four .ttf files into bot/fonts/; otherwise the PDF falls back to the built-in
# Times face (same metrics family, not embedded).
_FONT_DIRS = [
    ROOT / "fonts",
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/msttcorefonts"),
]
_FONT_FILES = {
    "TNR": ("Times New Roman.ttf", "times.ttf", "Times_New_Roman.ttf"),
    "TNR-Bold": ("Times New Roman Bold.ttf", "timesbd.ttf", "Times_New_Roman_Bold.ttf"),
    "TNR-Italic": ("Times New Roman Italic.ttf", "timesi.ttf", "Times_New_Roman_Italic.ttf"),
    "TNR-BoldItalic": ("Times New Roman Bold Italic.ttf", "timesbi.ttf", "Times_New_Roman_Bold_Italic.ttf"),
}
_FONTS: dict[str, str] | None = None


def fonts() -> dict[str, str]:
    """Font names for normal / bold / italic / bold italic. Registers once."""
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    found = {}
    for name, candidates in _FONT_FILES.items():
        for directory in _FONT_DIRS:
            match = next((directory / c for c in candidates if (directory / c).exists()), None)
            if match:
                found[name] = match
                break
    if len(found) == len(_FONT_FILES):
        for name, path in found.items():
            pdfmetrics.registerFont(TTFont(name, str(path)))
        registerFontFamily("TNR", normal="TNR", bold="TNR-Bold", italic="TNR-Italic", boldItalic="TNR-BoldItalic")
        _FONTS = {"normal": "TNR", "bold": "TNR-Bold", "italic": "TNR-Italic", "embedded": "yes"}
    else:
        _FONTS = {"normal": "Times-Roman", "bold": "Times-Bold", "italic": "Times-Italic", "embedded": ""}
    return _FONTS


# --- inline markdown ---------------------------------------------------------


def inline(text: str) -> str:
    """Markdown inline marks -> the small tag set ReportLab paragraphs understand."""
    # Signature / fill-in lines (____) are not emphasis marks.
    out = re.sub(r"_{3,}", lambda m: "\x00" * len(m.group(0)), text.strip())
    out = escape(out)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2"><u>\1</u></a>', out)
    out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", out)
    out = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"__(.+?)__", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", out)
    out = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<i>\1</i>", out)
    out = re.sub(r"&lt;(/?)u&gt;", r"<\1u>", out)
    out = re.sub(r"&lt;br\s*/?&gt;", "<br/>", out)
    return out.replace("\x00", "_")


def plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", inline(text)).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


# --- markdown blocks ---------------------------------------------------------

_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)]


def parse_blocks(markdown: str) -> list[dict]:
    """A small, strict markdown reader: headings, paragraphs, lists, tables, quotes."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            i += 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            blocks.append({"type": "gap"})
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            blocks.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2).strip("# ")})
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]) and "-" in lines[i + 1]:
            header = _cells(stripped)
            aligns = []
            for mark in _cells(lines[i + 1]):
                if mark.startswith(":") and mark.endswith(":"):
                    aligns.append("center")
                elif mark.endswith(":"):
                    aligns.append("right")
                else:
                    aligns.append("left")
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = _cells(lines[i])
                rows.append((row + [""] * len(header))[: len(header)])
                i += 1
            aligns = (aligns + ["left"] * len(header))[: len(header)]
            blocks.append({"type": "table", "header": header, "aligns": aligns, "rows": rows})
            continue
        ordered = _ORDERED.match(line)
        bullet = _BULLET.match(line)
        if ordered or bullet:
            match = ordered or bullet
            text = [match.group(3) if ordered else match.group(2)]
            level = 1 if len(match.group(1)) >= 2 else 0
            i += 1
            # Wrapped lines of the same item.
            while i < len(lines) and lines[i].strip() and not (
                _ORDERED.match(lines[i]) or _BULLET.match(lines[i]) or lines[i].lstrip().startswith(("#", "|", ">"))
            ):
                text.append(lines[i].strip())
                i += 1
            blocks.append(
                {
                    "type": "item",
                    "ordered": bool(ordered),
                    "number": int(ordered.group(2)) if ordered else None,
                    "level": level,
                    "text": " ".join(text),
                }
            )
            continue
        if stripped.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append({"type": "quote", "text": " ".join(quote)})
            continue
        para = []
        while i < len(lines) and lines[i].strip():
            nxt = lines[i]
            if para and (
                re.match(r"^#{1,6}\s", nxt.strip())
                or nxt.strip().startswith(("|", ">"))
                or _ORDERED.match(nxt)
                or _BULLET.match(nxt)
            ):
                break
            # Two trailing spaces or a backslash keep the line break (signature blocks, addresses).
            hard = nxt.endswith("  ") or nxt.rstrip().endswith("\\")
            para.append(nxt.strip().rstrip("\\").strip() + ("\n" if hard else " "))
            i += 1
        blocks.append({"type": "para", "text": "".join(para).strip()})
    return blocks


# --- layout ------------------------------------------------------------------


def _styles(double: bool) -> dict[str, ParagraphStyle]:
    f = fonts()
    lead = DOUBLE if double else SINGLE
    base = ParagraphStyle("body", fontName=f["normal"], fontSize=SIZE, leading=lead, alignment=TA_LEFT,
                          spaceAfter=6 if double else 10)
    single = ParagraphStyle("single", parent=base, leading=SINGLE, spaceAfter=0)
    return {
        "body": base,
        "single": single,
        "title": ParagraphStyle("title", parent=single, fontName=f["bold"], alignment=TA_CENTER,
                                spaceBefore=6, spaceAfter=16),
        "h2": ParagraphStyle("h2", parent=single, fontName=f["bold"], spaceBefore=10, spaceAfter=8,
                             keepWithNext=True),
        "h3": ParagraphStyle("h3", parent=single, fontName=f["bold"], spaceBefore=8, spaceAfter=6,
                             keepWithNext=True),
        "item": ParagraphStyle("item", parent=base, leftIndent=30, bulletIndent=0, bulletFontName=f["normal"],
                               bulletFontSize=SIZE),
        "subitem": ParagraphStyle("subitem", parent=base, leftIndent=58, bulletIndent=30,
                                  bulletFontName=f["normal"], bulletFontSize=SIZE),
        "quote": ParagraphStyle("quote", parent=single, leftIndent=36, rightIndent=36, spaceAfter=10),
        "cell": ParagraphStyle("cell", parent=single),
        "cell_right": ParagraphStyle("cell_right", parent=single, alignment=TA_RIGHT),
        "cell_center": ParagraphStyle("cell_center", parent=single, alignment=TA_CENTER),
    }


def _caption(matter: dict, styles: dict, width: float) -> list:
    """Firm block, then the two-column court caption, as in the firm's filings."""
    flow: list = []
    block = [line for line in matter.get("firm_block") or [] if line]
    if block:
        flow.append(Paragraph("<br/>".join(escape(line) for line in block), styles["single"]))
        flow.append(Spacer(1, 16))
    plaintiff, defendant = matter.get("plaintiff"), matter.get("defendant")
    court = [line for line in matter.get("court") or [] if line]
    if not (plaintiff and defendant and court):
        return flow
    indent = "&nbsp;" * 8
    left = (
        f"{escape(plaintiff)},<br/>{indent}Plaintiff,<br/>v.<br/>"
        f"{escape(defendant)},<br/>{indent}Defendant."
    )
    right_lines = [escape(line) for line in court]
    if matter.get("docket"):
        right_lines.append(f"DOCKET NO. {escape(matter['docket'])}")
    if matter.get("action"):
        right_lines.append(escape(matter["action"]))
    table = Table(
        [[Paragraph(left, styles["single"]), Paragraph("<br/>".join(right_lines), styles["single"])]],
        colWidths=[width * 0.48, width * 0.52],
    )
    table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.9, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
                ("LINEAFTER", (0, 0), (0, 0), 0.9, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    flow.append(table)
    flow.append(Spacer(1, 18))
    return flow


def _table(block: dict, styles: dict, width: float) -> Table:
    f = fonts()
    header, rows, aligns = block["header"], block["rows"], block["aligns"]
    ncols = len(header)
    pad = 16

    def text_width(text: str, font: str) -> float:
        return stringWidth(plain(text), font, SIZE)

    natural, minimum = [], []
    for col in range(ncols):
        cells = [header[col]] + [row[col] for row in rows]
        natural.append(max(text_width(c, f["bold"]) for c in cells) + pad)
        longest_word = max((text_width(w, f["bold"]) for c in cells for w in plain(c).split()), default=0)
        minimum.append(max(longest_word, text_width(header[col], f["bold"])) + pad)

    widths = list(natural)
    if sum(widths) <= width:
        widths[natural.index(max(natural))] += width - sum(natural)  # the widest column takes the slack
    else:
        # Too wide: narrow columns keep their natural width, the widest ones wrap.
        over = sum(widths) - width
        while over > 0.5:
            widest = max(widths)
            top = [i for i, w in enumerate(widths) if w >= widest - 0.01]
            runner_up = max((w for w in widths if w < widest - 0.01), default=0.0)
            floor = max(runner_up, max(minimum[i] for i in top))
            room = (widest - floor) * len(top)
            if room <= 0:
                break
            cut = min(over, room) / len(top)
            for i in top:
                widths[i] -= cut
            over = sum(widths) - width
        if over > 0.5:
            widths = [w * width / sum(widths) for w in widths]

    def style_for(col: int) -> ParagraphStyle:
        return {"right": styles["cell_right"], "center": styles["cell_center"]}.get(aligns[col], styles["cell"])

    data = [[Paragraph(f"<b>{inline(header[c])}</b>", style_for(c)) for c in range(ncols)]]
    for row in rows:
        data.append([Paragraph(inline(row[c]), style_for(c)) for c in range(ncols)])

    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
    ]
    if len(data) > 1:
        commands.append(("LINEBELOW", (0, 1), (-1, -1), 0.5, RULE))
    table.setStyle(TableStyle(commands))
    return table


def _canvas_class(footer_left: str, title: str, author: str):
    f = fonts()

    class NumberedCanvas(canvas.Canvas):
        """Second pass at save time so every page can say 'Page 2 of 6'."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []
            self.setTitle(title)
            self.setAuthor(author)

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont(f["normal"], 10)
                self.setFillColor(colors.black)
                self.drawCentredString(LETTER[0] / 2, 0.55 * inch, f"Page {self._pageNumber} of {total}")
                if footer_left:
                    self.setFont(f["normal"], 8)
                    self.setFillColor(colors.Color(0.4, 0.4, 0.4))
                    self.drawString(MARGIN, 0.55 * inch, footer_left)
                super().showPage()
            super().save()

    return NumberedCanvas


def build_pdf(
    markdown: str,
    out_path: Path,
    *,
    matter: dict,
    title: str = "Draft",
    kind: str = "",
    caption: bool = True,
    draft_mark: bool = True,
) -> dict:
    """Write the PDF. Returns the real page count and what formatting was applied."""
    double = bool(re.search(r"certification|affidavit|brief", kind, flags=re.I))
    styles = _styles(double)
    width = LETTER[0] - 2 * MARGIN
    story: list = []
    if caption:
        story.extend(_caption(matter, styles, width))

    number = 0  # certification paragraphs are numbered straight through
    for block in parse_blocks(markdown):
        kind_ = block["type"]
        if kind_ == "heading":
            if block["level"] == 1:
                story.append(Paragraph(inline(block["text"].upper()), styles["title"]))
            else:
                style = styles["h2"] if block["level"] == 2 else styles["h3"]
                story.append(Paragraph(inline(block["text"]), style))
        elif kind_ == "para":
            story.append(Paragraph(inline(block["text"]).replace("\n", "<br/>"), styles["body"]))
        elif kind_ == "item":
            style = styles["subitem"] if block["level"] else styles["item"]
            if block["ordered"] and not block["level"]:
                number = number + 1 if double else block["number"]
                mark = f"{number}."
            elif block["ordered"]:
                mark = f"{block['number']}."
            else:
                mark = "\u2022"
            story.append(Paragraph(inline(block["text"]), style, bulletText=mark))
        elif kind_ == "quote":
            story.append(Paragraph(inline(block["text"]), styles["quote"]))
        elif kind_ == "table":
            story.append(_table(block, styles, width))
            story.append(Spacer(1, 12))
        elif kind_ == "gap":
            story.append(Spacer(1, 10))
    if not story:
        story.append(Paragraph("(empty draft)", styles["body"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        # The page frame pads its content by 6 points, so text lands exactly one inch in.
        leftMargin=MARGIN - 6,
        rightMargin=MARGIN - 6,
        topMargin=MARGIN - 6,
        bottomMargin=MARGIN - 6,
        title=title,
    )
    footer = "DRAFT FOR ATTORNEY REVIEW" if draft_mark else ""
    doc.build(story, canvasmaker=_canvas_class(footer, title, "Tanya Freeman Law, LLC"))
    return {
        "pages": doc.page,
        "font": "Times New Roman" if fonts()["embedded"] else "Times (built-in fallback)",
        "double_spaced": double,
    }


def render_pages(pdf_path: Path, out_dir: Path, scale: float = 2.0) -> int:
    """PDF pages -> PNG images for the on-screen preview. Returns the page count."""
    import pypdfium2 as pdfium

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        total = len(pdf)
        for index in range(total):
            page = pdf[index]
            page.render(scale=scale).to_pil().save(out_dir / f"page-{index + 1}.png")
    finally:
        pdf.close()
    return total
