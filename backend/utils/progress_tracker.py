"""In-memory analysis progress tracking."""

from __future__ import annotations

import threading


class ProgressTracker:
    """Track analysis progress in memory.

    Stores state as: {analysis_id: {percentage, status, current_step, message}}

    Accessed from two threads: the analysis pipeline writes progress from a
    worker thread (FastAPI runs the blocking background tasks in its
    threadpool), while the status endpoint reads from the event loop thread.
    A lock guards every access, and ``get`` hands back a snapshot copy so a
    caller reading several fields cannot observe a half-applied update.
    """

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, analysis_id: str) -> None:
        """Initialize progress tracking for an analysis."""
        with self._lock:
            self._state[analysis_id] = {
                "percentage": 0,
                "status": "processing",
                "current_step": "initializing",
                "message": "Analysis started",
            }

    def update(
        self,
        analysis_id: str,
        percentage: int,
        step: str,
        message: str,
    ) -> None:
        """Update progress for an analysis.

        Args:
            analysis_id: The analysis to update.
            percentage: Progress percentage (0-100).
            step: Current processing step name.
            message: Human-readable status message.
        """
        with self._lock:
            self._state[analysis_id] = {
                "percentage": percentage,
                "status": "processing",
                "current_step": step,
                "message": message,
            }

    def complete(self, analysis_id: str) -> None:
        """Mark an analysis as completed."""
        with self._lock:
            state = self._state.setdefault(analysis_id, {})
            state.update(
                {
                    "percentage": 100,
                    "status": "completed",
                    "current_step": "done",
                    "message": "Analysis completed successfully",
                }
            )

    def fail(self, analysis_id: str, error: str) -> None:
        """Mark an analysis as failed.

        Args:
            analysis_id: The analysis that failed.
            error: Error message describing the failure.
        """
        with self._lock:
            state = self._state.setdefault(analysis_id, {})
            state.update(
                {
                    "percentage": state.get("percentage", 0),
                    "status": "failed",
                    "current_step": "error",
                    "message": error,
                }
            )

    def get(self, analysis_id: str) -> dict | None:
        """Get progress state for an analysis.

        Returns:
            A copy of the state dict with percentage, status, current_step and
            message — or None if not tracked. The copy means readers never see
            a partially applied update from the analysis worker thread.
        """
        with self._lock:
            state = self._state.get(analysis_id)
            return dict(state) if state is not None else None
