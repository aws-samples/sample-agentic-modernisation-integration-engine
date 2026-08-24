"""Integration tests — Specialist agents: ATX Analysis Agent and ATX Transform Agent health."""

from __future__ import annotations

import sys
from pathlib import Path

# Add ATX agent directories to sys.path so we can import their FastAPI apps.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATX_ANALYSIS_DIR = _REPO_ROOT / "atx-analysis-agent"
_ATX_TRANSFORM_DIR = _REPO_ROOT / "atx-transform-agent"

if str(_ATX_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ATX_ANALYSIS_DIR))
if str(_ATX_TRANSFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_ATX_TRANSFORM_DIR))


class TestAtxAnalysisAgentHealth:
    """Test ATX Analysis Agent health endpoint."""

    def test_health_returns_200(self):
        """GET /health on ATX Analysis Agent returns 200 with status=healthy."""
        # Import inside function to avoid module-level side effects.
        # Remove any cached modules from the other agent to prevent conflicts.
        saved_modules = {}
        conflicting = ["config", "services", "main"]
        for mod_name in list(sys.modules.keys()):
            for conflict in conflicting:
                if mod_name == conflict or mod_name.startswith(conflict + "."):
                    saved_modules[mod_name] = sys.modules.pop(mod_name)

        # Temporarily prepend atx-analysis-agent path
        original_path = sys.path[:]
        sys.path = [str(_ATX_ANALYSIS_DIR)] + [
            p for p in sys.path if p != str(_ATX_TRANSFORM_DIR)
        ]
        try:
            import importlib

            main_mod = importlib.import_module("main")
            from fastapi.testclient import TestClient

            client = TestClient(main_mod.app)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}
        finally:
            sys.path = original_path
            # Restore original modules
            for mod_name in list(sys.modules.keys()):
                for conflict in conflicting:
                    if mod_name == conflict or mod_name.startswith(conflict + "."):
                        del sys.modules[mod_name]
            sys.modules.update(saved_modules)

    def test_analysis_definitions_available(self):
        """GET /analysis-definitions on ATX Analysis Agent returns definitions."""
        saved_modules = {}
        conflicting = ["config", "services", "main"]
        for mod_name in list(sys.modules.keys()):
            for conflict in conflicting:
                if mod_name == conflict or mod_name.startswith(conflict + "."):
                    saved_modules[mod_name] = sys.modules.pop(mod_name)

        original_path = sys.path[:]
        sys.path = [str(_ATX_ANALYSIS_DIR)] + [
            p for p in sys.path if p != str(_ATX_TRANSFORM_DIR)
        ]
        try:
            import importlib

            main_mod = importlib.import_module("main")
            from fastapi.testclient import TestClient

            client = TestClient(main_mod.app)
            response = client.get("/analysis-definitions")
            assert response.status_code == 200
            data = response.json()
            assert "definitions" in data
        finally:
            sys.path = original_path
            for mod_name in list(sys.modules.keys()):
                for conflict in conflicting:
                    if mod_name == conflict or mod_name.startswith(conflict + "."):
                        del sys.modules[mod_name]
            sys.modules.update(saved_modules)


class TestAtxTransformAgentHealth:
    """Test ATX Transform Agent health endpoint."""

    def test_health_returns_200(self):
        """GET /health on ATX Transform Agent returns 200 with status=healthy."""
        saved_modules = {}
        conflicting = ["config", "services", "main"]
        for mod_name in list(sys.modules.keys()):
            for conflict in conflicting:
                if mod_name == conflict or mod_name.startswith(conflict + "."):
                    saved_modules[mod_name] = sys.modules.pop(mod_name)

        original_path = sys.path[:]
        sys.path = [str(_ATX_TRANSFORM_DIR)] + [
            p for p in sys.path if p != str(_ATX_ANALYSIS_DIR)
        ]
        try:
            import importlib

            main_mod = importlib.import_module("main")
            from fastapi.testclient import TestClient

            client = TestClient(main_mod.app)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}
        finally:
            sys.path = original_path
            for mod_name in list(sys.modules.keys()):
                for conflict in conflicting:
                    if mod_name == conflict or mod_name.startswith(conflict + "."):
                        del sys.modules[mod_name]
            sys.modules.update(saved_modules)

    def test_transformations_list(self):
        """GET /transformations on ATX Transform Agent returns definitions."""
        saved_modules = {}
        conflicting = ["config", "services", "main"]
        for mod_name in list(sys.modules.keys()):
            for conflict in conflicting:
                if mod_name == conflict or mod_name.startswith(conflict + "."):
                    saved_modules[mod_name] = sys.modules.pop(mod_name)

        original_path = sys.path[:]
        sys.path = [str(_ATX_TRANSFORM_DIR)] + [
            p for p in sys.path if p != str(_ATX_ANALYSIS_DIR)
        ]
        try:
            import importlib

            main_mod = importlib.import_module("main")
            from fastapi.testclient import TestClient

            client = TestClient(main_mod.app)
            response = client.get("/transformations")
            assert response.status_code == 200
            data = response.json()
            assert "definitions" in data
        finally:
            sys.path = original_path
            for mod_name in list(sys.modules.keys()):
                for conflict in conflicting:
                    if mod_name == conflict or mod_name.startswith(conflict + "."):
                        del sys.modules[mod_name]
            sys.modules.update(saved_modules)
