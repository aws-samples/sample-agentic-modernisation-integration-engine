"""Tests for analysis pipeline services."""

import os
import tempfile

import pytest

from services.dependency_analyzer import Dependency, DependencyAnalyzer
from services.enhanced_dependency_analyzer import (
    DependencyVulnerabilityResult,
    EnhancedDependencyAnalyzer,
    Vulnerability,
)
from services.file_analyzer import FileAnalyzer, FolderNode
from services.prompt_loader import load_prompt, list_prompts
from services.version_analyzer import (
    UpgradeRecommendation,
    VersionAnalyzer,
    compare_versions,
    normalize_version,
)


# --- FileAnalyzer ---


def test_file_analyzer_produces_stats_and_tree():
    """FileAnalyzer returns file stats and folder tree."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test files.
        os.makedirs(os.path.join(tmpdir, "src"))
        with open(os.path.join(tmpdir, "src", "main.py"), "w") as f:
            f.write("print('hello')\nprint('world')\n")
        with open(os.path.join(tmpdir, "src", "util.py"), "w") as f:
            f.write("x = 1\n")
        with open(os.path.join(tmpdir, "README.md"), "w") as f:
            f.write("# Hello\n")

        analyzer = FileAnalyzer()
        stats, tree = analyzer.analyze(tmpdir)

        assert isinstance(stats, list)
        assert len(stats) >= 2  # .py and .md
        assert isinstance(tree, FolderNode)
        assert tree.type == "directory"

        # Check that .py appears in stats.
        py_stat = next((s for s in stats if s.extension == ".py"), None)
        assert py_stat is not None
        assert py_stat.count == 2
        assert py_stat.total_lines == 3


def test_file_analyzer_skips_hidden_dirs():
    """FileAnalyzer skips .git and node_modules."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a .git dir with a file.
        git_dir = os.path.join(tmpdir, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write("ignored\n")

        # Create a regular file.
        with open(os.path.join(tmpdir, "app.js"), "w") as f:
            f.write("console.log('hi');\n")

        analyzer = FileAnalyzer()
        stats, tree = analyzer.analyze(tmpdir)

        # Only .js should appear (not the .git config file).
        assert len(stats) == 1
        assert stats[0].extension == ".js"


# --- DependencyAnalyzer ---


def test_dependency_analyzer_parses_requirements_txt():
    """DependencyAnalyzer extracts from requirements.txt."""
    with tempfile.TemporaryDirectory() as tmpdir:
        req_file = os.path.join(tmpdir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("fastapi==0.115.5\nuvicorn>=0.32.0\n# comment\n")

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze(tmpdir)

        assert len(deps) == 2
        assert all(isinstance(d, Dependency) for d in deps)
        fastapi_dep = next(d for d in deps if d.name == "fastapi")
        assert fastapi_dep.version == "0.115.5"
        assert fastapi_dep.ecosystem == "pip"


def test_dependency_analyzer_parses_package_json():
    """DependencyAnalyzer extracts from package.json."""
    import json

    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_file = os.path.join(tmpdir, "package.json")
        with open(pkg_file, "w") as f:
            json.dump(
                {
                    "dependencies": {"react": "^18.2.0"},
                    "devDependencies": {"vitest": "^1.0.0"},
                },
                f,
            )

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze(tmpdir)

        assert len(deps) == 2
        react_dep = next(d for d in deps if d.name == "react")
        assert react_dep.ecosystem == "npm"


# --- VersionAnalyzer ---


def test_version_analyzer_recommends_known_upgrades():
    """VersionAnalyzer recommends upgrades for known packages."""
    analyzer = VersionAnalyzer()
    deps = [
        {
            "name": "org.springframework.boot:spring-boot-starter-web",
            "version": "2.7.0",
            "ecosystem": "maven",
        },
        {"name": "react", "version": "16.14.0", "ecosystem": "npm"},
    ]
    recs = analyzer.analyze(deps)

    assert len(recs) >= 1
    assert all(isinstance(r, UpgradeRecommendation) for r in recs)
    names = [r.name for r in recs]
    assert any("spring-boot" in n for n in names)


def test_version_analyzer_no_recs_for_current():
    """VersionAnalyzer doesn't recommend upgrades for current versions."""
    analyzer = VersionAnalyzer()
    deps = [
        {"name": "some-lib", "version": "5.2.1", "ecosystem": "npm"},
    ]
    recs = analyzer.analyze(deps)
    assert recs == []


# --- Version range normalization ---


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        # npm range operators — the previous `^(\d+)\.(\d+)` match never fired on these,
        # so npm versions were silently exempt from every version heuristic.
        ("^4.18.2", "4.18.2"),
        ("~1.2.3", "1.2.3"),
        (">=2.0.0", "2.0.0"),
        (">=2.0.0 <3.0.0", "2.0.0"),
        ("1.0.0 || 2.0.0", "1.0.0"),
        # Wildcards resolve to their floor.
        ("1.x", "1.0.0"),
        ("1.2.x", "1.2.0"),
        ("18.*", "18.0.0"),
        # Other ecosystems' forms.
        ("v1.2.3", "1.2.3"),
        ("[1.0,2.0)", "1.0.0"),
        ("1.0.0-SNAPSHOT", "1.0.0"),
        ("2.21+", "2.21.0"),
        ("5.88.2", "5.88.2"),
        # No usable version information.
        ("${spring.version}", None),
        ("${project.version}", None),
        ("", None),
        ("   ", None),
        ("unknown", None),
        ("any", None),
        ("latest", None),
        ("*", None),
        ("workspace:*", None),
        ("file:../local-lib", None),
        (None, None),
    ],
)
def test_normalize_version(declared, expected):
    """Declared ranges normalize to a comparable version, or to None when unusable."""
    assert normalize_version(declared) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        # The case first-integer comparison gets wrong: both start with 0, so
        # `re.search(r"(\d+)")` compared 0 against 0 and called them equal.
        ("^0.21.4", "0.22.0", -1),
        ("^0.21.4", "0.21.4", 0),
        ("^0.21.4", "0.2.0", 1),
        # The Log4Shell false negative: 2 >= 2 by first integer, so a vulnerable
        # 2.14.1 was reported as already meeting a 2.21+ target.
        ("2.14.1", "2.21.0", -1),
        ("2.21.1", "2.21.0", 1),
        ("1.8.3", "1.8.3", 0),
        # Undeterminable input makes the comparison undefined, not "older".
        ("unknown", "3.0.0", None),
        ("${spring.version}", "3.0.0", None),
        ("", "3.0.0", None),
    ],
)
def test_compare_versions(left, right, expected):
    """Comparison is component-wise, and undefined when either side is unusable."""
    assert compare_versions(left, right) == expected


