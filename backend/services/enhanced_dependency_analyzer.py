"""Enhanced dependency analyzer — queries OSV API for vulnerabilities.

Supports 8 ecosystems: npm, pip (PyPI), maven, gradle (Maven), nuget, composer
(Packagist), cargo (crates.io), go.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

_OSV_API_URL = "https://api.osv.dev/v1/query"

# Map internal ecosystem names to OSV ecosystem identifiers.
_ECOSYSTEM_MAP: dict[str, str] = {
    "npm": "npm",
    "pip": "PyPI",
    "maven": "Maven",
    "gradle": "Maven",
    "nuget": "NuGet",
    "composer": "Packagist",
    "cargo": "crates.io",
    "go": "Go",
}


@dataclass
class Vulnerability:
    """A vulnerability found for a dependency.

    Attributes:
        fixed_versions: Versions in which the advisory is patched, as published by OSV.
            Empty when no fix has been released (OSV marks those ranges with
            ``last_affected`` instead of ``fixed``) — an unfixed advisory cannot yield
            an upgrade recommendation.
    """

    id: str
    summary: str = ""
    severity: str = "unknown"
    aliases: list[str] = field(default_factory=list)
    fixed_versions: list[str] = field(default_factory=list)


@dataclass
class DependencyVulnerabilityResult:
    """Vulnerability scan result for a single dependency."""

    name: str
    version: str
    ecosystem: str
    vulnerabilities: list[Vulnerability] = field(default_factory=list)


class EnhancedDependencyAnalyzer:
    """Queries OSV API for known vulnerabilities in dependencies."""

    def __init__(self, timeout: int = 10) -> None:
        self._timeout = timeout

    def scan(
        self,
        dependencies: list[dict],
    ) -> list[DependencyVulnerabilityResult]:
        """Scan a list of dependencies for vulnerabilities.

        Args:
            dependencies: List of dicts with keys: name, version, ecosystem.

        Returns:
            List of results with vulnerability details.
        """
        results: list[DependencyVulnerabilityResult] = []

        for dep in dependencies:
            name = dep.get("name", "")
            version = dep.get("version", "")
            ecosystem = dep.get("ecosystem", "")

            if not name or version in ("any", "unknown", ""):
                results.append(
                    DependencyVulnerabilityResult(
                        name=name, version=version, ecosystem=ecosystem
                    )
                )
                continue

            vulns = self._query_osv(name, version, ecosystem)
            results.append(
                DependencyVulnerabilityResult(
                    name=name,
                    version=version,
                    ecosystem=ecosystem,
                    vulnerabilities=vulns,
                )
            )

        return results

    def _query_osv(
        self, name: str, version: str, ecosystem: str
    ) -> list[Vulnerability]:
        """Query OSV API for vulnerabilities of a specific package."""
        osv_ecosystem = _ECOSYSTEM_MAP.get(ecosystem)
        if not osv_ecosystem:
            return []

        payload = {
            "package": {
                "name": name,
                "ecosystem": osv_ecosystem,
            },
            "version": version,
        }

        try:
            response = requests.post(
                _OSV_API_URL,
                json=payload,
                timeout=self._timeout,
            )
            if response.status_code != 200:
                logger.warning(
                    "OSV API returned %d for %s@%s",
                    response.status_code,
                    name,
                    version,
                )
                return []

            data = response.json()
            vulns: list[Vulnerability] = []

            for vuln_data in data.get("vulns", []):
                severity = "unknown"
                if vuln_data.get("database_specific", {}).get("severity"):
                    severity = vuln_data["database_specific"]["severity"]
                elif vuln_data.get("severity"):
                    for sev in vuln_data["severity"]:
                        if sev.get("type") == "CVSS_V3":
                            score = float(sev.get("score", "0"))
                            if score >= 9.0:
                                severity = "CRITICAL"
                            elif score >= 7.0:
                                severity = "HIGH"
                            elif score >= 4.0:
                                severity = "MEDIUM"
                            else:
                                severity = "LOW"
                            break

                vulns.append(
                    Vulnerability(
                        id=vuln_data.get("id", ""),
                        summary=vuln_data.get("summary", ""),
                        severity=severity,
                        aliases=vuln_data.get("aliases", []),
                        fixed_versions=self._extract_fixed_versions(vuln_data, name),
                    )
                )

            return vulns

        except requests.RequestException as exc:
            logger.warning("OSV API request failed for %s@%s: %s", name, version, exc)
            return []

    @staticmethod
    def _extract_fixed_versions(vuln_data: dict, package_name: str) -> list[str]:
        """Pull patched versions out of an OSV advisory's affected ranges.

        OSV expresses fixes as ``affected[].ranges[].events[].fixed``. Advisories with
        no released fix use ``last_affected`` instead, and yield nothing here.

        Only ranges for the package that was queried are considered — advisories often
        also list repackaged mirrors (e.g. ``org.webjars.npm:angular``) whose version
        numbers are not comparable to the queried package's.
        """
        fixed: list[str] = []

        for affected in vuln_data.get("affected", []):
            affected_name = affected.get("package", {}).get("name", "")
            if affected_name and affected_name != package_name:
                continue
            for rng in affected.get("ranges", []):
                for event in rng.get("events", []):
                    value = event.get("fixed")
                    if value and value not in fixed:
                        fixed.append(value)

        return fixed
