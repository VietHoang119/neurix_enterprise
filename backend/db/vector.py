"""pgvector <-> Python list conversion for the pg8000 driver, which has no
native vector adapter: vectors are sent as their text literal and CAST in
SQL, and come back as that same text form."""

import json


def to_pgvector(vec: list[float] | None) -> str | None:
    if vec is None:
        return None
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def from_pgvector(value) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value]
    return [float(v) for v in json.loads(value)]
