"""BIP exception hierarchy.

Every error raised deliberately by BIP code derives from :class:`BIPError`, so callers can
distinguish expected, explained failures from programming errors. Messages must never contain
secret values; build them from field names and reasons, not from raw input.
"""


class BIPError(Exception):
    """Base class for all deliberate BIP errors."""


class ConfigurationError(BIPError):
    """Settings are missing, malformed or inconsistent."""


class SecretError(BIPError):
    """A secret reference is invalid or its source cannot be read."""


class UnsafeURLError(BIPError):
    """A URL failed safety validation (scheme, host, address range or format)."""


class StoragePathError(BIPError):
    """A storage path is invalid, escapes the storage root, or would overwrite data."""


class StorageLimitError(BIPError):
    """A write would exceed the configured BIP data quota or the free-space floor."""
