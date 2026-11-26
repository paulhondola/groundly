from PIL import Image

from bench.common.corpus import census
from bench.extract.drafts import write_drafts
from tests.bench.test_stacks import page_doc
from tests.ingestion.pdf_fixtures import Page, make_pdf


def reader(name):
    def read(path, pages, lang):
        for n in pages or [1]:
            yield n, page_doc(n, text=name)

    return read


def test_drafts_writes_both_engines_and_the_image(tmp_path):
    (tmp_path / "src").mkdir()
    pdf = make_pdf(tmp_path / "src" / "d.pdf", [Page(image_cover=0.5)] * 2)
    pdf_row = {**census([("s", pdf)])[1], "source": "pdf", "ref": True}
    png = tmp_path / "src" / "scan.png"
    Image.new("RGB", (32, 32)).save(png)
    img_row = {"sha256": "ab", "page": 1, "path_hint": str(png), "source": "image", "ref": True}
    other = {**pdf_row, "page": 1, "ref": False}
    ref_dir = tmp_path / "ref"
    readers = {"docling": reader("docling"), "mineru": reader("mineru")}

    write_drafts([pdf_row, img_row, other], ref_dir, readers)

    sha = pdf_row["sha256"]
    assert {str(f.relative_to(ref_dir)) for f in ref_dir.rglob("*") if f.is_file()} == {
        f"drafts/{sha}-2.docling.md",
        f"drafts/{sha}-2.mineru.md",
        f"images/{sha}-2.png",
        "drafts/ab-1.docling.md",
        "drafts/ab-1.mineru.md",
        "images/ab-1.png",
    }
    assert (ref_dir / f"drafts/{sha}-2.mineru.md").read_text() == "mineru 2"
