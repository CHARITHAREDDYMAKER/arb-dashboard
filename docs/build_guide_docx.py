"""Convert docs/Dashboard_Guide.md to docs/Dashboard_Guide.docx, with the screenshots embedded.

    python docs/build_guide_docx.py

Handles the Markdown this guide uses: headings, paragraphs, bullet and numbered
lists (one nested level), tables, code blocks, images, **bold** and `code`.
Needs python-docx and Pillow (`pip install python-docx pillow`).

Screenshots are full-page captures, far taller than a page. Each one is cropped
to the main panel (no sidebar), the repeated header is removed for every tab
after the Overview, and the rest is cut into page-sized slices.
"""

import io
import json
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

DOCS = Path(__file__).resolve().parent
SOURCE, TARGET = DOCS / "Dashboard_Guide.md", DOCS / "Dashboard_Guide.docx"
SCREENS = DOCS.parent / "reports" / "screens"

PAGE_WIDTH_CM, MARGIN_CM = 21.0, 2.0
TEXT_WIDTH_CM = PAGE_WIDTH_CM - 2 * MARGIN_CM
SIDEBAR_PX = 300  # width of the dashboard sidebar in the screenshots
SLICE_RATIO = 1.25  # tallest slice, as a multiple of its width
ACCENT = RGBColor(0x1F, 0x5F, 0xB8)
HEADER_FILL = "DCE8F7"

INLINE = re.compile(r"(\*\*.+?\*\*|`.+?`)")


def add_inline(paragraph, text: str) -> None:
    """Add text to a paragraph, turning **bold** and `code` into formatted runs."""
    for part in INLINE.split(text):
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name, run.font.size = "Consolas", Pt(9.5)
        elif part:
            paragraph.add_run(part)


def shade(cell, fill: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shading)


def add_table(document, rows: list[list[str]]) -> None:
    table = document.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            cell = table.cell(row_index, column_index)
            paragraph = cell.paragraphs[0]
            add_inline(paragraph, text)
            for run in paragraph.runs:
                run.font.size = Pt(9)
                run.bold = run.bold or row_index == 0
            if row_index == 0:
                shade(cell, HEADER_FILL)
    document.add_paragraph()


def screenshot_slices(path: Path, keep_header: bool) -> list[Image.Image]:
    """Crop a full-page screenshot to the main panel and cut it into page-sized pieces."""
    image = Image.open(path).convert("RGB")
    layout = json.loads((SCREENS / "layout.json").read_text()) if (SCREENS / "layout.json").exists() else {}
    top = 0 if keep_header else max(layout.get(path.name, 0) - 12, 0)
    image = image.crop((SIDEBAR_PX, top, image.width, image.height))
    slice_count = max(1, round(image.height / (image.width * SLICE_RATIO) + 0.35))
    slice_height = -(-image.height // slice_count)
    return [image.crop((0, start, image.width, min(start + slice_height, image.height))) for start in range(0, image.height, slice_height)]


def add_image(document, path: Path, caption: str, keep_header: bool) -> None:
    slices = screenshot_slices(path, keep_header)
    for number, piece in enumerate(slices, start=1):
        buffer = io.BytesIO()
        piece.save(buffer, format="PNG")
        buffer.seek(0)
        document.add_picture(buffer, width=Cm(TEXT_WIDTH_CM))
        label = caption + (f" (part {number} of {len(slices)})" if len(slices) > 1 else "")
        note = document.add_paragraph(label)
        note.runs[0].italic, note.runs[0].font.size = True, Pt(8.5)


def parse_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def build() -> None:
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(PAGE_WIDTH_CM), Cm(29.7)
    section.left_margin = section.right_margin = Cm(MARGIN_CM)
    section.top_margin = section.bottom_margin = Cm(2.0)
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10.5)
    for level, size in ((1, 17), (2, 13.5), (3, 11.5), (4, 10.5)):
        style = document.styles[f"Heading {level}"]
        style.font.name, style.font.size, style.font.color.rgb = "Calibri", Pt(size), ACCENT

    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    index, first_image = 0, True
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
        elif line.startswith("```"):
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code = document.add_paragraph()
                run = code.add_run(lines[index])
                run.font.name, run.font.size = "Consolas", Pt(9)
                code.paragraph_format.space_after = Pt(0)
                code.paragraph_format.left_indent = Cm(0.6)
                index += 1
            index += 1
            document.add_paragraph()
        elif line.startswith("# "):
            document.add_heading(line[2:], level=0)
            index += 1
        elif match := re.match(r"(#{2,5}) (.+)", line):
            level = len(match.group(1)) - 1
            if level == 1 and index > 3:  # each numbered section starts on a new page
                document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            document.add_heading(match.group(2), level=min(level, 4))
            index += 1
        elif match := re.match(r"!\[(.*?)\]\((.+?)\)", line):
            add_image(document, (DOCS / match.group(2)).resolve(), match.group(1), keep_header=first_image)
            first_image = False
            index += 1
        elif line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|$", lines[index]):
                    rows.append(parse_table_row(lines[index]))
                index += 1
            add_table(document, rows)
        elif match := re.match(r"(\s*)- (.+)", line):
            style = "List Bullet 2" if len(match.group(1)) >= 2 else "List Bullet"
            add_inline(document.add_paragraph(style=style), match.group(2))
            index += 1
        elif match := re.match(r"\d+\. (.+)", line):
            add_inline(document.add_paragraph(style="List Number"), match.group(1))
            index += 1
        else:
            add_inline(document.add_paragraph(), line)
            index += 1

    document.save(TARGET)
    print(f"wrote {TARGET.relative_to(DOCS.parent)}")


if __name__ == "__main__":
    build()
