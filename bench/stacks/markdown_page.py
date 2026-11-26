"""A reader's Markdown for one page as a one-page DoclingDocument, so a VLM stack's
output goes through the same `chunk_pages` as docling's own and every chunk cites
its page."""

import functools
from io import BytesIO

from docling_core.types.doc import BoundingBox, DocItem, DoclingDocument, ProvenanceItem, Size

# Markdown has no geometry: every item gets the whole (unit) page as its box.
_PAGE = Size(width=1, height=1)
_BOX = BoundingBox(l=0, t=1, r=1, b=0)


@functools.cache
def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter

    return DocumentConverter(allowed_formats=[InputFormat.MD])


def markdown_page(md: str, n: int) -> DoclingDocument:
    if md.strip():
        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(name=f"page-{n}.md", stream=BytesIO(md.encode()))
        doc = _converter().convert(stream).document
    else:
        doc = DoclingDocument(name=f"page-{n}")
    doc.add_page(page_no=n, size=_PAGE)
    for item, _ in doc.iterate_items():
        if isinstance(item, DocItem):
            span = (0, len(getattr(item, "text", "") or ""))
            item.prov = [ProvenanceItem(page_no=n, bbox=_BOX, charspan=span)]
    return doc
