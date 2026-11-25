from pathlib import Path

from fastapi.testclient import TestClient

from groundly.web.app import create_app
from groundly.web.openapi import export_openapi, format_openapi

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "web" / "openapi.json"


def test_committed_openapi_json_matches_fastapi_schema():
    expected = format_openapi(export_openapi())
    assert OPENAPI_PATH.exists(), "run: uv run python -m groundly.web.openapi"
    assert OPENAPI_PATH.read_text(encoding="utf-8") == expected


def test_openapi_schema_contains_only_api_paths_and_typed_errors():
    spec = export_openapi()
    paths = spec["paths"]
    assert len(paths) == 17
    for expected_path in (
        "/api/auth",
        "/api/pair",
        "/api/remote",
        "/api/remote/devices/{id}/revoke",
        "/api/remote/revoke-all",
        "/api/subjects/{subject}/questions/{id}/check",
    ):
        assert expected_path in paths
    assert all(path.startswith("/api/") for path in paths)
    assert "ErrorResponse" in spec["components"]["schemas"]
    for path, operations in paths.items():
        if path.startswith(("/api/subjects", "/api/runs")):
            for operation in operations.values():
                for code in ("401", "500", "503"):
                    schema = operation["responses"][code]["content"]["application/json"]["schema"]
                    assert schema == {"$ref": "#/components/schemas/ErrorResponse"}
    submit_schema = paths["/api/subjects/{subject}/attempts"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert "SubmittedChoiceAnswer" in str(submit_schema) or "selected" in str(submit_schema)
    assert "SubmittedOpenAnswer" in str(submit_schema) or "text" in str(submit_schema)


def test_runtime_openapi_and_docs_remain_disabled():
    client = TestClient(create_app(), base_url="http://127.0.0.1:8765")
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404
