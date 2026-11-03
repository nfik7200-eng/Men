"""Database error types. Messages never contain secret values or file paths."""

from bip.core.exceptions import BIPError


class DatabaseError(BIPError):
    """The operational database is unavailable or an operation on it failed."""


class RecordNotFoundError(BIPError):
    """The requested record does not exist."""


class DuplicateRecordError(BIPError):
    """The record already exists (unique constraint)."""


class InvalidRecordError(BIPError):
    """The record failed validation against BIP contracts."""
