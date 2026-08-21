"""Transformation management — CRUD for custom transformation definitions."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from models import (
    TransformationDefinitionCreate,
    TransformationDefinitionUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transformations", tags=["transformations"])

# Storage paths for transformation definitions.
_LOCAL_STORAGE = Path("/app/shared/transformation_def")
_DEF_FILE = "definitions.json"


def _get_storage_path() -> Path:
    """Get the storage path, creating it if needed."""
    path = _LOCAL_STORAGE
    # Fallback to local temp for development.
    if not path.parent.exists():
        path = Path(os.environ.get("TRANSFORM_DEF_PATH", "/tmp/transformation_def"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_definitions() -> list[dict]:
    """Load definitions from JSON storage."""
    path = _get_storage_path() / _DEF_FILE
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_definitions(definitions: list[dict]) -> None:
    """Save definitions to JSON storage."""
    path = _get_storage_path() / _DEF_FILE
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(definitions, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp_path), str(path))


@router.get("/definitions")
async def list_definitions() -> dict:
    """List all transformation definitions.

    Returns:
        {"definitions": [...]}
    """
    definitions = _load_definitions()
    return {"definitions": definitions}


@router.post("/definitions", status_code=201)
async def create_definition(body: TransformationDefinitionCreate) -> dict:
    """Create a new transformation definition.

    Returns:
        The created definition.
    """
    if not body.name:
        raise HTTPException(status_code=400, detail="Name is required")

    definitions = _load_definitions()

    new_def = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "description": body.description,
        "type": "custom",
        "definition_path": body.definition_path,
        "published": False,
    }

    definitions.append(new_def)
    _save_definitions(definitions)

    return new_def


@router.put("/definitions/{definition_id}")
async def update_definition(
    definition_id: str, body: TransformationDefinitionUpdate
) -> dict:
    """Update an existing transformation definition.

    Returns:
        The updated definition.
    """
    definitions = _load_definitions()
    target = None
    for d in definitions:
        if d["id"] == definition_id:
            target = d
            break

    if target is None:
        raise HTTPException(status_code=404, detail="Definition not found")

    if body.name is not None:
        target["name"] = body.name
    if body.description is not None:
        target["description"] = body.description
    if body.definition_path is not None:
        target["definition_path"] = body.definition_path
    if body.published is not None:
        target["published"] = body.published

    _save_definitions(definitions)
    return target


@router.delete("/definitions/{definition_id}")
async def delete_definition(definition_id: str) -> dict:
    """Delete a transformation definition.

    Returns:
        {"detail": "Deleted"}
    """
    definitions = _load_definitions()
    original_count = len(definitions)
    definitions = [d for d in definitions if d["id"] != definition_id]

    if len(definitions) == original_count:
        raise HTTPException(status_code=404, detail="Definition not found")

    _save_definitions(definitions)
    return {"detail": "Deleted"}
