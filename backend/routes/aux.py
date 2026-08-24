"""Auxiliary routes — prompts, IaC, misc endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["aux"])
