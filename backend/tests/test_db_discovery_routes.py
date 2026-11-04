"""Database discovery HTTP routes."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from routes.db_discovery import validate_subpath
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app_state.progress_tracker = ProgressTracker()
    app_state.storage_manager = StorageManager(base_path=str(tmp_path / "analyses"))
    app_state.db_discovery_storage = StorageManager(base_path=str(tmp_path / "db"))
    return TestClient(app)


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "db/Website/GetThing.sql",
            "CREATE PROCEDURE Website.GetThing AS\nBEGIN\n  MERGE Sales.T AS t USING s ON t.id = s.id\n"
            "  WHEN MATCHED THEN UPDATE SET a = 1;\nEND\n",
        )
        zf.writestr(
            "db/Sales/T.sql", "CREATE TABLE Sales.T (id uniqueidentifier NOT NULL)\n"
        )
        zf.writestr(
            "app/Repo.cs",
            'using System.Data.SqlClient;\nclass R { void A() { var c = new SqlCommand("Website.GetThing", x);'
            " c.CommandType = CommandType.StoredProcedure; } }\n",
        )
        zf.writestr("../evil.sql", "CREATE TABLE dbo.Evil (a int)")
    return buf.getvalue()


def test_upload_full_lifecycle(client: TestClient):
    res = client.post(
        "/api/db-discovery/upload",
        files={"file": ("db.zip", _zip(), "application/zip")},
    )
    assert res.status_code == 200, res.text
    discovery_id = res.json()["discovery_id"]
    assert discovery_id.startswith("dbupload_")

    # TestClient runs background tasks before returning, so the run is already complete.
    status = client.get(f"/api/db-discovery/{discovery_id}/status").json()
    assert status["status"] == "completed", status
    assert status["progress"] == 100

    report = client.get(f"/api/db-discovery/{discovery_id}").json()
    assert report["discovery_id"] == discovery_id
    ids = {o["id"] for o in report["objects"]}
    assert {"procedure:website.getthing", "table:sales.t"} <= ids
    assert not any("Evil" in o["name"] for o in report["objects"])
    proc = next(o for o in report["objects"] if o["id"] == "procedure:website.getthing")
    assert proc["called_from_code"] == ["app/Repo.cs:2"]
    assert any(f["rule_id"] == "SYN_MERGE" for f in report["findings"])

    md = client.get(f"/api/db-discovery/{discovery_id}/report.md")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert "# Database Discovery" in md.text

    listing = client.get("/api/db-discoveries").json()["discoveries"]
    assert [d["discovery_id"] for d in listing] == [discovery_id]
    assert listing[0]["object_counts"]["procedure"] == 1
    # Discoveries are stored apart from code analyses.
    assert client.get("/api/analyses").json()["analyses"] == []

    assert client.delete(f"/api/db-discovery/{discovery_id}").json() == {
        "detail": "Deleted"
    }
    assert client.get(f"/api/db-discovery/{discovery_id}").status_code == 404
    assert client.delete(f"/api/db-discovery/{discovery_id}").status_code == 404


def test_two_uploads_in_the_same_second_get_distinct_ids(client: TestClient):
    a = client.post(
        "/api/db-discovery/upload", files={"file": ("a.zip", _zip(), "application/zip")}
    ).json()
    b = client.post(
        "/api/db-discovery/upload", files={"file": ("b.zip", _zip(), "application/zip")}
    ).json()
    assert a["discovery_id"] != b["discovery_id"]


def test_upload_rejects_non_zip(client: TestClient):
    assert (
        client.post(
            "/api/db-discovery/upload", files={"file": ("a.sql", b"x", "text/plain")}
        ).status_code
        == 400
    )
    bad = client.post(
        "/api/db-discovery/upload",
        files={"file": ("a.zip", b"not a zip", "application/zip")},
    )
    assert bad.status_code == 400


def test_rules_catalogue(client: TestClient):
    rules = client.get("/api/db-discovery/rules").json()["rules"]
    ids = {r["id"] for r in rules}
    assert {"SYN_MERGE", "APP_CONN_WINDOWS_AUTH", "SCHED_SQL_AGENT"} <= ids


def test_invalid_and_unknown_ids(client: TestClient):
    assert client.get("/api/db-discovery/bad$id/status").status_code == 400
    assert (
        client.get("/api/db-discovery/dbupload_19990101_000000/status").status_code
        == 404
    )
    assert client.get("/api/db-discovery/dbupload_19990101_000000").status_code == 404


def test_github_validation(client: TestClient):
    assert (
        client.post("/api/db-discovery/github", json={"repo_url": ""}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/db-discovery/github", json={"repo_url": "file:///etc"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/db-discovery/github", json={"repo_url": "https://127.0.0.1/x"}
        ).status_code
        == 400
    )
    res = client.post(
        "/api/db-discovery/github",
        json={"repo_url": "https://github.com/a/b", "subpath": "../../etc"},
    )
    assert res.status_code == 400


def test_github_run_uses_sparse_clone(client: TestClient, tmp_path: Path, monkeypatch):
    repo = tmp_path / "clone"
    (repo / "samples" / "db").mkdir(parents=True)
    (repo / "samples" / "db" / "v.sql").write_text(
        "CREATE VIEW dbo.V AS SELECT TOP 1 a FROM dbo.T"
    )
    (repo / "other.sql").write_text("CREATE TABLE dbo.OutsideSubpath (a int)")
    seen: dict = {}

    class FakeHandler:
        def clone(self, repo_url, branch, pat_token, timeout, sparse_paths):
            seen.update(repo_url=repo_url, branch=branch, sparse_paths=sparse_paths)
            return str(repo)

        def cleanup(self, path):
            seen["cleaned"] = path

        def _validate_url(self, url):
            return None

    import services.github_handler as gh

    monkeypatch.setattr(gh, "GitHubHandler", FakeHandler)
    res = client.post(
        "/api/db-discovery/github",
        json={
            "repo_url": "https://github.com/a/b",
            "branch": "master",
            "subpath": "/samples/db/",
        },
    )
    assert res.status_code == 200, res.text
    discovery_id = res.json()["discovery_id"]
    assert seen["sparse_paths"] == ["samples/db"] and seen["branch"] == "master"
    assert seen["cleaned"] == str(repo)
    report = client.get(f"/api/db-discovery/{discovery_id}").json()
    assert (
        report["subpath"] == "samples/db"
        and report["source_url"] == "https://github.com/a/b"
    )
    assert [o["id"] for o in report["objects"]] == ["view:dbo.v"]


def test_github_failure_hides_token(client: TestClient, monkeypatch):
    class FailingHandler:
        def clone(self, **kwargs):
            raise RuntimeError(
                "fatal: could not read from https://ghp_secret123@github.com/a/b"
            )

        def cleanup(self, path):
            pass

        def _validate_url(self, url):
            return None

    import services.github_handler as gh

    monkeypatch.setattr(gh, "GitHubHandler", FailingHandler)
    res = client.post(
        "/api/db-discovery/github",
        json={"repo_url": "https://github.com/a/b", "pat_token": "ghp_secret123"},
    )
    status = client.get(f"/api/db-discovery/{res.json()['discovery_id']}/status").json()
    assert status["status"] == "failed"
    assert "ghp_secret123" not in status["message"] and "***" in status["message"]


@pytest.mark.parametrize(
    "bad", ["../x", "a/../../b", "a//b", "a;rm -rf", "~/x", "a\\b"]
)
def test_validate_subpath_rejects(bad: str):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        validate_subpath(bad)


def test_validate_subpath_normalises():
    assert validate_subpath(" /samples/databases/wide-world-importers/wwi-ssdt/ ") == (
        "samples/databases/wide-world-importers/wwi-ssdt"
    )
    assert validate_subpath("") == ""
