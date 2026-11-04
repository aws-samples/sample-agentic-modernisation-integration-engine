"""Engine-neutral value normalisation, so equal data hashes equally on SQL Server and MySQL.

The two drivers return different Python types for the same data: SQL Server `bit` is a bool while
MySQL `TINYINT(1)` is an int; `uniqueidentifier` is a `uuid.UUID` while `CHAR(36)` is a string;
`datetimeoffset` carries a time zone while `DATETIME(6)` does not. Every value is reduced to one
canonical string before hashing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import unicodedata
import uuid
from decimal import Decimal
from typing import Any

NULL = "∅"
SEP = "\x1f"


def normalize_value(value: Any) -> str:
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        text = format(value.normalize(), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, float):
        return format(value, ".12g")
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, dt.timedelta):  # PyMySQL returns MySQL TIME as timedelta
        total = value.total_seconds()
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return dt.time(
            int(hours) % 24, int(minutes), int(seconds), value.microseconds
        ).isoformat(timespec="microseconds")
    if isinstance(value, uuid.UUID):
        return str(value).lower()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    text = unicodedata.normalize("NFC", str(value)).rstrip(" ")
    stripped = text.lstrip()
    if stripped[:1] in ("{", "[") and len(stripped) < 5_000_000:
        try:
            return json.dumps(
                json.loads(stripped),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        except ValueError:
            pass
    if len(text) == 36 and text.count("-") == 4:
        try:
            return str(uuid.UUID(text)).lower()
        except ValueError:
            pass
    return text


def merge_json_chunks(rows: list[tuple]) -> list[tuple]:
    """Rejoin SQL Server FOR JSON output, which the server splits into ~2033-character rows.

    Applies only when the set has more than one row, every row is a single string column, and the
    concatenated text parses as JSON; anything else is returned unchanged.
    """
    if len(rows) < 2 or not all(len(r) == 1 and isinstance(r[0], str) for r in rows):
        return rows
    joined = "".join(r[0] for r in rows)
    if joined.lstrip()[:1] not in ("{", "["):
        return rows
    try:
        json.loads(joined)
    except ValueError:
        return rows
    return [(joined,)]


def normalize_row(row: tuple | list) -> str:
    return SEP.join(normalize_value(v) for v in row)


def row_digest(row: tuple | list) -> int:
    return int.from_bytes(
        hashlib.sha256(normalize_row(row).encode("utf-8")).digest()[:16], "big"
    )


class TableChecksum:
    """Order-independent checksum: sum of per-row 128-bit digests modulo 2**128, plus a count.

    A sum (not XOR) keeps duplicate rows from cancelling each other out.
    """

    MOD = 1 << 128

    def __init__(self) -> None:
        self.rows = 0
        self.value = 0

    def add(self, row) -> None:
        self.rows += 1
        self.value = (self.value + row_digest(row)) % self.MOD

    def hexdigest(self) -> str:
        return f"{self.value:032x}"
