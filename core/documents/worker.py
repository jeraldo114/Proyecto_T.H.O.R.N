"""Proceso de lectura PDF: sin rutas del operador, herramientas ni credenciales."""

from __future__ import annotations

import io
import json
import sys

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 40
MAX_DOCUMENT_TEXT = 6000


def parse_pdf(raw: bytes) -> dict[str, object]:
    """Extrae texto; nunca ejecuta JavaScript, formularios ni enlaces de un PDF."""
    from pypdf import PdfReader

    if not raw.startswith(b"%PDF-") or len(raw) > MAX_PDF_BYTES:
        raise ValueError("document_invalid_pdf")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted:
        raise ValueError("document_encrypted")
    if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
        raise ValueError("document_page_limit")
    remaining = MAX_DOCUMENT_TEXT
    pages = []
    truncated = False
    for number, page in enumerate(reader.pages, 1):
        if not remaining:
            truncated = True
            break
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 8 * 1024 * 1024:
            raise ValueError("document_content_limit")
        text = page.extract_text() or ""
        truncated |= len(text) > remaining
        clipped = text[:remaining]
        remaining -= len(clipped)
        pages.append({"page": number, "text": clipped})
    return {"page_count": len(reader.pages), "pages": pages, "truncated": truncated,
            "text_available": any(p["text"].strip() for p in pages), "ocr": False}


def main() -> int:
    """El padre controla tiempo/memoria y descarta errores con contenido privado."""
    try:
        raw = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        result = {"document": parse_pdf(raw)}
    except Exception as error:
        allowed = {"document_invalid_pdf", "document_encrypted", "document_page_limit", "document_content_limit"}
        result = {"error": str(error) if str(error) in allowed else "document_invalid_pdf"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
