import hashlib


class ConflictError(Exception):
    """The current file no longer matches the last explicit read."""


def digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()