def test_version_analyzer_flags_vulnerable_minor_version():
    """A 2.14.1 log4j-core is recommended for upgrade despite sharing major 2."""
    analyzer = VersionAnalyzer()
    recs = analyzer.analyze(
        [
            {
                "name": "org.apache.logging.log4j:log4j-core",
                "version": "2.14.1",
                "ecosystem": "maven",
            }
        ]
    )

    assert len(recs) == 1
    assert "Log4Shell" in recs[0].reason
    assert recs[0].current_version == "2.14.1"


def test_version_analyzer_normalizes_npm_range_for_heuristic():
    """A pre-1.0 npm range is flagged; the leading ^ no longer defeats the check."""
    analyzer = VersionAnalyzer()
    recs = analyzer.analyze(
        [{"name": "tiny-lib", "version": "^0.21.4", "ecosystem": "npm"}]
    )

    assert len(recs) == 1
    assert recs[0].current_version == "^0.21.4"
    assert recs[0].recommended_version == "1.0.0 or later"
    assert recs[0].current_version_note == ""


# --- Undeterminable current versions ---


def test_version_analyzer_labels_parent_pom_managed_version():
    """A Maven dependency with no declared version is labelled, never left blank."""
    analyzer = VersionAnalyzer()
    recs = analyzer.analyze(
        [
            {
                "name": "org.springframework.boot:spring-boot-starter-web",
                "version": "unknown",
                "ecosystem": "maven",
            }
        ]
    )

    assert len(recs) == 1
    assert recs[0].current_version == ""
    assert "parent POM" in recs[0].current_version_note
    assert recs[0].ecosystem == "maven"


def test_version_analyzer_labels_unresolved_property_placeholder():
    """A ${property} version is reported as unresolved, quoting the placeholder."""
    analyzer = VersionAnalyzer()
    recs = analyzer.analyze(
        [
            {
                "name": "org.springframework.boot:spring-boot-starter-web",
                "version": "${spring.version}",
                "ecosystem": "maven",
            }
        ]
    )

    assert len(recs) == 1
    assert recs[0].current_version == ""
    assert "${spring.version}" in recs[0].current_version_note


def test_version_analyzer_skips_heuristic_without_a_usable_version():
    """The outdated heuristic needs a version to judge, so it stays silent without one."""
    analyzer = VersionAnalyzer()
    recs = analyzer.analyze(
        [
            {"name": "com.h2database:h2", "version": "unknown", "ecosystem": "maven"},
            {"name": "mystery-lib", "version": "", "ecosystem": "npm"},
        ]
    )

    assert recs == []


def test_version_analyzer_every_row_is_legible():
    """Each emitted row carries a name, a recommendation, an ecosystem and a reason."""
    analyzer = VersionAnalyzer()
    recs = analyzer.analyze(
        [
            {"name": "react", "version": "16.14.0", "ecosystem": "npm"},
            {
                "name": "javax.servlet:javax.servlet-api",
                "version": "4.0.1",
                "ecosystem": "maven",
            },
            {"name": "tiny-lib", "version": "~0.4.0", "ecosystem": "npm"},
        ]
    )

    assert len(recs) == 3
    for rec in recs:
        assert rec.name
        assert rec.recommended_version
        assert rec.ecosystem
        assert rec.reason
        # Either a version or an explanation of its absence — never both empty.
        assert rec.current_version or rec.current_version_note


