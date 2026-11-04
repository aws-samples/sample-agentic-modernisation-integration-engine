"""Database discovery routes — SQL Server → Aurora MySQL assessment.

Response envelopes:
  POST   /api/db-discovery/upload          → {"discovery_id": str, "status": str, "message": str}
  POST   /api/db-discovery/github          → {"discovery_id": str, "status": str, "message": str}
  GET    /api/db-discovery/rules           → {"rules": [...]}
  GET    /api/db-discovery/{id}/status     → {"discovery_id", "status", "progress", "current_step", "message"}
  GET    /api/db-discovery/{id}            → full report (top-level object)
  GET    /api/db-discovery/{id}/report.md  → text/markdown attachment
  GET    /api/db-discoveries               → {"discoveries": [...]}
  DELETE /api/db-discovery/{id}            → {"detail": "Deleted"}
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
import zipfile

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from state import app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["db-discovery"])

DB_DISCOVERY_STORAGE_PATH = os.environ.get(
    "DB_DISCOVERY_STORAGE_PATH", "/app/temp/db_discoveries"
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_SUBPATH = re.compile(r"^[A-Za-z0-9_./ -]+$")


class DbDiscoveryGithubRequest(BaseModel):
    repo_url: str
    branch: str = "main"
    pat_token: str = ""
    subpath: str = ""


class DbDiscoveryStartResponse(BaseModel):
    discovery_id: str
    status: str = "processing"
    message: str = "Discovery started"


def _validate_id(discovery_id: str) -> None:
    if not discovery_id or not _SAFE_ID.match(discovery_id):
        raise HTTPException(status_code=400, detail="Invalid discovery ID")


def validate_subpath(subpath: str) -> str:
    """Normalise a repository sub-path, rejecting anything that could leave the clone."""
    sub = subpath.strip().strip("/")
    if not sub:
        return ""
    if (
        not _SAFE_SUBPATH.match(sub)
        or ".." in sub.split("/")
        or sub.startswith(("/", "~"))
        or "//" in sub
    ):
        raise HTTPException(status_code=400, detail="Invalid subpath")
    return sub


def _storage():
    storage = app_state.db_discovery_storage
    if storage is None:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    return storage


def _new_id(source: str) -> str:
    return f"{source}_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}"


def _unique_id(source: str) -> str:
    """Repo convention {source}_{YYYYMMDD_HHMMSS}, with a suffix if a second run lands in the same second."""
    base = _new_id(source)
    storage = app_state.db_discovery_storage
    tracker = app_state.progress_tracker
    candidate, n = base, 1
    while (tracker and tracker.get(candidate)) or (storage and storage.load(candidate)):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _service():
    from services.db_discovery.discovery_service import DatabaseDiscoveryService

    return DatabaseDiscoveryService(
        storage=app_state.db_discovery_storage, tracker=app_state.progress_tracker
    )


# --- Background tasks (plain def: FastAPI runs them in the threadpool) --------------------------


def _run_upload(zip_path: str, discovery_id: str) -> None:
    from utils.zip_safety import ZipBombError, safe_extract

    tracker = app_state.progress_tracker
    extract_dir = tempfile.mkdtemp(prefix="dbdiscovery_")
    try:
        tracker.update(discovery_id, 2, "extracting", "Extracting archive...")
        safe_extract(zip_path, extract_dir)
        _service().run(extract_dir, discovery_id, "upload")
        tracker.complete(discovery_id)
    except (ZipBombError, zipfile.BadZipFile) as exc:
        tracker.fail(discovery_id, f"Invalid archive: {exc}")
    except Exception as exc:
        logger.exception("Database discovery %s failed", discovery_id)
        tracker.fail(discovery_id, str(exc))
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        shutil.rmtree(os.path.dirname(zip_path), ignore_errors=True)


def _run_github(
    body: DbDiscoveryGithubRequest, subpath: str, discovery_id: str
) -> None:
    from services.github_handler import GitHubHandler

    tracker = app_state.progress_tracker
    handler = GitHubHandler()
    repo_path = ""
    try:
        tracker.update(discovery_id, 2, "cloning", "Cloning repository...")
        repo_path = handler.clone(
            repo_url=body.repo_url,
            branch=body.branch,
            pat_token=body.pat_token,
            timeout=600,
            sparse_paths=[subpath] if subpath else None,
        )
        root = (
            os.path.realpath(os.path.join(repo_path, subpath)) if subpath else repo_path
        )
        if os.path.commonpath([root, os.path.realpath(repo_path)]) != os.path.realpath(
            repo_path
        ):
            raise ValueError("Subpath escapes the repository")
        if not os.path.isdir(root):
            raise ValueError(f"Path '{subpath}' was not found in the repository")
        _service().run(
            root,
            discovery_id,
            "github",
            source_url=body.repo_url,
            branch=body.branch,
            subpath=subpath,
        )
        tracker.complete(discovery_id)
    except Exception as exc:
        # A git error message can contain the clone URL, and the URL carries the PAT.
        message = str(exc)
        if body.pat_token:
            message = message.replace(body.pat_token, "***")
        logger.error("Database discovery %s failed: %s", discovery_id, message)
        tracker.fail(discovery_id, message)
    finally:
        if repo_path:
            handler.cleanup(repo_path)


# --- Endpoints ---------------------------------------------------------------------------------


@router.post("/db-discovery/upload", response_model=DbDiscoveryStartResponse)
async def start_upload(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
) -> DbDiscoveryStartResponse:
    """Upload a ZIP of SQL scripts and/or application code; discovery runs in the background."""
    _storage()
    safe_name = os.path.basename(file.filename or "")
    if not safe_name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are accepted")
    tmp_dir = tempfile.mkdtemp(prefix="dbupload_")
    zip_path = os.path.join(tmp_dir, "upload.zip")
    with open(zip_path, "wb") as f:
        f.write(await file.read())
    if not zipfile.is_zipfile(zip_path):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="File is not a valid ZIP archive")

    discovery_id = _unique_id("dbupload")
    app_state.progress_tracker.start(discovery_id)
    background_tasks.add_task(_run_upload, zip_path, discovery_id)
    return DbDiscoveryStartResponse(discovery_id=discovery_id)


@router.post("/db-discovery/github", response_model=DbDiscoveryStartResponse)
async def start_github(
    body: DbDiscoveryGithubRequest, background_tasks: BackgroundTasks
) -> DbDiscoveryStartResponse:
    """Clone a repository (optionally only a sub-path) and run discovery in the background."""
    _storage()
    if not body.repo_url.strip():
        raise HTTPException(status_code=400, detail="repo_url is required")
    subpath = validate_subpath(body.subpath)
    from services.github_handler import GitHubHandler

    try:
        # Validate before accepting the job so a bad URL is a 400, not a failed background run.
        # _validate_url reads no instance state; __new__ skips the constructor's mkdir.
        GitHubHandler.__new__(GitHubHandler)._validate_url(body.repo_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    discovery_id = _unique_id("dbgithub")
    app_state.progress_tracker.start(discovery_id)
    background_tasks.add_task(_run_github, body, subpath, discovery_id)
    return DbDiscoveryStartResponse(discovery_id=discovery_id)


@router.get("/db-discovery/rules")
async def list_rules() -> dict:
    """The full rule catalogue (T-SQL and application rules)."""
    from services.db_discovery.discovery_service import all_rules

    return {"rules": all_rules()}


@router.get("/db-discovery/{discovery_id}/status")
async def get_status(discovery_id: str) -> dict:
    _validate_id(discovery_id)
    state = (
        app_state.progress_tracker.get(discovery_id)
        if app_state.progress_tracker
        else None
    )
    if state is None:
        if _storage().load(discovery_id):
            return {
                "discovery_id": discovery_id,
                "status": "completed",
                "progress": 100,
                "current_step": "done",
                "message": "Discovery completed",
            }
        raise HTTPException(status_code=404, detail="Discovery not found")
    return {
        "discovery_id": discovery_id,
        "status": state["status"],
        "progress": state["percentage"],
        "current_step": state["current_step"],
        "message": state["message"],
    }


def _load_or_404(discovery_id: str) -> dict:
    _validate_id(discovery_id)
    data = _storage().load(discovery_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Discovery not found")
    return data


@router.get("/db-discovery/{discovery_id}/report.md")
async def get_markdown(discovery_id: str) -> Response:
    from services.db_discovery.discovery_service import render_markdown

    report = _load_or_404(discovery_id)
    return Response(
        content=render_markdown(report),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{discovery_id}.md"'},
    )


@router.get("/db-discovery/{discovery_id}")
async def get_report(discovery_id: str) -> dict:
    return _load_or_404(discovery_id)


@router.get("/db-discoveries")
async def list_discoveries() -> dict:
    storage = _storage()
    items = []
    for item in storage.list_analyses():
        data = storage.load(item.analysis_id) or {}
        summary = data.get("summary", {})
        items.append(
            {
                "discovery_id": item.analysis_id,
                "source_type": data.get("source_type", item.source_type),
                "source_url": data.get("source_url") or "",
                "subpath": data.get("subpath", ""),
                "completed_at": data.get("completed_at", item.created_at),
                "object_counts": summary.get("object_counts", {}),
                "finding_counts": summary.get("finding_counts", {}),
            }
        )
    return {"discoveries": items}


@router.delete("/db-discovery/{discovery_id}")
async def delete_discovery(discovery_id: str) -> dict:
    _validate_id(discovery_id)
    if not _storage().delete(discovery_id):
        raise HTTPException(status_code=404, detail="Discovery not found")
    return {"detail": "Deleted"}
