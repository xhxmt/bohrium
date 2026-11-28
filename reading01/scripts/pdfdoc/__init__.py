"""pdfdoc - layout-aware scientific PDF parser used by the weekly-skill.

Pipeline: backends (words with geometry/fonts) -> layout (furniture, columns,
reading order) -> structure (blocks, headings, captions, equations, tables,
footnotes, references, cross-page continuation) -> export (JSON / Markdown /
tables / previews / QA report).

Only the Python standard library is required.  Optional accelerators are
used when importable / on PATH: PyMuPDF, pdfplumber, poppler-utils
(pdftotext, pdftoppm), tesseract (OCR), Pillow (previews).  Everything runs
locally; no document is sent over the network.
"""
__version__ = "1.2.0"