# --- OSV-sourced recommendations ---


def test_osv_fixed_versions_extracted_from_affected_ranges():
    """Fixed versions come from affected[].ranges[].events[].fixed."""
    vuln_data = {
        "id": "GHSA-4vvj-4cpr-p986",
        "aliases": ["CVE-2024-43788"],
        "affected": [
            {
                "package": {"name": "webpack", "ecosystem": "npm"},
                "ranges": [
                    {
                        "type": "SEMVER",
                        "events": [
                            {"introduced": "5.0.0-alpha.0"},
                            {"fixed": "5.94.0"},
                        ],
                    }
                ],
            },
            # A repackaged mirror; its versions are not comparable to webpack's.
            {
                "package": {"name": "org.webjars.npm:webpack", "ecosystem": "Maven"},
                "ranges": [{"type": "ECOSYSTEM", "events": [{"fixed": "99.0.0"}]}],
            },
        ],
    }

    fixed = EnhancedDependencyAnalyzer._extract_fixed_versions(vuln_data, "webpack")
    assert fixed == ["5.94.0"]


def test_osv_advisory_without_a_fix_yields_nothing():
    """An advisory with only last_affected has no released fix to recommend."""
    vuln_data = {
        "id": "GHSA-4w4v-5hc9-xrr2",
        "affected": [
            {
                "package": {"name": "angular", "ecosystem": "npm"},
                "ranges": [
                    {
                        "type": "SEMVER",
                        "events": [{"introduced": "1.3.0"}, {"last_affected": "1.8.3"}],
                    }
                ],
            }
        ],
    }

    assert (
        EnhancedDependencyAnalyzer._extract_fixed_versions(vuln_data, "angular") == []
    )


def test_version_analyzer_recommends_osv_fixed_version_with_cve():
    """A CVE with a published fix outranks the curated rules and names the CVE."""
    analyzer = VersionAnalyzer()
    deps = [{"name": "webpack", "version": "5.88.2", "ecosystem": "npm"}]
    scan = [
        DependencyVulnerabilityResult(
            name="webpack",
            version="5.88.2",
            ecosystem="npm",
            vulnerabilities=[
                Vulnerability(
                    id="GHSA-4vvj-4cpr-p986",
                    aliases=["CVE-2024-43788"],
                    fixed_versions=["5.94.0"],
                ),
                Vulnerability(
                    id="GHSA-8fgc-7cc6-rx7x",
                    aliases=["CVE-2025-68458"],
                    fixed_versions=["5.104.1"],
                ),
            ],
        )
    ]

    recs = analyzer.analyze(deps, scan)

    assert len(recs) == 1
    # The highest fix clears every advisory.
    assert recs[0].recommended_version == "5.104.1"
    assert "CVE-2024-43788" in recs[0].reason
    assert "CVE-2025-68458" in recs[0].reason


def test_version_analyzer_ignores_fix_already_applied():
    """A dependency already past the published fix gets no vulnerability row."""
    analyzer = VersionAnalyzer()
    deps = [{"name": "webpack", "version": "5.104.1", "ecosystem": "npm"}]
    scan = [
        DependencyVulnerabilityResult(
            name="webpack",
            version="5.104.1",
            ecosystem="npm",
            vulnerabilities=[
                Vulnerability(
                    id="GHSA-4vvj-4cpr-p986",
                    aliases=["CVE-2024-43788"],
                    fixed_versions=["5.94.0"],
                )
            ],
        )
    ]

    assert analyzer.analyze(deps, scan) == []


# --- PromptLoader ---


def test_prompt_loader_loads_existing_prompt():
    """load_prompt loads from the prompts directory."""
    content = load_prompt("analysis-summary")
    assert "summary" in content.lower() or "analyst" in content.lower()


def test_prompt_loader_substitutes_variables():
    """load_prompt substitutes template variables."""
    content = load_prompt("analysis-summary", {"source_url": "https://github.com/test"})
    assert "https://github.com/test" in content


def test_prompt_loader_falls_back_for_missing():
    """load_prompt returns a default for missing prompt files."""
    content = load_prompt("nonexistent-prompt")
    assert content  # Should return something, not empty.


def test_list_prompts_finds_files():
    """list_prompts returns available prompt files."""
    prompts = list_prompts()
    assert isinstance(prompts, list)
    # We have at least analysis-summary.md and documentation-generation.md.
    assert len(prompts) >= 2
    names = [p["name"] for p in prompts]
    assert "analysis-summary" in names
    assert "documentation-generation" in names
