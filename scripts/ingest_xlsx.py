"""
Turns the hardware failure workbook into the flat .txt corpus that the rest
of the pipeline reads.

The retrieval and graph stages both read data/docs, so the workbook is the
source of truth and this script is the only thing that knows about xlsx.
Keeping the intermediate .txt files on disk means the corpus stays greppable
and diffable, and build_index.py / extract_graph.py need no changes at all.

Each row becomes one document:

    TF001: Board fails power on self test at low temp
    <blank line>
    Type: ... Area: ... Severity: ...
    What happened and cause: ...
    Resolution: ...
    Related documents: ...

The first line is the title and everything after the blank line is the body,
which is the split chunking._read_docs() expects.

Run this, then build_index.py, then extract_graph.py.
"""

import os
import sys

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import DOCS_DIR, XLSX_PATH

SHEET = "Documents"


def row_to_text(row) -> tuple[str, str]:
    doc_id, typ, title, area, sev, date, status, cause, resolution, related = (
        list(row) + [None] * 10
    )[:10]

    header = f"{doc_id}: {title}"

    # Type/Area/Severity/Status/Date are not written into the body. They
    # are around 15 words of near identical boilerplate per document, a
    # quarter of a 60 word chunk spent on text that does not distinguish
    # one document from another.
    body_parts = []
    if cause:
        body_parts.append(f"What happened and cause: {cause}")
    if resolution:
        body_parts.append(f"Resolution: {resolution}")
    if related:
        # Stated in prose as well as in the column, because the graph
        # extraction reads sentences, not spreadsheet structure.
        body_parts.append(f"Related documents: {related}.")

    return header, "\n\n".join(body_parts)


def main() -> int:
    # Optional workbook path so a second corpus can be ingested without
    # editing config. Defaults to the one named in config.XLSX_PATH.
    path = sys.argv[1] if len(sys.argv) > 1 else XLSX_PATH
    if not os.path.exists(path):
        sys.exit(f"workbook not found: {path}")
    print(f"source: {os.path.basename(path)}")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        sys.exit(f"sheet {SHEET!r} not in {wb.sheetnames}")

    rows = list(wb[SHEET].iter_rows(values_only=True))
    data = [r for r in rows[1:] if r and r[0]]
    if not data:
        sys.exit("no data rows found")

    os.makedirs(DOCS_DIR, exist_ok=True)

    # Regenerate cleanly so a row deleted from the workbook does not linger
    # on disk and keep showing up in search results.
    stale = [f for f in os.listdir(DOCS_DIR) if f.endswith(".txt")]
    for f in stale:
        os.remove(os.path.join(DOCS_DIR, f))
    if stale:
        print(f"removed {len(stale)} existing .txt files")

    written = 0
    for row in data:
        title, body = row_to_text(row)
        path = os.path.join(DOCS_DIR, f"{row[0]}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"{title}\n\n{body}\n")
        written += 1

    print(f"wrote {written} documents to {DOCS_DIR}")
    return written


if __name__ == "__main__":
    main()
