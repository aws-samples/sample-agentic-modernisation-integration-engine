"""Analysis routes — endpoints for code analysis pipeline.

Response envelopes:
  POST /api/analyze/upload         → {"analysis_id": str, "status": str, "message": str}
  POST /api/analyze/github         → {"analysis_id": str, "status": str, "message": str}
  GET  /api/analysis/{id}/status   → {"analysis_id": str, "status": str, "progress": int,
                                       "current_step": str, "message": str}
  GET  /api/analysis/{id}/summary  → full stored analysis object (top-level dict)
  GET  /api/analysis/{id}/file-stats          → {"file_stats": [...]}
  GET  /api/analysis/{id}/folder-structure    → {"folder_structure": {...}}
  GET  /api/analysis/{id}/dependencies        → {"dependencies": [...]}
  GET  /api/analysis/{id}/dependency-graph    → {"dependency_graph": {"nodes": [...], "links": [...]}}
  GET  /api/analysis/{id}/upgrade-recommendations → {"upgrade_recommendations": [...]}
  GET  /api/analysis/{id}/diagrams            → {"diagrams": {...}}
  GET  /api/analysis/{id}/mermaid             → raw mermaid dict (class/sequence/integration keys)
  GET  /api/analysis/{id}/documentation       → {"documentation": str, "ai_enrichment_status": str}
  GET  /api/analyses                          → {"analyses": [...]}
  DELETE /api/analysis/{id}                   → {"detail": "Deleted"}
"""

from __future__ import annotations

import os
import re
import tempfile
import time

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from models import GithubAnalysisRequest, UploadResponse
from state import app_state

router = APIRouter(prefix="/api", tags=["analysis"])

# Valid analysis ID: alphanumeric, dash, underscore only.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_analysis_id(analysis_id: str) -> None:
    """Validate analysis_id against injection."""
    if not analysis_id or not _SAFE_ID.match(analysis_id):
        raise HTTPException(status_code=400, detail="Invalid analysis ID")


