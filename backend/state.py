"""Application state singleton."""

from __future__ import annotations

from typing import Any


class AppState:
    """Singleton holding shared application state references.

    Stores references to storage manager, progress tracker,
    and analysis count for the lifetime of the application.
    """

    _instance: AppState | None = None

    def __new__(cls) -> AppState:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self.storage_manager: Any = None
        self.progress_tracker: Any = None
        self.analysis_count: int = 0

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (useful for testing)."""
        cls._instance = None


app_state = AppState()
