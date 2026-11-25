import io
import json
import subprocess
import sys
import textwrap

from groundly.ingestion import extract_worker as w


def _run(handler, n):
    reqs = "".join(
        json.dumps({"in": f"/x{i}", "out": f"/o{i}", "ocr_lang": None}) + "\n" for i in range(n)
    )
    out = io.StringIO()
    w.serve(io.StringIO(reqs), out, handle=handler)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    return [line["status"] for line in lines if "status" in line]


def test_serve_maps_each_outcome_to_a_status_and_keeps_going():
    outcomes = iter(
        [
            None,
            SystemExit(w.EXIT_NO_TEXT),
            SystemExit(w.EXIT_MODEL_UNAVAILABLE),
            SystemExit(w.EXIT_INPUT_TOO_LARGE),
            SystemExit("not a readable nbformat 4 notebook"),
            ValueError("docling blew up"),
            None,
        ]
    )

    def handler(i, o, lang, on_page):
        e = next(outcomes)
        if e:
            raise e

    assert _run(handler, 7) == [
        "ok",
        "no_text",
        "model_unavailable",
        "input_too_large",
        "error",
        "error",
        "ok",
    ]


def test_serve_writes_the_cause_to_stderr_before_replying(capsys):
    def handler(i, o, lang, on_page):
        raise SystemExit("not a readable nbformat 4 notebook: boom")

    _run(handler, 1)
    assert capsys.readouterr().err.strip().splitlines()[-1].endswith("boom")


def test_an_unmapped_exit_code_reaches_stderr_as_the_cause(capsys):
    def handler(i, o, lang, on_page):
        raise SystemExit(7)

    assert _run(handler, 1) == ["error"]
    assert capsys.readouterr().err.strip().splitlines()[-1] == "exit code 7"


def test_library_stdout_never_reaches_the_protocol_stream():
    code = textwrap.dedent("""
        import os
        from groundly.ingestion import extract_worker as w
        def h(i, o, lang, on_page):
            print("noise")
            os.write(1, b"raw noise\\n")
            on_page(1)
        w.serve_stdio(handle=h)
    """)
    req = json.dumps({"in": "/a", "out": "/b", "ocr_lang": None}) + "\n"
    r = subprocess.run(
        [sys.executable, "-c", code], input=req, capture_output=True, text=True, check=True
    )
    assert [json.loads(line) for line in r.stdout.splitlines()] == [{"page": 1}, {"status": "ok"}]


def test_page_lines_precede_the_status_line():
    req = json.dumps({"in": "/a", "out": "/b", "ocr_lang": None}) + "\n"
    out = io.StringIO()
    w.serve(io.StringIO(req), out, handle=lambda i, o, lang, on_page: (on_page(1), on_page(2)))
    assert [json.loads(line) for line in out.getvalue().splitlines()] == [
        {"page": 1},
        {"page": 2},
        {"status": "ok"},
    ]
