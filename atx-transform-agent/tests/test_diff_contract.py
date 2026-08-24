"""Diff response contract tests for the ATX Transform Agent.

The transform results page renders ``EnhancedFileComparison``, which keys tabs on
``filename`` and rows on ``lines[]`` (``{type, content, old_line_number,
new_line_number}`` after the page's snake_case → camelCase normalisation). The
previous payload emitted ``path`` plus ``before``/``after``/``diff``, so every file
rendered as "unknown" with zero lines and nothing raised — the ``as`` casts and
``?? 'unknown'`` / ``?? []`` fallbacks swallowed it.

These tests pin the shape the renderer actually consumes, plus the payload bounds.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from services import storage_service
from services.file_comparison import (
    MAX_CHANGED_FILES,
    MAX_LINES_PER_FILE,
    MAX_TOTAL_LINES,
    get_diff_summary,
    get_file_diff,
)


@pytest.fixture
def tmp_storage(tmp_path):
    """Point the transform agent's storage at a temporary directory."""
    storage = tmp_path / "storage"
    storage.mkdir()
    with patch("config.settings.storage_path", str(storage)):
        yield storage


def _register(repo_id: str, status: str = "completed") -> dict:
    """Persist a transformation record the way ``POST /transform`` does."""
    return storage_service.write_record(
        {
            "repo_id": repo_id,
            "status": status,
            "created_at": "2025-01-01T00:00:00+00:00",
            "repo_url": "https://github.com/org/repo",
            "branch": "main",
            "transformation_type": "AWS/java-version-upgrade",
        }
    )


def _make_transformation(storage, repo_id: str, original: dict[str, str], transformed: dict[str, str]) -> None:
    """Materialise an original/repo tree pair on disk."""
    for tree_name, files in (("original", original), ("repo", transformed)):
        for rel_path, content in files.items():
            target = storage / repo_id / tree_name / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        (storage / repo_id / tree_name).mkdir(parents=True, exist_ok=True)


# --- The renderer's contract ---


def test_diff_files_carry_filename_and_populated_lines(tmp_storage):
    """``filename`` and a non-empty ``lines[]`` — the fields the renderer reads."""
    repo_id = "diff-shape"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"pom.xml": "<version>8</version>\n"},
        transformed={"pom.xml": "<version>17</version>\n"},
    )

    result = get_file_diff(repo_id)

    assert len(result["files"]) == 1
    entry = result["files"][0]
    assert entry["filename"] == "pom.xml"
    assert isinstance(entry["lines"], list) and entry["lines"], "lines[] must be populated"
    for line in entry["lines"]:
        assert line["type"] in ("added", "removed", "unchanged")
        assert "content" in line
        assert "old_line_number" in line
        assert "new_line_number" in line


def test_modified_file_yields_both_added_and_removed_lines(tmp_storage):
    repo_id = "diff-modified"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"App.java": "class App {\n  int x = 1;\n}\n"},
        transformed={"App.java": "class App {\n  int x = 2;\n}\n"},
    )

    entry = get_file_diff(repo_id)["files"][0]
    types = {line["type"] for line in entry["lines"]}

    assert "added" in types and "removed" in types
    assert entry["status"] == "modified"

    removed = [line for line in entry["lines"] if line["type"] == "removed"]
    added = [line for line in entry["lines"] if line["type"] == "added"]
    assert removed[0]["content"] == "  int x = 1;"
    assert removed[0]["old_line_number"] == 2
    assert removed[0]["new_line_number"] is None
    assert added[0]["content"] == "  int x = 2;"
    assert added[0]["new_line_number"] == 2
    assert added[0]["old_line_number"] is None

    # Unchanged context is retained so the renderer can collapse it.
    unchanged = [line for line in entry["lines"] if line["type"] == "unchanged"]
    assert unchanged and unchanged[0]["old_line_number"] == 1 and unchanged[0]["new_line_number"] == 1


