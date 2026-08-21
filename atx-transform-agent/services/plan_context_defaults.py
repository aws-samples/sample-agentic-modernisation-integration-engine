"""Default ``additionalPlanContext`` for transformation definitions that require one.

Why this exists
---------------
Some ATX transformation definitions cannot run non-interactively without a target
version. ``AWS/java-version-upgrade`` is one: with no ``-g`` it exits immediately with::

    Running this transformation in non-interactive mode requires the --configuration
    (or -g) input provided with the "additionalPlanContext" section populated.
    This is needed to specify the target language version of the transformation.

The ``-g`` plumbing already existed end to end (``TransformRequest.configuration`` →
background task → :func:`services.transform_service.build_atx_command`), but nothing
ever supplied a value: the frontend does not send one and the agent had no default, so
``-g`` was simply never passed and every Java run failed at startup.

The default lives here, in the agent, rather than in the frontend, because a
frontend-only default would leave every direct ``POST /transform`` caller broken —
including the ``curl`` invocations in the project's own acceptance-test doc. One place
covers both.

STOPGAP — this table is not the design
--------------------------------------
Hardcoding "which definitions need a plan context, and what to default it to" in the
agent is a stopgap, and the next reader should not mistake it for the intended
architecture. The durable fix has two halves:

1. **The catalog should declare the requirement.**
   ``data/aws_managed_transformations.json`` has no field saying a definition requires
   ``additionalPlanContext``, so nothing downstream — this agent, the backend, or the
   UI — can tell which definitions need one. Adding such a field (and the accepted
   values) makes the requirement data rather than code.
2. **The UI should require the value.**
   The target version is the most consequential parameter of the transformation. It
   belongs in a prefilled, editable, required input on the transform page, so the user
   sees and confirms it instead of inheriting a value chosen here.

Deliberately incomplete
-----------------------
Only ``AWS/java-version-upgrade`` has an entry. ``AWS/nodejs-version-upgrade``,
``AWS/python-version-upgrade`` and the rest have **no** established target version, and
inventing one would ship a plausible wrong answer — a Node 22 default is not more
correct than no default, it is just harder to notice. Those definitions keep failing
with the CLI's own message until a target version is actually chosen for each, which is
the honest state. Adding one later is a single entry in
:data:`DEFAULT_PLAN_CONTEXT_BY_DEFINITION`.
"""

import re
from typing import NamedTuple

from services.transformation_validation import is_valid_transformation_type

#: The caller supplied ``configuration`` and it was used verbatim.
SOURCE_REQUEST = "request"

#: No ``configuration`` was supplied and this module's default was applied.
SOURCE_AGENT_DEFAULT = "agent-default"

#: Default ``-g`` value per **resolved definition identifier** — the same value that
#: reaches ``atx custom def exec -n`` and that a record's ``transformation_type``
#: carries. Phrased the way the CLI's own help phrases it.
#:
#: Matched **exactly**, never by substring: ``AWS/java-version-upgrade`` and a
#: hypothetical ``AWS/java-version-upgrade-preview`` are different definitions and a
#: prefix match would silently apply one's target version to the other.
DEFAULT_PLAN_CONTEXT_BY_DEFINITION: dict[str, str] = {
    "AWS/java-version-upgrade": "additionalPlanContext=The target Java version to upgrade to is Java 21",
}

# ``transformation_type`` may arrive as a package ARN — the request validator admits
# ``arn:aws:transform-custom:<region>:<account>:package/<definition-name>`` alongside
# the short name, so the lookup has to see through it or it would silently miss.
_PACKAGE_ARN_PREFIX = re.compile(r"^arn:aws:transform-custom:[a-z0-9-]{1,20}:\d{12}:package/")


class ResolvedConfiguration(NamedTuple):
    """The ``-g`` value a transformation will actually run with, and where it came from.

    ``source`` names the origin rather than being a bare boolean so a third origin (a
    catalog-declared default, a per-user preference) can be added without changing the
    field's meaning. It is ``None`` only when ``value`` is ``None`` — no configuration
    was supplied and none is known.
    """

    value: str | None
    source: str | None


def definition_name(transformation_type: str) -> str | None:
    """Resolve ``transformation_type`` to a bare ATX definition identifier.

    Returns the short name for either accepted form (short name or package ARN), or
    ``None`` for anything the request validator would have rejected.
    """
    if not is_valid_transformation_type(transformation_type):
        return None
    return _PACKAGE_ARN_PREFIX.sub("", transformation_type, count=1)


def default_configuration_for(transformation_type: str) -> str | None:
    """The default ``-g`` value for ``transformation_type``, or ``None`` if unknown."""
    name = definition_name(transformation_type)
    if name is None:
        return None
    return DEFAULT_PLAN_CONTEXT_BY_DEFINITION.get(name)


def resolve_configuration(transformation_type: str, configuration: str | None) -> ResolvedConfiguration:
    """Decide the effective ``-g`` value for a transformation.

    An explicitly supplied ``configuration`` always wins and is returned verbatim: never
    merged with the default, never overridden, never appended to. A default is applied
    only when the caller supplied nothing at all.

    Pure and idempotent — resolving an already-resolved value returns that same value
    (with source ``request``, since by then it *is* the supplied configuration), so the
    request handler, the command builder and the log notice cannot disagree about what a
    run is using.
    """
    if configuration:
        return ResolvedConfiguration(configuration, SOURCE_REQUEST)

    default = default_configuration_for(transformation_type)
    if default is None:
        # No default is known for this definition. The CLI's own error message is a
        # better answer than a guessed target version.
        return ResolvedConfiguration(None, None)
    return ResolvedConfiguration(default, SOURCE_AGENT_DEFAULT)


def default_applied_notice(transformation_type: str, configuration: str) -> str:
    """The log line stating that a default was applied, and what it was.

    The target version is the most consequential parameter of the transformation, so an
    invisible default is the failure mode to avoid: the run would appear to have been
    asked for something it was never asked for. This line goes into the transformation's
    own ``output.log``, which is what the transform console is already showing.
    """
    return (
        f"No configuration was supplied for {transformation_type}; "
        f"the agent applied its default target version: -g {configuration}"
    )
