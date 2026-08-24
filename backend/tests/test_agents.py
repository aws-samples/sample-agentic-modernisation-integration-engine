"""Tests for AI agents (doc_analysis, llm_judge, kiro_specs) and MCP server."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.doc_analysis_agent import DocAnalysisAgent
from agents.kiro_specs_agent import KiroSpecsAgent
from agents.llm_judge import LLMJudge, SCORING_DIMENSIONS
from agents.prompt_loader import load_prompt, _get_default_prompt
from mcp.static_analysis_server import StaticAnalysisServer
from utils.storage_manager import StorageManager


# --- Fixtures ---


@pytest.fixture
def mock_storage(tmp_path):
    """StorageManager backed by a temp directory."""
    return StorageManager(base_path=str(tmp_path))


@pytest.fixture
def sample_analysis_data():
    """Complete sample analysis data."""
    return {
        "analysis_id": "github_20250101_120000",
        "source_type": "github",
        "source_url": "https://github.com/example/repo",
        "completed_at": "2025-01-01T12:01:00Z",
        "file_stats": [
            {"extension": ".py", "count": 10, "total_lines": 500, "total_size": 15000},
            {"extension": ".java", "count": 5, "total_lines": 300, "total_size": 9000},
        ],
        "folder_structure": {
            "name": "root",
            "type": "directory",
            "children": [
                {"name": "src", "type": "directory", "children": []},
                {"name": "README.md", "type": "file", "size": 1024},
            ],
        },
        "dependencies": [
            {
                "name": "fastapi",
                "version": "0.115.5",
                "ecosystem": "pypi",
                "source_file": "pyproject.toml",
            },
            {
                "name": "boto3",
                "version": "1.35.74",
                "ecosystem": "pypi",
                "source_file": "pyproject.toml",
            },
        ],
        "dependency_graph": {
            "nodes": [
                {
                    "id": "fastapi",
                    "label": "fastapi",
                    "type": "package",
                    "metadata": {},
                },
                {"id": "boto3", "label": "boto3", "type": "package", "metadata": {}},
            ],
            "links": [
                {"source": "fastapi", "target": "boto3", "type": "depends_on"},
            ],
        },
        "upgrade_recommendations": [
            {
                "package": "boto3",
                "current_version": "1.35.74",
                "recommended_version": "1.36.0",
                "reason": "Security patch",
            },
        ],
        "diagrams": {
            "class_diagram": "classDiagram\n  class App",
            "sequence_diagram": "sequenceDiagram\n  User->>App: request",
            "integration_diagram": "graph TD\n  A-->B",
        },
        "parsed_files": [
            {
                "filename": "main.py",
                "classes": [
                    {
                        "name": "App",
                        "line_number": 1,
                        "methods": ["run"],
                        "parent_classes": [],
                    }
                ],
                "methods": [
                    {
                        "name": "main",
                        "line_number": 10,
                        "parameters": [],
                        "return_type": None,
                        "class_name": None,
                    }
                ],
                "imports": ["fastapi", "uvicorn"],
                "complexity": 3,
                "language": "python",
                "line_count": 50,
            }
        ],
    }


@pytest.fixture
def stored_analysis(mock_storage, sample_analysis_data):
    """Storage with a sample analysis saved."""
    mock_storage.save("github_20250101_120000", sample_analysis_data)
    return mock_storage


# --- Prompt Loader Tests ---


class TestPromptLoader:
    def test_load_default_prompt(self):
        """Falls back to default when file doesn't exist."""
        result = load_prompt("nonexistent-prompt")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_default_prompts_exist(self):
        """All expected default prompts are defined."""
        for name in [
            "documentation-generation",
            "analysis-summary",
            "quality-evaluation",
            "kiro-spec-generation",
        ]:
            prompt = _get_default_prompt(name)
            assert len(prompt) > 0

    def test_variable_substitution(self):
        """Template variables are substituted."""
        result = load_prompt("nonexistent-prompt", variables={"key": "value"})
        # Should not crash even if no variables in default prompt
        assert isinstance(result, str)

    def test_load_real_prompt_from_disk(self):
        """Loads from the repo prompts/ directory if available."""
        # This tests the real filesystem — the prompts/ dir should exist
        result = load_prompt("documentation-generation")
        assert isinstance(result, str)
        assert len(result) > 50  # Should have substantial content


# --- DocAnalysisAgent Tests ---