def _get_analysis_or_404(analysis_id: str) -> dict:
    """Load analysis from storage or raise 404."""
    _validate_analysis_id(analysis_id)
    storage = app_state.storage_manager
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    data = storage.load(analysis_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return data


def _generate_analysis_id(source: str) -> str:
    """Generate a unique analysis ID: {source}_{YYYYMMDD_HHMMSS}."""
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    return f"{source}_{ts}"


# --- Upload endpoint ---


@router.post("/analyze/upload", response_model=UploadResponse)
async def upload_analysis(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> UploadResponse:
    """Upload a ZIP file for analysis. Returns immediately; runs in background."""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are accepted")

    analysis_id = _generate_analysis_id("upload")

    # Save uploaded file to temp location.
    # Use only the basename of the client-supplied filename and confirm the
    # resolved path stays within tmp_dir — a filename like "../../x.zip" must
    # not escape the temp directory (path traversal, CWE-22).
    tmp_dir = tempfile.mkdtemp(prefix="upload_")
    safe_name = os.path.basename(file.filename)
    if not safe_name or not safe_name.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Invalid file name")
    zip_path = os.path.join(tmp_dir, safe_name)
    if os.path.commonpath(
        [os.path.realpath(zip_path), os.path.realpath(tmp_dir)]
    ) != os.path.realpath(tmp_dir):
        raise HTTPException(status_code=400, detail="Invalid file name")
    with open(zip_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Start background analysis.
    tracker = app_state.progress_tracker
    if tracker:
        tracker.start(analysis_id)

    from services.code_parser_service import CodeParserService

    service = CodeParserService()
    # analyze_zip is a plain `def` and must stay one: the whole pipeline blocks,
    # so FastAPI has to run it in its threadpool rather than on the event loop.
    background_tasks.add_task(service.analyze_zip, zip_path, analysis_id)

    return UploadResponse(
        analysis_id=analysis_id,
        status="processing",
        message="Analysis started",
    )


# --- GitHub endpoint ---


@router.post("/analyze/github", response_model=UploadResponse)
async def github_analysis(
    body: GithubAnalysisRequest,
    background_tasks: BackgroundTasks,
) -> UploadResponse:
    """Analyze a GitHub repository. Clones and runs pipeline in background."""
    if not body.repo_url:
        raise HTTPException(status_code=400, detail="repo_url is required")

    analysis_id = _generate_analysis_id("github")

    tracker = app_state.progress_tracker
    if tracker:
        tracker.start(analysis_id)

    background_tasks.add_task(_run_github_analysis, body, analysis_id)

    return UploadResponse(
        analysis_id=analysis_id,
        status="processing",
        message="Analysis started",
    )


def _run_github_analysis(body: GithubAnalysisRequest, analysis_id: str) -> None:
    """Background task: clone repo and run analysis pipeline.

    Deliberately a plain `def`, not `async def`. Every step here blocks — the
    GitPython clone, the Tree-sitter parse walk, and the Bedrock enrichment
    calls — and none of it awaits. Declared `async def` it would hold the
    uvicorn event loop for the whole analysis (~2 minutes), so the POST
    response never flushes and every other request queues behind it. As a
    plain function FastAPI runs it in its threadpool instead.
    """
    from services.code_parser_service import CodeParserService
    from services.github_handler import GitHubHandler

    handler = GitHubHandler()
    try:
        repo_path = handler.clone(
            repo_url=body.repo_url,
            branch=body.branch,
            pat_token=body.pat_token,
        )
        service = CodeParserService()
        service.analyze_directory(
            repo_path,
            analysis_id,
            source_type="github",
            source_url=body.repo_url,
        )
    except Exception as exc:
        tracker = app_state.progress_tracker
        if tracker:
            tracker.fail(analysis_id, str(exc))


# --- Status endpoint ---


@router.get("/analysis/{analysis_id}/status")
async def get_analysis_status(analysis_id: str) -> dict:
    """Get analysis progress status."""
    _validate_analysis_id(analysis_id)
    tracker = app_state.progress_tracker
    if not tracker:
        raise HTTPException(status_code=500, detail="Tracker not initialized")

    state = tracker.get(analysis_id)
    if state is None:
        # Check if completed in storage.
        storage = app_state.storage_manager
        if storage and storage.load(analysis_id):
            return {
                "analysis_id": analysis_id,
                "status": "completed",
                "progress": 100,
                "current_step": "done",
                "message": "Analysis completed",
            }
        raise HTTPException(status_code=404, detail="Analysis not found")

    return {
        "analysis_id": analysis_id,
        "status": state["status"],
        "progress": state["percentage"],
        "current_step": state["current_step"],
        "message": state["message"],
    }


# --- Summary endpoint ---


@router.get("/analysis/{analysis_id}/summary")
async def get_analysis_summary(analysis_id: str) -> dict:
    """Get the full stored analysis object."""
    return _get_analysis_or_404(analysis_id)


# --- File stats endpoint ---


@router.get("/analysis/{analysis_id}/file-stats")
async def get_file_stats(analysis_id: str) -> dict:
    """Get file statistics for an analysis."""
    data = _get_analysis_or_404(analysis_id)
    return {"file_stats": data.get("file_stats", [])}


# --- Folder structure endpoint ---


@router.get("/analysis/{analysis_id}/folder-structure")
async def get_folder_structure(analysis_id: str) -> dict:
    """Get folder tree structure for an analysis."""
    data = _get_analysis_or_404(analysis_id)
    return {"folder_structure": data.get("folder_structure", {})}


# --- Dependencies endpoint ---


@router.get("/analysis/{analysis_id}/dependencies")
async def get_dependencies(analysis_id: str) -> dict:
    """Get dependencies for an analysis."""
    data = _get_analysis_or_404(analysis_id)
    return {"dependencies": data.get("dependencies", [])}


# --- Dependency graph endpoint ---


@router.get("/analysis/{analysis_id}/dependency-graph")
async def get_dependency_graph(analysis_id: str) -> dict:
    """Get dependency graph (nodes + links) for an analysis."""
    data = _get_analysis_or_404(analysis_id)
    return {
        "dependency_graph": data.get("dependency_graph", {"nodes": [], "links": []})
    }


# --- Upgrade recommendations endpoint ---


@router.get("/analysis/{analysis_id}/upgrade-recommendations")
async def get_upgrade_recommendations(analysis_id: str) -> dict:
    """Get version upgrade recommendations."""
    data = _get_analysis_or_404(analysis_id)
    return {"upgrade_recommendations": data.get("upgrade_recommendations", [])}


# --- Diagrams endpoint ---


@router.get("/analysis/{analysis_id}/diagrams")
async def get_diagrams(analysis_id: str) -> dict:
    """Get generated Mermaid diagrams."""
    data = _get_analysis_or_404(analysis_id)
    return {"diagrams": data.get("diagrams", {})}


# --- Mermaid raw endpoint ---


@router.get("/analysis/{analysis_id}/mermaid")
async def get_mermaid(analysis_id: str) -> dict:
    """Get raw Mermaid diagram source code."""
    data = _get_analysis_or_404(analysis_id)
    return data.get("diagrams", {})


# --- Documentation endpoint ---


@router.get("/analysis/{analysis_id}/documentation")
async def get_documentation(analysis_id: str) -> dict:
    """Get AI-generated documentation for an analysis."""
    data = _get_analysis_or_404(analysis_id)
    return {
        "documentation": data.get("ai_documentation", ""),
        # No default of "skipped": an absent status means enrichment never
        # recorded an outcome, which is not the same claim as "Bedrock was
        # unavailable or we chose not to run it". Reporting the empty string
        # lets the client show its neutral "nothing here yet" state instead of
        # asserting a skip that never happened.
        "ai_enrichment_status": data.get("ai_enrichment_status", ""),
    }


# --- List analyses endpoint ---


@router.get("/analyses")
async def list_analyses() -> dict:
    """List all stored analyses."""
    storage = app_state.storage_manager
    if not storage:
        return {"analyses": []}
    items = storage.list_analyses()
    return {"analyses": [item.model_dump() for item in items]}


# --- Delete analysis endpoint ---


@router.delete("/analysis/{analysis_id}")
async def delete_analysis(analysis_id: str) -> dict:
    """Delete a stored analysis."""
    _validate_analysis_id(analysis_id)
    storage = app_state.storage_manager
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    deleted = storage.delete(analysis_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return {"detail": "Deleted"}
