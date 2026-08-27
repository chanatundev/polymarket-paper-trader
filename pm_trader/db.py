"""Compatibility import for the append-only SQLite store."""

from .storage import Database, SQLiteStore

__all__ = ["Database", "SQLiteStore"]
