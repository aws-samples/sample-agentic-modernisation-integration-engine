"""Security fix routes — vulnerability remediation endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["security-fix"])
