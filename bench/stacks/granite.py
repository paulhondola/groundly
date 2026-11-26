"""granite-docling 258M on MLX through docling's VlmPipeline, read page by page in the
product's batch shape. Needs the optional `bench` extra; `ocr_lang` has no meaning for a
VLM and is ignored."""

import functools
import sys
from collections.abc import Iterator
from pathlib import Path

from bench.stacks import caps
from groundly.ingestion.extract_worker import PAGE_BATCH, _page_runs
from groundly.ingestion.formats import IMAGE_SUFFIXES

SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES
MAX_TOKENS = 8192


@functools.cache
def _converter():
    from docling.datamodel import vlm_model_specs
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmPipelineOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    vlm = vlm_model_specs.GRANITEDOCLING_MLX.model_copy(update={"max_new_tokens": MAX_TOKENS})
    kw = {"pipeline_cls": VlmPipeline, "pipeline_options": VlmPipelineOptions(vlm_options=vlm)}
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(**kw),
            InputFormat.IMAGE: ImageFormatOption(**kw),
        }
    )


def _count_caps(result) -> None:
    from docling.datamodel.base_models import VlmStopReason

    for page in result.pages:
        r = page.predictions.vlm_response
        # MLX reports UNSPECIFIED on a normal stop, so the token count backs up the reason
        if r and (r.stop_reason == VlmStopReason.LENGTH or (r.num_tokens or 0) >= MAX_TOKENS):
            caps.hit(page.page_no)


def read(path: Path, pages: list[int] | None, ocr_lang: str | None) -> Iterator:
    converter = _converter()

    def convert(start: int, end: int):
        result = converter.convert(path, page_range=(start, end))
        _count_caps(result)
        return result

    def split(result, start: int, end: int):
        doc = result.document
        # docling 2.115's VlmPipeline numbers a range's pages from 1; map them back
        shift = start - 1 if doc.pages and min(doc.pages) < start else 0
        for n in range(start, end + 1):
            if n - shift in doc.pages:
                yield n, doc.filter(page_nrs={n - shift})

    if pages is not None:
        for start, end in _page_runs(pages):
            yield from split(convert(start, end), start, end)
        return
    first = convert(1, PAGE_BATCH)
    total = first.input.page_count
    yield from split(first, 1, PAGE_BATCH)
    for start in range(PAGE_BATCH + 1, total + 1, PAGE_BATCH):
        yield from split(convert(start, min(start + PAGE_BATCH - 1, total)), start, total)


if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
