"""Transformation identifier handling.

Two defects are pinned here:

- ``POST /transform`` accepted a display name ("Java Version Upgrade") and only failed
  minutes later inside the ATX CLI with an opaque ``ValidationException`` on the
  ``resource`` parameter. It must now fail fast with a 422 that names the value.
- ``GET /transformations`` appended each custom definition *file* as one entry, so the
  backend's ``definitions.json`` (a JSON list) arrived as a nested list, and custom
  entries carried no usable identifier. Entries must be flat, and each must state the
  identifier the CLI accepts.
"""

import json

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from main import app
from services.transformation_validation import (
    is_valid_transformation_type,
    resolve_definition_name,
)

client = TestClient(app)

AWS_MANAGED_ID = "AWS/java-version-upgrade"
AWS_MANAGED_LABEL = "Java Version Upgrade"
PACKAGE_ARN = f"arn:aws:transform-custom:us-east-1:123456789012:package/{AWS_MANAGED_ID}"


@pytest.fixture(autouse=True)
def _no_real_transformation(monkeypatch, tmp_path):
    """Keep accepted requests from cloning a repository or launching the ATX CLI.

    Validation runs before the handler body, so stubbing the background task does not
    stub anything under test here. Storage is redirected to a temp directory because an
    accepted request now persists its record before returning.
    """
    monkeypatch.setattr(main, "_run_transform_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "storage"))


def _post(transformation_type: str):
    return client.post(
        "/transform",
        json={
            "repo_url": "https://github.com/example/repo",
            "branch": "main",
            "transformation_type": transformation_type,
        },
    )


# --- POST /transform fail-fast guard ---


def test_display_name_is_rejected_with_422_naming_the_value():
    """A display name with spaces is rejected before the CLI runs."""
    response = _post(AWS_MANAGED_LABEL)

    assert response.status_code == 422
    detail = json.dumps(response.json())
    # The caller must be able to see which value was rejected and what is expected.
    assert AWS_MANAGED_LABEL in detail
    assert "AWS/java-version-upgrade" in detail


def test_definition_identifier_is_accepted():
    """The AWS-managed identifier passes the guard."""
    response = _post(AWS_MANAGED_ID)

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_package_arn_form_is_accepted():
    """The ``arn:aws:transform-custom:...:package/<name>`` alternative is legal too."""
    response = _post(PACKAGE_ARN)

    assert response.status_code == 200


def test_custom_definition_name_is_accepted():
    """A custom definition's registered ATX name passes the guard."""
    response = _post("e2e-test-transform")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "value",
    [
        "Java Version Upgrade",  # spaces
        "",  # empty
        "AWS/java version upgrade",  # spaces behind a valid prefix
        "java--version",  # doubled separator
        "-java-version",  # leading separator
        "AWS/" + "a" * 65,  # over the 64-char budget
        "AWS/java-version-upgrade\n",  # trailing newline
    ],
)
def test_invalid_identifiers_are_rejected(value):
    assert is_valid_transformation_type(value) is False
    assert _post(value).status_code == 422


@pytest.mark.parametrize(
    "value",
    [
        AWS_MANAGED_ID,
        "AWS/vue.js-version-upgrade",
        "e2e-test-transform",
        "my_custom.def-1",
        PACKAGE_ARN,
        "arn:aws:transform-custom:us-west-2:123456789012:package/e2e-test-transform",
    ],
)
def test_valid_identifiers_are_accepted(value):
    assert is_valid_transformation_type(value) is True
    assert _post(value).status_code == 200


# --- GET /transformations identifier resolution ---


def test_aws_managed_definitions_expose_their_identifier_not_their_label():
    response = client.get("/transformations")
    assert response.status_code == 200
    definitions = response.json()["definitions"]

    java = next(d for d in definitions if d["id"] == AWS_MANAGED_ID)
    assert java["name"] == AWS_MANAGED_LABEL  # label still available for display
    assert java["atx_definition_name"] == AWS_MANAGED_ID
    assert is_valid_transformation_type(java["atx_definition_name"])


def test_custom_definitions_are_flattened_and_resolve_to_their_name(tmp_path, monkeypatch):
    """The backend writes a JSON *list*; entries must arrive flat, keyed on ``name``.

    A custom record's ``id`` is a locally generated uuid4 that ATX has never heard of,
    so the identifier is its registered ``name``.
    """
    (tmp_path / "definitions.json").write_text(
        json.dumps(
            [
                {
                    "id": "93ae1efc-b409-4500-b007-074e79381ba8",
                    "name": "e2e-test-transform",
                    "description": "E2E test transformation definition",
                    "type": "custom",
                    "definition_path": "",
                    "published": False,
                },
                {
                    "id": "97c84c3b-1678-4b24-a035-c57eb508cd90",
                    "name": "not a valid atx name",
                    "description": "unexecutable",
                    "type": "custom",
                    "definition_path": "",
                    "published": False,
                },
            ]
        )
    )
    monkeypatch.setattr(settings, "transformations_path", str(tmp_path))

    definitions = client.get("/transformations").json()["definitions"]

    assert all(isinstance(d, dict) for d in definitions), "no entry may be a nested list"

    good = next(d for d in definitions if d["name"] == "e2e-test-transform")
    assert good["atx_definition_name"] == "e2e-test-transform"
    assert _post(good["atx_definition_name"]).status_code == 200

    # A custom name that cannot satisfy the constraint gets no identifier rather than a
    # uuid that would fail later inside the CLI.
    bad = next(d for d in definitions if d["name"] == "not a valid atx name")
    assert bad["atx_definition_name"] is None


def test_resolve_definition_name_never_returns_a_custom_uuid():
    custom = {"id": "93ae1efc-b409-4500-b007-074e79381ba8", "name": "e2e-test", "type": "custom"}
    assert resolve_definition_name(custom) == "e2e-test"