def test_added_and_deleted_files_get_single_sided_line_numbers(tmp_storage):
    repo_id = "diff-add-delete"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"Removed.java": "gone\n"},
        transformed={"New.java": "fresh\n"},
    )

    by_name = {f["filename"]: f for f in get_file_diff(repo_id)["files"]}

    added = by_name["New.java"]
    assert added["status"] == "added"
    assert [line["type"] for line in added["lines"]] == ["added"]
    assert added["lines"][0]["new_line_number"] == 1 and added["lines"][0]["old_line_number"] is None

    deleted = by_name["Removed.java"]
    assert deleted["status"] == "deleted"
    assert [line["type"] for line in deleted["lines"]] == ["removed"]
    assert deleted["lines"][0]["old_line_number"] == 1 and deleted["lines"][0]["new_line_number"] is None


def test_unchanged_files_are_excluded_from_payload_but_still_counted(tmp_storage):
    """Unchanged files are the bulk of a repo and have nothing to render."""
    repo_id = "diff-unchanged"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"pom.xml": "<version>8</version>\n", "README.md": "same\n", "LICENSE": "same\n"},
        transformed={"pom.xml": "<version>17</version>\n", "README.md": "same\n", "LICENSE": "same\n"},
    )

    payload_names = {f["filename"] for f in get_file_diff(repo_id)["files"]}
    assert payload_names == {"pom.xml"}

    summary = get_diff_summary(repo_id)
    assert summary["unchanged"] == 2
    assert summary["total_files"] == 3
    assert summary["changed_files"] == 1
    assert summary["modified"] == 1
    assert summary["has_changes"] is True


def test_summary_reports_line_level_additions_and_deletions(tmp_storage):
    """The results page header reads ``additions``/``deletions``."""
    repo_id = "diff-summary-lines"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"a.txt": "one\ntwo\n"},
        transformed={"a.txt": "one\ntwo prime\nthree\n"},
    )

    summary = get_diff_summary(repo_id)
    assert summary["additions"] == 2
    assert summary["deletions"] == 1


def test_diff_payload_no_longer_ships_redundant_content_copies(tmp_storage):
    """``before``/``after``/``diff`` were three copies of the same bytes."""
    repo_id = "diff-lean"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"a.txt": "old\n"},
        transformed={"a.txt": "new\n"},
    )

    entry = get_file_diff(repo_id)["files"][0]
    assert "before" not in entry
    assert "after" not in entry
    assert "diff" not in entry


# --- Bounds ---


def test_per_file_lines_are_capped_and_flagged(tmp_storage):
    repo_id = "diff-big-file"
    original = "\n".join(f"line {i}" for i in range(MAX_LINES_PER_FILE + 500)) + "\n"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"big.txt": original},
        transformed={"big.txt": original.replace("line 0", "line zero")},
    )

    entry = get_file_diff(repo_id)["files"][0]
    assert len(entry["lines"]) == MAX_LINES_PER_FILE
    assert entry["truncated"] is True

    # Truncating the view must not understate the real change count.
    assert get_diff_summary(repo_id)["additions"] == 1


