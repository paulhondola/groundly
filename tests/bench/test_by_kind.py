from PIL import Image

from bench.stacks.by_kind import by_kind
from tests.bench.test_stacks import page_doc
from tests.ingestion.pdf_fixtures import Page, make_pdf

TEXT = Page(lines=[("A graph is a pair of sets of vertices and edges.", 20)])
IMAGE = Page(image_cover=0.5)


def reader():
    calls: list[list[int] | None] = []

    def read(path, pages, ocr_lang):
        calls.append(pages)
        for n in pages:
            yield n, page_doc(n)

    read.calls = calls
    return read


def setup(tmp_path):
    path = make_pdf(tmp_path / "d.pdf", [TEXT, IMAGE, TEXT, IMAGE])
    image, default = reader(), reader()
    return path, image, default, by_kind({"image": image}, default=default)


def test_image_pages_go_to_their_reader_and_the_rest_to_default(tmp_path):
    path, image, default, read = setup(tmp_path)
    list(read(path, None, None))
    assert [n for c in image.calls for n in c] == [2, 4]
    assert [n for c in default.calls for n in c] == [1, 3]


def test_pages_come_back_once_in_ascending_order(tmp_path):
    path, _, _, read = setup(tmp_path)
    assert [n for n, _ in read(path, None, None)] == [1, 2, 3, 4]


def test_a_standalone_image_is_always_image(tmp_path):
    png = tmp_path / "s.png"
    Image.new("RGB", (32, 32)).save(png)
    image, default = reader(), reader()
    assert [n for n, _ in by_kind({"image": image}, default=default)(png, None, None)] == [1]
    assert image.calls == [[1]] and default.calls == []


def test_requested_pages_only(tmp_path):
    path, image, default, read = setup(tmp_path)
    assert [n for n, _ in read(path, [4, 3], None)] == [3, 4]
    assert default.calls == [[3]] and image.calls == [[4]]


def test_none_means_every_page(tmp_path):
    path, image, default, read = setup(tmp_path)
    list(read(path, None, None))
    assert default.calls == [[1], [3]] and image.calls == [[2], [4]]
