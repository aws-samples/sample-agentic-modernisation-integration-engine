"""ZIP bomb detection and safe extraction utilities."""

from __future__ import annotations

import zipfile
from pathlib import Path

# Maximum allowed uncompressed size: 2 GB
MAX_UNCOMPRESSED_SIZE: int = 2 * 1024 * 1024 * 1024

# Chunk size for streaming extraction (64 KB)
CHUNK_SIZE: int = 64 * 1024


class ZipBombError(Exception):
    """Raised when a ZIP file exceeds safe extraction limits."""


def check_zip_safety(zip_path: str | Path) -> None:
    """Validate a ZIP file does not exceed decompression limits.

    Raises:
        ZipBombError: If the declared uncompressed size exceeds MAX_UNCOMPRESSED_SIZE.
        zipfile.BadZipFile: If the file is not a valid ZIP.
    """
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        total_uncompressed = sum(info.file_size for info in zf.infolist())
        if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
            raise ZipBombError(
                f"ZIP uncompressed size ({total_uncompressed:,} bytes) "
                f"exceeds limit ({MAX_UNCOMPRESSED_SIZE:,} bytes)"
            )


def safe_extract(zip_path: str | Path, target_dir: str | Path) -> list[str]:
    """Extract ZIP file safely with chunk-based reading.

    Validates total size before extraction, then extracts using
    chunked reads to avoid memory spikes.

    Returns:
        List of extracted file paths (relative to target_dir).

    Raises:
        ZipBombError: If cumulative extracted bytes exceed MAX_UNCOMPRESSED_SIZE.
        zipfile.BadZipFile: If the file is not a valid ZIP.
    """
    zip_path = Path(zip_path)
    target_dir = Path(target_dir)

    check_zip_safety(zip_path)

    extracted_files: list[str] = []
    total_extracted: int = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            # Skip directories
            if info.is_dir():
                continue

            # Prevent path traversal
            member_path = Path(info.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                continue

            out_path = target_dir / member_path
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(info) as src, open(out_path, "wb") as dst:
                while True:
                    chunk = src.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_extracted += len(chunk)
                    if total_extracted > MAX_UNCOMPRESSED_SIZE:
                        raise ZipBombError(
                            f"Extraction exceeded {MAX_UNCOMPRESSED_SIZE:,} bytes"
                        )
                    dst.write(chunk)

            extracted_files.append(str(member_path))

    return extracted_files