def test_total_payload_lines_are_bounded(tmp_storage):
    repo_id = "diff-many-lines"
    body = "\n".join(f"line {i}" for i in range(MAX_LINES_PER_FILE)) + "\n"
    file_count = (MAX_TOTAL_LINES // MAX_LINES_PER_FILE) + 3
    original = {f"f{i}.txt": body for i in range(file_count)}
    transformed = {name: content.replace("line 1\n", "line one\n") for name, content in original.items()}
    _make_transformation(tmp_storage, repo_id, original=original, transformed=transformed)

    result = get_file_diff(repo_id)
    total_lines = sum(len(f["lines"]) for f in result["files"])

    assert total_lines <= MAX_TOTAL_LINES
    assert result["truncated"] is True
    # Every changed file remains visible in the tab strip.
    assert len(result["files"]) == file_count


def test_changed_file_count_is_bounded(tmp_storage):
    repo_id = "diff-many-files"
    file_count = MAX_CHANGED_FILES + 5
    original = {f"f{i}.txt": "old\n" for i in range(file_count)}
    transformed = {f"f{i}.txt": "new\n" for i in range(file_count)}
    _make_transformation(tmp_storage, repo_id, original=original, transformed=transformed)

    result = get_file_diff(repo_id)
    assert len(result["files"]) == MAX_CHANGED_FILES
    assert result["omitted_files"] == 5
    assert result["truncated"] is True


# --- Endpoint plumbing ---


def test_diff_endpoints_serve_the_contract(tmp_storage):
    repo_id = "diff-endpoint"
    _register(repo_id)
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"pom.xml": "<version>8</version>\n"},
        transformed={"pom.xml": "<version>17</version>\n"},
    )

    with TestClient(app) as client:
        diff = client.get(f"/diff/{repo_id}")
        summary = client.get(f"/diff-summary/{repo_id}")

    assert diff.status_code == 200
    assert diff.json()["files"][0]["filename"] == "pom.xml"
    assert diff.json()["files"][0]["lines"]

    assert summary.status_code == 200
    assert summary.json()["has_changes"] is True


def test_diff_endpoints_404_on_unknown_repo_id(tmp_storage):
    with TestClient(app) as client:
        assert client.get("/diff/nope").status_code == 404
        assert client.get("/diff-summary/nope").status_code == 404


def test_missing_output_tree_reports_an_error_not_a_crash(tmp_storage):
    repo_id = "diff-no-output"
    _register(repo_id, status="error")

    with TestClient(app) as client:
        diff = client.get(f"/diff/{repo_id}")
        summary = client.get(f"/diff-summary/{repo_id}")

    assert diff.status_code == 200
    assert diff.json()["files"] == []
    assert "error" in diff.json()
    assert summary.json()["has_changes"] is False


# --- Categories: generated documentation vs source edits ---
#
# The reported defect: a `AWS/comprehensive-codebase-analysis` run showed 32 changed
# files, all markdown, and read as "where did my source changes go?". The diff was
# correct — that definition writes an `ATXDocumentation/` tree and edits no source —
# but the payload gave no way to tell "changed no code" from "code changes missing".


def test_documentation_only_run_reports_zero_source_changes(tmp_storage):
    """The user's case: an analysis run generates docs and edits nothing."""
    repo_id = "diff-docs-only"
    source = {
        "pom.xml": "<project/>\n",
        "src/main/java/App.java": "class App {}\n",
        "README.md": "original readme\n",
    }
    _make_transformation(
        tmp_storage,
        repo_id,
        original=source,
        transformed={
            **source,
            "ATXDocumentation/README.md": "# Analysis\n",
            "ATXDocumentation/analysis/tech-debt.md": "# Tech debt\nline two\n",
        },
    )

    summary = get_diff_summary(repo_id)

    # Stated explicitly, not inferred from an absence.
    assert summary["source_files_changed"] == 0
    assert summary["documentation_files_changed"] == 2
    assert summary["changed_by_category"]["source"] == {"files": 0, "additions": 0, "deletions": 0}
    assert summary["changed_by_category"]["documentation"]["files"] == 2
    assert summary["changed_by_category"]["documentation"]["additions"] == 3

    # Pre-existing fields keep their meaning: 2 changed files really did change.
    assert summary["changed_files"] == 2
    assert summary["added"] == 2
    assert summary["additions"] == 3
    assert summary["has_changes"] is True

    # Both categories stay in the payload — this is a grouping, not a filter.
    files = get_file_diff(repo_id)["files"]
    assert {f["filename"] for f in files} == {
        "ATXDocumentation/README.md",
        "ATXDocumentation/analysis/tech-debt.md",
    }
    assert {f["category"] for f in files} == {"documentation"}
    assert all(f["lines"] for f in files)