class TestDocAnalysisAgent:
    def test_analyze_codebase_context(self, stored_analysis):
        agent = DocAnalysisAgent(stored_analysis)
        result = agent.analyze_codebase_context("github_20250101_120000")

        assert result["analysis_id"] == "github_20250101_120000"
        assert result["total_files"] == 15
        assert result["total_lines"] == 800
        assert result["total_dependencies"] == 2
        assert result["total_classes"] == 1
        assert result["total_methods"] == 1
        assert "python" in result["languages"]
        assert result["source_type"] == "github"

    def test_analyze_codebase_context_not_found(self, mock_storage):
        agent = DocAnalysisAgent(mock_storage)
        result = agent.analyze_codebase_context("nonexistent")
        assert "error" in result

    def test_validate_analysis_output_valid(self, stored_analysis):
        agent = DocAnalysisAgent(stored_analysis)
        result = agent.validate_analysis_output("github_20250101_120000")

        assert result["valid"] is True
        assert result["missing_fields"] == []
        assert result["analysis_id"] == "github_20250101_120000"

    def test_validate_analysis_output_not_found(self, mock_storage):
        agent = DocAnalysisAgent(mock_storage)
        result = agent.validate_analysis_output("nonexistent")
        assert result["valid"] is False
        assert "error" in result

    def test_validate_analysis_output_incomplete(self, mock_storage):
        mock_storage.save("incomplete", {"analysis_id": "incomplete"})
        agent = DocAnalysisAgent(mock_storage)
        result = agent.validate_analysis_output("incomplete")
        assert result["valid"] is False
        assert "file_stats" in result["missing_fields"]

    # One attempt: this asserts the fallback, not the retry policy (covered in
    # test_bedrock_invocation.py), and a retried error would sleep for real.
    @patch("utils.bedrock.settings.BEDROCK_MAX_ATTEMPTS", 1)
    def test_generate_kiro_spec_bedrock_error(self, stored_analysis):
        """Graceful fallback when Bedrock is unavailable."""
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "down"}}, "InvokeModel"
        )

        agent = DocAnalysisAgent(stored_analysis)
        agent._client = mock_client
        result = agent.generate_kiro_spec("github_20250101_120000", "main.py")
        assert "error" in result
        assert result["error"] == "Bedrock unavailable"


# --- LLMJudge Tests ---


class TestLLMJudge:
    def test_check_json_structure_valid(self):
        judge = LLMJudge()
        result = judge.check_json_structure('{"key": "value", "num": 42}')
        assert result["valid"] is True
        assert result["type"] == "dict"
        assert "key" in result["keys"]

    def test_check_json_structure_array(self):
        judge = LLMJudge()
        result = judge.check_json_structure("[1, 2, 3]")
        assert result["valid"] is True
        assert result["type"] == "list"
        assert result["length"] == 3

    def test_check_json_structure_invalid(self):
        judge = LLMJudge()
        result = judge.check_json_structure("not json at all")
        assert result["valid"] is False
        assert "error" in result

    def test_score_dimension_invalid(self):
        judge = LLMJudge()
        result = judge.score_dimension("some text", "invalid_dimension")
        assert "error" in result

    def test_scoring_dimensions_defined(self):
        assert len(SCORING_DIMENSIONS) == 5
        assert "accuracy" in SCORING_DIMENSIONS
        assert "completeness" in SCORING_DIMENSIONS
        assert "actionability" in SCORING_DIMENSIONS
        assert "specificity" in SCORING_DIMENSIONS
        assert "correctness" in SCORING_DIMENSIONS

    def test_extract_json_direct(self):
        judge = LLMJudge()
        result = judge._extract_json('{"score": 8, "justification": "good"}')
        assert result is not None
        assert result["score"] == 8

    def test_extract_json_from_code_block(self):
        judge = LLMJudge()
        text = '```json\n{"score": 7, "justification": "ok"}\n```'
        result = judge._extract_json(text)
        assert result is not None
        assert result["score"] == 7

    def test_extract_json_from_text_with_json(self):
        judge = LLMJudge()
        text = 'Here is the result: {"score": 6, "justification": "decent"}'
        result = judge._extract_json(text)
        assert result is not None
        assert result["score"] == 6

    def test_extract_json_returns_none_for_garbage(self):
        judge = LLMJudge()
        assert judge._extract_json("no json here whatsoever") is None


# --- KiroSpecsAgent Tests ---


