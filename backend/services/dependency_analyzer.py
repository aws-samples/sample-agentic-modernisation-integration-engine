"""Dependency analyzer — extracts dependencies from package manifest files."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass
class Dependency:
    """A dependency extracted from a manifest file."""

    name: str
    version: str
    ecosystem: str
    source_file: str


# Supported manifest files and their ecosystems.
_MANIFEST_MAP: dict[str, str] = {
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "package.json": "npm",
    "requirements.txt": "pip",
    "Pipfile": "pip",
    "setup.py": "pip",
    "pyproject.toml": "pip",
    "Cargo.toml": "cargo",
    "go.mod": "go",
    "composer.json": "composer",
    "Gemfile": "rubygems",
    "*.csproj": "nuget",
    "packages.config": "nuget",
}


class DependencyAnalyzer:
    """Extracts dependencies from package manifest files in a directory tree."""

    def analyze(self, root_path: str) -> list[Dependency]:
        """Walk the directory tree and extract all dependencies.

        Args:
            root_path: Root directory to scan.

        Returns:
            List of extracted dependencies.
        """
        deps: list[Dependency] = []

        for dirpath, _dirnames, filenames in os.walk(root_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(file_path, root_path)

                if filename == "pom.xml":
                    deps.extend(self._parse_pom(file_path, rel_path))
                elif filename == "package.json":
                    deps.extend(self._parse_package_json(file_path, rel_path))
                elif filename == "requirements.txt":
                    deps.extend(self._parse_requirements_txt(file_path, rel_path))
                elif filename == "build.gradle":
                    deps.extend(self._parse_gradle(file_path, rel_path))
                elif filename == "go.mod":
                    deps.extend(self._parse_go_mod(file_path, rel_path))
                elif filename == "Cargo.toml":
                    deps.extend(self._parse_cargo_toml(file_path, rel_path))
                elif filename == "composer.json":
                    deps.extend(self._parse_composer_json(file_path, rel_path))
                elif filename.endswith(".csproj"):
                    deps.extend(self._parse_csproj(file_path, rel_path))

        return deps

    def _parse_pom(self, file_path: str, rel_path: str) -> list[Dependency]:
        """Parse Maven pom.xml for dependencies."""
        deps: list[Dependency] = []
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"

            for dep_elem in root.iter(f"{ns}dependency"):
                group_id = dep_elem.findtext(f"{ns}groupId", "")
                artifact_id = dep_elem.findtext(f"{ns}artifactId", "")
                version = dep_elem.findtext(f"{ns}version", "unknown")

                if artifact_id:
                    name = f"{group_id}:{artifact_id}" if group_id else artifact_id
                    deps.append(
                        Dependency(
                            name=name,
                            version=version,
                            ecosystem="maven",
                            source_file=rel_path,
                        )
                    )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps

    def _parse_package_json(self, file_path: str, rel_path: str) -> list[Dependency]:
        """Parse npm package.json for dependencies."""
        deps: list[Dependency] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            for section in ("dependencies", "devDependencies"):
                for name, version in data.get(section, {}).items():
                    deps.append(
                        Dependency(
                            name=name,
                            version=version,
                            ecosystem="npm",
                            source_file=rel_path,
                        )
                    )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps

    def _parse_requirements_txt(
        self, file_path: str, rel_path: str
    ) -> list[Dependency]:
        """Parse Python requirements.txt."""
        deps: list[Dependency] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    # Handle ==, >=, <=, ~=, !=
                    match = re.match(r"^([A-Za-z0-9_.-]+)\s*([><=!~]+)?\s*(.*)", line)
                    if match:
                        name = match.group(1)
                        version = match.group(3) or "any"
                        deps.append(
                            Dependency(
                                name=name,
                                version=version,
                                ecosystem="pip",
                                source_file=rel_path,
                            )
                        )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps

    def _parse_gradle(self, file_path: str, rel_path: str) -> list[Dependency]:
        """Parse Gradle build.gradle for dependencies (basic regex)."""
        deps: list[Dependency] = []
        pattern = re.compile(
            r"""(?:implementation|api|compile|testImplementation)\s*['"(]"""
            r"""([^:'"]+):([^:'"]+):([^'")\s]+)"""
        )
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
            for match in pattern.finditer(content):
                group_id, artifact_id, version = (
                    match.group(1),
                    match.group(2),
                    match.group(3),
                )
                deps.append(
                    Dependency(
                        name=f"{group_id}:{artifact_id}",
                        version=version,
                        ecosystem="gradle",
                        source_file=rel_path,
                    )
                )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps

    def _parse_go_mod(self, file_path: str, rel_path: str) -> list[Dependency]:
        """Parse Go go.mod for dependencies."""
        deps: list[Dependency] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                in_require = False
                for line in f:
                    line = line.strip()
                    if line.startswith("require ("):
                        in_require = True
                        continue
                    if in_require and line == ")":
                        in_require = False
                        continue
                    if in_require:
                        parts = line.split()
                        if len(parts) >= 2:
                            deps.append(
                                Dependency(
                                    name=parts[0],
                                    version=parts[1],
                                    ecosystem="go",
                                    source_file=rel_path,
                                )
                            )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps

    def _parse_cargo_toml(self, file_path: str, rel_path: str) -> list[Dependency]:
        """Parse Rust Cargo.toml for dependencies (basic parsing)."""
        deps: list[Dependency] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                in_deps = False
                for line in f:
                    line = line.strip()
                    if line in ("[dependencies]", "[dev-dependencies]"):
                        in_deps = True
                        continue
                    if line.startswith("[") and in_deps:
                        in_deps = False
                        continue
                    if in_deps and "=" in line:
                        name, value = line.split("=", 1)
                        name = name.strip()
                        value = value.strip().strip('"').strip("'")
                        # Handle inline table: {version = "1.0"}
                        ver_match = re.search(r'version\s*=\s*"([^"]+)"', value)
                        version = ver_match.group(1) if ver_match else value
                        if name:
                            deps.append(
                                Dependency(
                                    name=name,
                                    version=version,
                                    ecosystem="cargo",
                                    source_file=rel_path,
                                )
                            )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps

    def _parse_composer_json(self, file_path: str, rel_path: str) -> list[Dependency]:
        """Parse PHP composer.json for dependencies."""
        deps: list[Dependency] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            for section in ("require", "require-dev"):
                for name, version in data.get(section, {}).items():
                    if name == "php":
                        continue
                    deps.append(
                        Dependency(
                            name=name,
                            version=version,
                            ecosystem="composer",
                            source_file=rel_path,
                        )
                    )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps

    def _parse_csproj(self, file_path: str, rel_path: str) -> list[Dependency]:
        """Parse .NET .csproj for NuGet PackageReference elements."""
        deps: list[Dependency] = []
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            for pkg_ref in root.iter("PackageReference"):
                name = pkg_ref.get("Include", "")
                version = pkg_ref.get("Version", "unknown")
                if name:
                    deps.append(
                        Dependency(
                            name=name,
                            version=version,
                            ecosystem="nuget",
                            source_file=rel_path,
                        )
                    )
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
        return deps
