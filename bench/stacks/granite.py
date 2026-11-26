"""granite-docling 258M on MLX through docling's VlmPipeline, one page per conversion, so a
finished page is handed on at once and none waits on a batch (a capped page can take a
minute). Needs the `bench` dependency group; `ocr_lang` has no meaning for a VLM and is
ignored."""

import functools
import sys
from collections.abc import Iterator
from pathlib import Path

from bench.stacks import caps
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

    def convert(n: int):
        result = converter.convert(path, page_range=(n, n))
        _count_caps(result)
        doc = result.document
        # docling 2.115's VlmPipeline numbers a range's pages from 1; map it back
        src = n if n in doc.pages else 1
        return result, (doc.filter(page_nrs={src}) if src in doc.pages else None)

    def one(n: int, converted) -> Iterator:
        if converted[1] is not None:
            yield n, converted[1]

    if pages is not None:
        for n in sorted(pages):
            yield from one(n, convert(n))
        return
    first = convert(1)
    yield from one(1, first)
    for n in range(2, first[0].input.page_count + 1):
        yield from one(n, convert(n))


if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
