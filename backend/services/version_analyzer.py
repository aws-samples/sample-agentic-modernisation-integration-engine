"""Version analyzer — recommends dependency upgrades.

Recommendation sources, in priority order (at most one row per dependency):

1. **Known vulnerability fixes** — when an OSV scan result is supplied, a CVE with a
   published fixed version is the strongest possible recommendation: it is grounded in
   real advisory data and names the CVE it resolves.
2. **Curated modernization rules** — a small table of well-known major migrations
   (Spring Boot 3, JUnit 5, the Jakarta namespace move).
3. **Pre-1.0 heuristic** — a declared 0.x version carries no stability guarantee.

Declared versions arrive in the messy forms real manifests use (`^4.18.2`, `~1.2.3`,
`>=2.0.0 <3.0.0`, `1.x`, `v1.2.3`, `[1.0,2.0)`, `${spring.version}`, `unknown`, or
empty). Every comparison goes through `normalize_version` first; a version that cannot
be normalized is never silently treated as "0" or "old". Where the declared version is
undeterminable, the recommendation carries an explicit
`current_version_note` explaining why, so a blank cell in the UI is never ambiguous.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class UpgradeRecommendation:
    """A recommended version upgrade for a dependency.

    Attributes:
        name: Dependency coordinate, e.g. ``org.springframework.boot:spring-boot-starter-web``.
        current_version: The version as declared in the manifest, or ``""`` when the
            manifest does not declare a usable version. When empty,
            ``current_version_note`` explains why.
        current_version_note: Human-readable reason the current version is unknown.
            Empty when ``current_version`` is populated.
        recommended_version: The version (or version family) to move to.
        ecosystem: Package ecosystem, e.g. ``maven``, ``npm``.
        reason: Actionable justification for the upgrade.
    """

    name: str
    current_version: str
    recommended_version: str
    ecosystem: str
    reason: str
    current_version_note: str = ""


@dataclass(frozen=True)
class _KnownUpgrade:
    """A curated modernization rule keyed on artifact name."""

    target_display: str
    reason: str
    # Minimum version that satisfies this rule. ``None`` means the rule is keyed on
    # identity rather than version (e.g. an artifact rename), so it always applies.
    target_floor: str | None


# Curated major-version migrations, keyed on artifact name (the part after ``:`` for
# Maven/Gradle coordinates).
_KNOWN_UPGRADES: dict[str, _KnownUpgrade] = {
    "spring-boot-starter-web": _KnownUpgrade(
        target_display="3.x",
        target_floor="3.0.0",
        reason="Spring Boot 3.x supports Jakarta EE and Java 17+",
    ),
    "junit": _KnownUpgrade(
        target_display="5.x",
        target_floor="5.0.0",
        reason="JUnit 5 provides modern testing APIs",
    ),
    "javax.servlet-api": _KnownUpgrade(
        target_display="jakarta.servlet-api 6.x",
        # An artifact still named javax.servlet-api can never satisfy the Jakarta
        # namespace move, whatever its version — so there is no version floor.
        target_floor=None,
        reason=(
            "Jakarta EE namespace migration required for modern containers "
            "(artifact must be renamed to jakarta.servlet-api)"
        ),
    ),
    "log4j-core": _KnownUpgrade(
        target_display="2.21.1",
        target_floor="2.21.0",
        reason="Addresses CVE-2021-44228 (Log4Shell) and later Log4j 2 advisories",
    ),
    "react": _KnownUpgrade(
        target_display="18.x",
        target_floor="18.0.0",
        reason="React 18 provides concurrent rendering and automatic batching",
    ),
}

# Declared-version strings that carry no version information at all. These are the
# placeholders the manifest parsers emit when a manifest omits a version.
_UNDETERMINED_TOKENS = frozenset(
    {"unknown", "any", "latest", "none", "null", "*", "x", "-", "next", "stable"}
)

# Splits a compound range (">=2.0.0 <3.0.0", "1.0 || 2.0", "[1.0,2.0)") into its parts.
_RANGE_SPLIT = re.compile(r"\s*(?:\|\||,|\s|\u2013|\u2014)\s*")

# Leading characters used by range/prefix syntax across ecosystems: npm operators,
# Maven range brackets, and the Go/tag "v" prefix.
_LEADING_OPERATORS = "^~><=! vV[]()"

# Leading dotted-numeric core, tolerating wildcard components (1.x, 1.2.*).
_VERSION_CORE = re.compile(
    r"^(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?",
)


def normalize_version(raw: object) -> str | None:
    """Normalize a declared version or range to a comparable ``major.minor.patch``.

    Handles the forms real manifests contain: npm ranges (``^4.18.2``, ``~1.2.3``,
    ``>=2.0.0``), wildcards (``1.x``), Go prefixes (``v1.2.3``), Maven ranges
    (``[1.0,2.0)``), pre-release suffixes (``1.0.0-SNAPSHOT``), Maven property
    placeholders (``${spring.version}``) and the parsers' ``unknown``/empty
    placeholders.

    Args:
        raw: The declared version string (or anything else, defensively).

    Returns:
        A normalized ``major.minor.patch`` string, or ``None`` when the input carries
        no usable version information.
    """
    if not isinstance(raw, str):
        return None

    text = raw.strip()
    if not text:
        return None

    # Unresolved build-tool property, e.g. ${spring.version} or $revision.
    if "${" in text or text.startswith("$"):
        return None

    if text.lower() in _UNDETERMINED_TOKENS:
        return None

    # A compound range constrains a window; its lower bound is the comparable value.
    first = next((part for part in _RANGE_SPLIT.split(text) if part), "")
    if not first:
        return None

    candidate = first.lstrip(_LEADING_OPERATORS)
    match = _VERSION_CORE.match(candidate)
    if not match:
        # Non-numeric specifiers: "file:../local", "git+https://...", "workspace:*".
        return None

    parts = [match.group(1)]
    for group in (match.group(2), match.group(3)):
        # A wildcard component is a floor: 1.x means "somewhere in 1.0.0+".
        parts.append("0" if group is None or group in ("x", "X", "*") else group)

    return ".".join(parts)


def version_key(normalized: str) -> tuple[int, int, int]:
    """Convert a normalized version into a comparable tuple."""
    parts = normalized.split(".")
    numbers = [int(p) for p in parts[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


def compare_versions(left: object, right: object) -> int | None:
    """Compare two declared versions component-wise.

    Returns:
        ``-1``/``0``/``1`` for less/equal/greater, or ``None`` when either side cannot
        be normalized (comparison is undefined rather than assumed).
    """
    left_norm = normalize_version(left)
    right_norm = normalize_version(right)
    if left_norm is None or right_norm is None:
        return None

    left_key = version_key(left_norm)
    right_key = version_key(right_norm)
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def describe_undeclared_version(raw: object, ecosystem: str) -> str:
    """Explain why a declared version is unusable, for display in place of a value.

    A blank cell that means "we don't know" is indistinguishable from a rendering bug,
    so every recommendation without a usable version carries one of these notes.
    """
    text = raw.strip() if isinstance(raw, str) else ""

    if "${" in text or text.startswith("$"):
        return f"not resolved (manifest property {text})"

    if ecosystem in ("maven", "gradle"):
        return "not declared (inherited from parent POM or dependency management)"

    if text and text.lower() not in _UNDETERMINED_TOKENS:
        return f"not comparable (declared as {text})"

    return "not declared in the manifest"


@dataclass
class _VulnFix:
    """A vulnerability with a published fixed version, for one dependency."""

    fixed_version: str
    advisory_ids: list[str] = field(default_factory=list)
    total_vulns: int = 0


class VersionAnalyzer:
    """Analyzes dependencies and recommends version upgrades."""

    def analyze(
        self,
        dependencies: list[dict],
        vulnerability_results: list[object] | None = None,
    ) -> list[UpgradeRecommendation]:
        """Analyze dependencies and produce upgrade recommendations.

        Args:
            dependencies: Dicts with keys ``name``, ``version``, ``ecosystem``.
            vulnerability_results: Optional OSV scan results
                (``DependencyVulnerabilityResult`` instances from
                ``EnhancedDependencyAnalyzer.scan``). When supplied, a CVE with a
                published fixed version takes priority over the curated rules.

        Returns:
            At most one recommendation per dependency. An empty list is a valid,
            honest result for an up-to-date project.
        """
        fixes = self._index_vulnerability_fixes(vulnerability_results or [])
        recommendations: list[UpgradeRecommendation] = []
        seen: set[str] = set()

        for dep in dependencies:
            name = str(dep.get("name") or "")
            if not name or name in seen:
                continue
            version = dep.get("version", "")
            ecosystem = str(dep.get("ecosystem") or "")

            rec = (
                self._recommend_from_vulnerability(
                    name, version, ecosystem, fixes.get(name)
                )
                or self._recommend_from_known_upgrades(name, version, ecosystem)
                or self._recommend_from_prerelease_heuristic(name, version, ecosystem)
            )
            if rec:
                seen.add(name)
                recommendations.append(rec)

        return recommendations

    # --- Recommendation sources ---

    def _recommend_from_vulnerability(
        self,
        name: str,
        version: object,
        ecosystem: str,
        fix: _VulnFix | None,
    ) -> UpgradeRecommendation | None:
        """Recommend the patched version published by an OSV advisory."""
        if fix is None:
            return None

        # Only recommend a fix that is actually ahead of what is declared. When the
        # declared version is undeterminable the advisory still applies to whatever is
        # resolved, so keep the row and label the current version.
        comparison = compare_versions(version, fix.fixed_version)
        if comparison is not None and comparison >= 0:
            return None

        advisories = ", ".join(fix.advisory_ids[:3])
        remaining = fix.total_vulns - min(len(fix.advisory_ids), 3)
        suffix = f" and {remaining} more" if remaining > 0 else ""
        reason = f"Fixes {advisories}{suffix}; patched in {fix.fixed_version}"

        return self._build(name, version, ecosystem, fix.fixed_version, reason)

    def _recommend_from_known_upgrades(
        self, name: str, version: object, ecosystem: str
    ) -> UpgradeRecommendation | None:
        """Apply the curated modernization rules, keyed on artifact name."""
        artifact_name = name.split(":")[-1] if ":" in name else name
        rule = _KNOWN_UPGRADES.get(artifact_name)
        if rule is None:
            return None

        if rule.target_floor is not None:
            comparison = compare_versions(version, rule.target_floor)
            # Already at or beyond the target — nothing to recommend. An
            # undeterminable version (``None``) is not evidence of being current, so
            # the rule still applies and the row says the version is undeclared.
            if comparison is not None and comparison >= 0:
                return None

        return self._build(name, version, ecosystem, rule.target_display, rule.reason)

    def _recommend_from_prerelease_heuristic(
        self, name: str, version: object, ecosystem: str
    ) -> UpgradeRecommendation | None:
        """Flag declared 0.x versions, which carry no stability guarantee."""
        normalized = normalize_version(version)
        if normalized is None or version_key(normalized)[0] != 0:
            return None

        return self._build(
            name,
            version,
            ecosystem,
            "1.0.0 or later",
            f"Declared version {normalized} is pre-1.0 and offers no API stability guarantee",
        )

    # --- Helpers ---

    def _build(
        self,
        name: str,
        version: object,
        ecosystem: str,
        recommended_version: str,
        reason: str,
    ) -> UpgradeRecommendation:
        """Build a recommendation, labelling an undeterminable current version."""
        declared = version if isinstance(version, str) else ""
        usable = normalize_version(declared) is not None

        return UpgradeRecommendation(
            name=name,
            current_version=declared.strip() if usable else "",
            current_version_note=(
                "" if usable else describe_undeclared_version(declared, ecosystem)
            ),
            recommended_version=recommended_version,
            ecosystem=ecosystem,
            reason=reason,
        )

    def _index_vulnerability_fixes(self, results: list[object]) -> dict[str, _VulnFix]:
        """Index OSV scan results by dependency name, keeping the highest fix.

        A package with several advisories needs the highest published fixed version to
        clear them all, so that is what gets recommended.
        """
        fixes: dict[str, _VulnFix] = {}

        for result in results:
            name = getattr(result, "name", "") or ""
            vulns = getattr(result, "vulnerabilities", None) or []
            if not name or not vulns:
                continue

            best: str | None = None
            advisory_ids: list[str] = []

            for vuln in vulns:
                ids = self._advisory_labels(vuln)
                for fixed in getattr(vuln, "fixed_versions", None) or []:
                    normalized = normalize_version(fixed)
                    if normalized is None:
                        continue
                    if best is None or version_key(normalized) > version_key(best):
                        best = normalized
                    if ids and ids[0] not in advisory_ids:
                        advisory_ids.append(ids[0])

            if best is not None and advisory_ids:
                fixes[name] = _VulnFix(
                    fixed_version=best,
                    advisory_ids=advisory_ids,
                    total_vulns=len(advisory_ids),
                )

        return fixes

    def _advisory_labels(self, vuln: object) -> list[str]:
        """Prefer CVE aliases over database-specific IDs for readability."""
        aliases = [
            alias
            for alias in (getattr(vuln, "aliases", None) or [])
            if isinstance(alias, str) and alias.startswith("CVE-")
        ]
        vuln_id = getattr(vuln, "id", "") or ""
        if aliases:
            return aliases
        return [vuln_id] if vuln_id else []
