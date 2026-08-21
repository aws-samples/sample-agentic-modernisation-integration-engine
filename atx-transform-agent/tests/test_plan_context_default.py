"""A transformation that requires ``additionalPlanContext`` must get one.

``AWS/java-version-upgrade`` refuses to run non-interactively without
``-g additionalPlanContext=...``; nothing supplied one — the frontend sends no
``configuration`` and the agent had no default — so every run died at startup with::

    Running this transformation in non-interactive mode requires the --configuration
    (or -g) input provided with the "additionalPlanContext" section populated.
    This is needed to specify the target language version of the transformation.

Pinned here: the default is applied in the agent (so direct ``POST /transform`` callers
get it too), an explicit ``configuration`` always wins, definitions with no established
target version get no invented one, the ARN form resolves the same, and the applied
default is recorded on the transformation and announced in its log rather than being
silent.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from main import app
from services import storage_service
from services.plan_context_defaults import (
    SOURCE_AGENT_DEFAULT,
    SOURCE_REQUEST,
    default_configuration_for,
    resolve_configuration,
)
from services.transform_service import build_atx_command, get_log_path, run_transformation

JAVA = "AWS/java-version-upgrade"
JAVA_ARN = f"arn:aws:transform-custom:us-east-1:123456789012:package/{JAVA}"
NODEJS = "AWS/nodejs-version-upgrade"
JAVA_DEFAULT = "additionalPlanContext=The target Java version to upgrade to is Java 21"

client = TestClient(app)


def _g_value(cmd: list[str]) -> str | None:
    return cmd[cmd.index("-g") + 1] if "-g" in cmd else None


# --- 1. The default reaches the command ---


def test_java_version_upgrade_gets_a_default_plan_context_when_none_is_supplied():
    cmd = build_atx_command(JAVA, Path("/tmp/repo"))

    assert "-g" in cmd, "no -g was passed, so the CLI will refuse to start"
    value = _g_value(cmd)
    assert value.startswith("additionalPlanContext=")
    assert "Java 21" in value
    assert value == JAVA_DEFAULT


def test_an_explicit_configuration_is_passed_through_unchanged():
    """Never merged, never overridden, never appended to."""
    supplied = "additionalPlanContext=The target Java version to upgrade to is Java 17"
    cmd = build_atx_command(JAVA, Path("/tmp/repo"), supplied)

    assert cmd.count("-g") == 1
    assert _g_value(cmd) == supplied
    assert "Java 21" not in " ".join(cmd)


@pytest.mark.parametrize(
    "transformation_type",
    [
        NODEJS,
        "AWS/python-version-upgrade",
        "AWS/angular-version-upgrade",
        "e2e-test-transform",
    ],
)
def test_a_definition_with_no_known_default_passes_no_g(transformation_type):
    """No target version is established for these, so none is invented.

    They keep failing with the CLI's own message, which names what is missing.
    """
    cmd = build_atx_command(transformation_type, Path("/tmp/repo"))

    assert "-g" not in cmd
    assert default_configuration_for(transformation_type) is None


def test_the_arn_form_resolves_to_the_same_default():
    """The request validator admits a package ARN, so the lookup must see through one."""
    assert default_configuration_for(JAVA_ARN) == JAVA_DEFAULT
    assert _g_value(build_atx_command(JAVA_ARN, Path("/tmp/repo"))) == JAVA_DEFAULT


@pytest.mark.parametrize(
    "lookalike",
    [
        "AWS/java-version-upgrade-preview",
        "java-version-upgrade",
        "AWS/java-version",
    ],
)
def test_the_lookup_matches_exactly_and_never_by_substring(lookalike):
    """A different definition is a different definition, prefix or not."""
    assert default_configuration_for(lookalike) is None


def test_resolution_reports_where_the_value_came_from():
    assert resolve_configuration(JAVA, None) == (JAVA_DEFAULT, SOURCE_AGENT_DEFAULT)
    assert resolve_configuration(JAVA, "additionalPlanContext=Java 17") == (
        "additionalPlanContext=Java 17",
        SOURCE_REQUEST,
    )
    assert resolve_configuration(NODEJS, None) == (None, None)


def test_resolution_is_idempotent():
    """Resolving an already-resolved value returns it unchanged, so the record, the
    command and the log notice cannot disagree about what a run is using."""
    once = resolve_configuration(JAVA, None)
    twice = resolve_configuration(JAVA, once.value)
    assert twice.value == once.value
    assert twice.source == SOURCE_REQUEST


# --- 2. The applied default is recorded ---


@pytest.fixture
def tmp_storage(tmp_path, monkeypatch):
    """Redirect storage and keep accepted requests from cloning or launching the CLI."""
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "storage_path", str(storage))
    monkeypatch.setattr(main, "_run_transform_background", lambda *args, **kwargs: None)
    return storage


def _start(transformation_type: str, **body) -> str:
    response = client.post(
        "/transform",
        json={
            "repo_url": "https://github.com/example/repo",
            "branch": "main",
            "transformation_type": transformation_type,
            **body,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["repo_id"]


def test_the_record_carries_the_default_and_names_its_source(tmp_storage):
    record = storage_service.read_record(_start(JAVA))

    assert record["configuration"] == JAVA_DEFAULT
    assert record["configuration_source"] == SOURCE_AGENT_DEFAULT


def test_the_record_carries_a_supplied_configuration_as_coming_from_the_request(tmp_storage):
    supplied = "additionalPlanContext=The target Java version to upgrade to is Java 17"
    record = storage_service.read_record(_start(JAVA, configuration=supplied))

    assert record["configuration"] == supplied
    assert record["configuration_source"] == SOURCE_REQUEST


def test_a_definition_with_no_default_records_no_configuration(tmp_storage):
    record = storage_service.read_record(_start(NODEJS))

    assert record["configuration"] is None
    assert record["configuration_source"] is None


# --- 3. The applied default is visible in the log the console shows ---


def test_the_applied_default_is_announced_in_the_transformation_log(tmp_path, monkeypatch):
    """Written through the same de-noised, timestamped path as captured CLI output, so
    replay and live views are identical."""
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "storage_path", str(storage))
    # A real subprocess that echoes its arguments and exits 0 — no stubbed internals.
    monkeypatch.setattr(settings, "atx_cli_path", "echo")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    exit_code = run_transformation("logdefault", JAVA, repo_path)

    assert exit_code == 0
    lines = get_log_path("logdefault").read_text().splitlines()

    notice = next(line for line in lines if "applied its default" in line)
    assert JAVA_DEFAULT in notice
    assert JAVA in notice
    # Same shape as every other stored line: "[<iso timestamp>] <payload>".
    assert notice.startswith("[") and "] " in notice
    # And it is the first thing in the log, before any CLI output.
    assert lines[0] == notice
    # The echoed command proves -g actually reached the CLI.
    assert f"-g {JAVA_DEFAULT}" in lines[1]


def test_no_notice_is_written_when_the_caller_supplied_the_configuration(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "storage_path", str(storage))
    monkeypatch.setattr(settings, "atx_cli_path", "echo")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_transformation("logsupplied", JAVA, repo_path, "additionalPlanContext=Java 17")

    log = get_log_path("logsupplied").read_text()
    assert "applied its default" not in log
    assert "-g additionalPlanContext=Java 17" in log
