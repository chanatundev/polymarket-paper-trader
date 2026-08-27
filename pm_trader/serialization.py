"""Stable, secret-aware serialization helpers for records and reports."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:private.?key|secret|password|authorization|api.?key|signature|mnemonic|wallet|proxywallet|allowance|credential|address$|^(?:maker|taker|owner|user)(?:_address)?$)",
    re.IGNORECASE,
)


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """Return JSON-safe data with credentials and wallet-sensitive fields redacted."""
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return sanitize(dataclasses.asdict(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float cannot be serialized")
        return value
    return value


def canonical_json(value: Any) -> str:
    """Serialize data deterministically for hashes, storage, and replay identity."""
    return json.dumps(sanitize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bounded_json(value: Any, max_bytes: int) -> tuple[str, bool]:
    """Serialize a value and return ``(text, truncated)`` under a hard byte limit."""
    text = canonical_json(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    marker = {"truncated": True, "reason": "response_size_limit"}
    marker_text = canonical_json(marker)
    if len(marker_text.encode("utf-8")) > max_bytes:
        return "{}", True
    return marker_text, True
