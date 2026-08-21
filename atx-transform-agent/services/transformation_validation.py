"""Validation of ATX transformation identifiers, and resolution of the identifier
for a transformation definition record.

Why this exists
---------------
``atx custom def exec -n <name>`` sends ``<name>`` to the ATX control plane as the
``resource`` parameter, which is constrained by a documented pattern. A value that
violates it (a display name with spaces, most commonly) is only rejected *after* a
full CLI round trip, and surfaces as an opaque::

    ValidationException ... Value at 'resource' failed to satisfy constraint:
    Member must satisfy regular expression pattern: ...

buried in the tail of ``output.log``. The caller of ``POST /transform`` gets a 200
and a repo_id, then a failed transformation with no usable signal. Validating the
same pattern in the request model turns that into an immediate 422 that names the
offending value.

The pattern is reproduced from the service's own error message; both documented
alternatives are accepted — the short definition name and the
``arn:aws:transform-custom:...:package/<name>`` package ARN.
"""

import re

# Short definition name: an optional ``AWS/`` namespace prefix, then 1-64 chars of
# alphanumeric runs joined by single ``.``, ``_`` or ``-`` separators. The length
# lookahead deliberately sits *after* the prefix, exactly as in the service pattern,
# so ``AWS/`` does not count against the 64-char budget.
_DEFINITION_NAME = r"(?:AWS/)?(?=.{1,64}$)[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*"

# Package ARN alternative for custom definitions published to a transform package.
_PACKAGE_ARN = r"arn:aws:transform-custom:[a-z0-9-]{1,20}:\d{12}:package/" + _DEFINITION_NAME

TRANSFORMATION_TYPE_PATTERN = re.compile(f"(?:{_DEFINITION_NAME})|(?:{_PACKAGE_ARN})")

EXPECTED_FORM = (
    "expected an ATX definition identifier such as 'AWS/java-version-upgrade' "
    "(alphanumeric segments joined by '.', '_' or '-', optional 'AWS/' prefix, at most "
    "64 characters after the prefix) or a package ARN such as "
    "'arn:aws:transform-custom:<region>:<account-id>:package/<definition-name>'"
)


def is_valid_transformation_type(value: object) -> bool:
    """Return True if ``value`` satisfies the ATX ``resource`` pattern.

    ``fullmatch`` rather than ``match``: the pattern's own alternatives are not
    uniformly anchored, and a trailing newline would otherwise slip through
    (Python's ``$`` matches before a final newline).
    """
    return isinstance(value, str) and TRANSFORMATION_TYPE_PATTERN.fullmatch(value) is not None


def validate_transformation_type(value: str) -> str:
    """Return ``value`` unchanged, or raise ``ValueError`` naming it.

    Raising ``ValueError`` from a Pydantic field validator is what turns this into
    FastAPI's 422 response; the message is what the caller actually reads, so it
    carries both the rejected value and the accepted forms.
    """
    if not is_valid_transformation_type(value):
        raise ValueError(
            f"transformation_type {value!r} is not a valid ATX definition identifier — "
            f"{EXPECTED_FORM}. Pass the definition's identifier, not its display name."
        )
    return value


def resolve_definition_name(definition: dict) -> str | None:
    """Resolve the ATX CLI identifier for one transformation definition record.

    The two sources of definitions do not agree on which field holds the identifier:

    - AWS-managed entries (``data/aws_managed_transformations.json``) carry
      ``id`` = ``AWS/java-version-upgrade`` (the CLI identifier) and ``name`` =
      ``Java Version Upgrade`` (a display label).
    - Custom entries, written by the backend's transformation-definition CRUD, carry
      ``id`` = a locally generated uuid4 that ATX has never heard of, and ``name`` =
      the registered ATX definition name. A uuid4 happens to *satisfy* the resource
      pattern, so validity alone cannot distinguish the two shapes — the record's
      ``type`` is what does.

    Returns None when the record has no usable identifier (for example a custom
    definition whose name contains spaces and therefore cannot be executed at all).
    Callers surface that rather than substituting a value that would fail later.
    """
    if definition.get("type") == "custom":
        candidate = definition.get("name")
    else:
        candidate = definition.get("id") or definition.get("name")
    return candidate if is_valid_transformation_type(candidate) else None