class TestKiroSpecsAgent:
    def test_get_analysis_context(self, stored_analysis):
        agent = KiroSpecsAgent(stored_analysis)
        result = agent.get_analysis_context("github_20250101_120000")

        assert result["analysis_id"] == "github_20250101_120000"
        assert result["source_type"] == "github"
        assert result["dependency_count"] == 2
        assert result["component_count"] == 1
        assert len(result["components"]) == 1
        assert result["components"][0]["name"] == "App"

    def test_get_analysis_context_not_found(self, mock_storage):
        agent = KiroSpecsAgent(mock_storage)
        result = agent.get_analysis_context("nonexistent")
        assert "error" in result

    def test_validate_specs_valid(self, mock_storage):
        agent = KiroSpecsAgent(mock_storage)
        specs = {
            "requirements": "## Requirement 1\n\nThe system shall process files.",
            "design": "## Architecture\n\nThe system uses a layered architecture.",
            "tasks": "- [ ] 1. Implement parser\n- [ ] 2. Add tests",
        }
        result = agent.validate_specs(specs)
        assert result["valid"] is True
        assert result["issues"] == []

    def test_validate_specs_missing_sections(self, mock_storage):
        agent = KiroSpecsAgent(mock_storage)
        specs = {"requirements": "some text"}
        result = agent.validate_specs(specs)
        assert result["valid"] is False
        assert "Missing 'design' section" in result["issues"]
        assert "Missing 'tasks' section" in result["issues"]

    def test_validate_specs_short_content(self, mock_storage):
        agent = KiroSpecsAgent(mock_storage)
        specs = {"requirements": "short", "design": "short", "tasks": "short"}
        result = agent.validate_specs(specs)
        assert (
            result["valid"] is True
        )  # Missing sections = invalid, short = suggestions
        assert len(result["suggestions"]) > 0

    def test_parse_spec_sections(self):
        text = (
            "# Requirements\n\nThe system shall...\n\n"
            "# Design\n\nArchitecture overview...\n\n"
            "# Tasks\n\n- [ ] 1. First task\n"
        )
        result = KiroSpecsAgent._parse_spec_sections(text)
        assert "system shall" in result["requirements"]
        assert "Architecture" in result["design"]
        assert "First task" in result["tasks"]


# --- StaticAnalysisServer Tests ---


class TestStaticAnalysisServer:
    def test_list_analyses(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.list_analyses()
        assert len(result) == 1
        assert result[0]["analysis_id"] == "github_20250101_120000"

    def test_list_analyses_empty(self, mock_storage):
        server = StaticAnalysisServer(mock_storage)
        result = server.list_analyses()
        assert result == []

    def test_get_file_statistics(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.get_file_statistics("github_20250101_120000")
        assert result["analysis_id"] == "github_20250101_120000"
        assert len(result["file_stats"]) == 2
        assert result["totals"]["files"] == 15
        assert result["totals"]["lines"] == 800

    def test_get_file_statistics_not_found(self, mock_storage):
        server = StaticAnalysisServer(mock_storage)
        result = server.get_file_statistics("nonexistent")
        assert "error" in result

    def test_get_folder_structure(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.get_folder_structure("github_20250101_120000")
        assert result["folder_structure"]["name"] == "root"
        assert len(result["folder_structure"]["children"]) == 2

    def test_get_dependencies(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.get_dependencies("github_20250101_120000")
        assert result["count"] == 2
        assert result["dependencies"][0]["name"] == "fastapi"

    def test_get_dependency_graph(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.get_dependency_graph("github_20250101_120000")
        assert result["node_count"] == 2
        assert result["link_count"] == 1
        assert result["nodes"][0]["id"] == "fastapi"

    def test_get_code_metrics(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.get_code_metrics("github_20250101_120000")
        metrics = result["metrics"]
        assert metrics["total_files"] == 15
        assert metrics["total_lines"] == 800
        assert metrics["total_classes"] == 1
        assert metrics["total_methods"] == 1
        assert metrics["total_imports"] == 2
        assert metrics["total_complexity"] == 3
        assert "python" in metrics["languages"]

    def test_get_upgrade_recommendations(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.get_upgrade_recommendations("github_20250101_120000")
        assert result["count"] == 1
        assert result["upgrade_recommendations"][0]["package"] == "boto3"

    def test_parse_code_python(self, mock_storage):
        server = StaticAnalysisServer(mock_storage)
        source = "def hello():\n    return 'world'\n"
        result = server.parse_code(source, "hello.py")
        assert "error" not in result
        assert result["language"] == "python"
        assert result["line_count"] >= 2
        assert len(result["methods"]) >= 1

    def test_parse_code_unsupported(self, mock_storage):
        server = StaticAnalysisServer(mock_storage)
        result = server.parse_code("some content", "file.xyz")
        assert "error" in result
        assert "Unsupported" in result["error"]

    def test_get_analysis_summary(self, stored_analysis):
        server = StaticAnalysisServer(stored_analysis)
        result = server.get_analysis_summary("github_20250101_120000")
        assert result["analysis_id"] == "github_20250101_120000"
        assert result["source_type"] == "github"
        assert result["summary"]["total_files"] == 15
        assert result["summary"]["total_lines"] == 800
        assert result["summary"]["total_dependencies"] == 2
        assert result["summary"]["has_diagrams"] is True

    def test_get_analysis_summary_not_found(self, mock_storage):
        server = StaticAnalysisServer(mock_storage)
        result = server.get_analysis_summary("nonexistent")
        assert "error" in result