def test_run_with_both_source_and_documentation_reports_both(tmp_storage):
    repo_id = "diff-both"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"pom.xml": "<source>8</source>\n", "App.java": "class App {}\n"},
        transformed={
            "pom.xml": "<source>17</source>\n",
            "App.java": "class App {}\n",
            "ATXDocumentation/upgrade.md": "# Upgrade\n",
        },
    )

    summary = get_diff_summary(repo_id)
    assert summary["source_files_changed"] == 1
    assert summary["documentation_files_changed"] == 1
    assert summary["changed_by_category"]["source"] == {"files": 1, "additions": 1, "deletions": 1}
    assert summary["changed_by_category"]["documentation"] == {"files": 1, "additions": 1, "deletions": 0}
    assert summary["changed_files"] == 2

    by_name = {f["filename"]: f for f in get_file_diff(repo_id)["files"]}
    assert by_name["pom.xml"]["category"] == "source"
    assert by_name["ATXDocumentation/upgrade.md"]["category"] == "documentation"


def test_source_only_run_reports_zero_documentation(tmp_storage):
    """A java-version-upgrade run: all source, no generated docs."""
    repo_id = "diff-source-only"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"App.java": "int x = 1;\n"},
        transformed={"App.java": "int x = 2;\n"},
    )

    summary = get_diff_summary(repo_id)
    assert summary["source_files_changed"] == 1
    assert summary["documentation_files_changed"] == 0
    assert summary["changed_by_category"]["documentation"] == {"files": 0, "additions": 0, "deletions": 0}


def test_modified_existing_markdown_is_a_source_edit_not_generated_output(tmp_storage):
    """Editing a README the repo already had is a change to the repository."""
    repo_id = "diff-md-modified"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"README.md": "before\n"},
        transformed={"README.md": "after\n"},
    )

    assert get_file_diff(repo_id)["files"][0]["category"] == "source"
    assert get_diff_summary(repo_id)["source_files_changed"] == 1


def test_added_markdown_outside_the_atx_tree_counts_as_documentation(tmp_storage):
    repo_id = "diff-loose-md"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"App.java": "class App {}\n"},
        transformed={"App.java": "class App {}\n", "docs/MIGRATION.md": "# Migration\n"},
    )

    assert get_file_diff(repo_id)["files"][0]["category"] == "documentation"
    assert get_diff_summary(repo_id)["documentation_files_changed"] == 1


def test_non_markdown_inside_the_atx_tree_is_still_documentation(tmp_storage):
    repo_id = "diff-atx-json"
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"App.java": "class App {}\n"},
        transformed={"App.java": "class App {}\n", "ATXDocumentation/metrics.json": "{}\n"},
    )

    assert get_file_diff(repo_id)["files"][0]["category"] == "documentation"


def test_category_totals_are_present_even_when_the_output_tree_is_missing(tmp_storage):
    """A stable shape: the page must not have to guard every category read."""
    summary = get_diff_summary("diff-absent")

    assert summary["source_files_changed"] == 0
    assert summary["documentation_files_changed"] == 0
    assert summary["changed_by_category"] == {
        "source": {"files": 0, "additions": 0, "deletions": 0},
        "documentation": {"files": 0, "additions": 0, "deletions": 0},
    }


def test_endpoints_expose_the_category_breakdown(tmp_storage):
    repo_id = "diff-category-endpoint"
    _register(repo_id)
    _make_transformation(
        tmp_storage,
        repo_id,
        original={"App.java": "class App {}\n"},
        transformed={"App.java": "class App {}\n", "ATXDocumentation/README.md": "# Docs\n"},
    )

    with TestClient(app) as client:
        diff = client.get(f"/diff/{repo_id}").json()
        summary = client.get(f"/diff-summary/{repo_id}").json()

    assert diff["files"][0]["category"] == "documentation"
    assert summary["documentation_files_changed"] == 1
    assert summary["source_files_changed"] == 0
