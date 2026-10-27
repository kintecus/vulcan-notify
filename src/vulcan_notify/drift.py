"""Detect upstream API drift from the structure of eduVulcan responses.

The client parses with `.get(key, default)` throughout, so a renamed field does not
raise: it quietly becomes an empty string or an empty list, and the section still
reports healthy. This module watches the response *structure* instead -- key paths and
value types, never values, since the payloads are minors' grades and teachers' names.

Each endpoint's structure is learned silently the first time it is seen. After that,
four things raise a drift event:

- `added`: a new key under an object whose children were already known
- `missing`: a known key absent while its parent object is present (Vulcan serializes
  nulls rather than omitting keys, so absence means the key is gone)
- `type_changed`: a non-null type never seen before on a path that had one
- `now_null`: a path that has never been null comes back null everywhere
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# path -> type names seen. Paths look like `$.ocenyPrzedmioty[].kolumnyOcenyCzastkowe[]`.
Shape = dict[str, set[str]]

# Object keys that are data (ids, dates, UUIDs) rather than schema. Without this every
# new id in a dictionary-shaped object would read as an added field.
_DATA_KEY = re.compile(r"[\d\-:T.+Z]+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}")


def endpoint_name(url: str) -> str:
    """`https://uczen.eduvulcan.pl/t/api/Oceny?key=..` -> `uczen:Oceny`."""
    parts = urlsplit(url)
    host = parts.hostname.split(".", 1)[0] if parts.hostname else ""
    name = parts.path.rsplit("/api/", 1)[-1].strip("/")
    return f"{host}:{name}"


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        # Weights come back as 1 and 1.5 in the same column; JSON has one number type.
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def extract(value: Any, path: str = "$", out: Shape | None = None) -> Shape:
    """Walk a JSON value and record every key path with the types seen there."""
    if out is None:
        out = {}
    if isinstance(value, dict):
        out.setdefault(path, set()).add("object")
        for key, child in value.items():
            segment = "*" if _DATA_KEY.fullmatch(str(key)) else key
            extract(child, f"{path}.{segment}", out)
    elif isinstance(value, list):
        out.setdefault(path, set()).add("array")
        for item in value:
            extract(item, f"{path}[]", out)
    else:
        out.setdefault(path, set()).add(_type_name(value))
    return out


def merge(into: Shape, other: Shape) -> None:
    for path, types in other.items():
        into.setdefault(path, set()).update(types)


def parent(path: str) -> str:
    if path.endswith("[]"):
        return path[:-2]
    head, _, _ = path.rpartition(".")
    return head


@dataclass(frozen=True)
class Drift:
    endpoint: str
    kind: str  # added | missing | type_changed | now_null | empty
    path: str
    detail: str

    def describe(self) -> str:
        where = f"{self.endpoint} {self.path}".strip()
        return f"{where}: {self.kind.replace('_', ' ')} ({self.detail})"


@dataclass
class KnownPath:
    types: set[str]
    missing: bool = False


@dataclass
class Reconciled:
    drifts: list[Drift] = field(default_factory=list)
    known: dict[str, KnownPath] = field(default_factory=dict)
    changed: bool = False


def reconcile(endpoint: str, known: dict[str, KnownPath], seen: Shape) -> Reconciled:
    """Compare one endpoint's observed shape against what was learned before.

    Returns the drifts to report and the updated knowledge. A drift is reported once:
    a missing key is flagged and remembered as missing until it comes back, and new
    paths and types are learned as soon as they are reported.
    """
    if not known:
        return Reconciled(known={p: KnownPath(set(t)) for p, t in seen.items()}, changed=bool(seen))

    result = Reconciled(known={p: KnownPath(set(k.types), k.missing) for p, k in known.items()})
    # Parents whose children we have already learned. New children under a parent that
    # was only ever null or an empty array are first sightings, not drift.
    expanded = {parent(p) for p in known}

    for path, types in seen.items():
        entry = result.known.get(path)
        if entry is None:
            if parent(path) in expanded:
                result.drifts.append(Drift(endpoint, "added", path, "|".join(sorted(types))))
            result.known[path] = KnownPath(set(types))
            result.changed = True
            continue

        old_real = entry.types - {"null"}
        new_real = types - {"null"}
        if old_real and new_real - old_real:
            result.drifts.append(
                Drift(
                    endpoint,
                    "type_changed",
                    path,
                    f"{'|'.join(sorted(old_real))} -> {'|'.join(sorted(new_real))}",
                )
            )
        elif old_real and "null" not in entry.types and types == {"null"}:
            result.drifts.append(Drift(endpoint, "now_null", path, "was never null before"))

        if not types <= entry.types or entry.missing:
            entry.types |= types
            entry.missing = False
            result.changed = True

    for path, entry in result.known.items():
        if path in seen or entry.missing or path.endswith("[]"):
            continue
        # Only an object key can be "missing"; an array with no elements is just empty.
        if "object" in seen.get(parent(path), set()):
            result.drifts.append(Drift(endpoint, "missing", path, "key no longer returned"))
            entry.missing = True
            result.changed = True

    return result


def summarize(drifts: list[Drift], limit: int = 3) -> str:
    """One-line human summary for a push notification."""
    lines = [d.describe() for d in drifts[:limit]]
    if len(drifts) > limit:
        lines.append(f"+{len(drifts) - limit} more")
    return "\n".join(lines)


_DATE_HINT = re.compile(r"^(data|dzien|godzina|poczatek|koniec)|(Od|Do)$")


def synthesize(shape: Shape, path: str = "$") -> Any:
    """Build a sample JSON value from a learned shape, for parser tests.

    Every key the real API returns is present, with a non-empty placeholder of the
    observed type. A parser that reads a key the API does not return gets its default
    instead, which is what the fixture tests look for.
    """
    types = shape.get(path, {"null"})
    if "object" in types:
        prefix = f"{path}."
        children = {
            p[len(prefix) :].split(".")[0].split("[]")[0] for p in shape if p.startswith(prefix)
        }
        return {key: synthesize(shape, f"{prefix}{key}") for key in sorted(children)}
    if "array" in types:
        return [synthesize(shape, f"{path}[]")] if f"{path}[]" in shape else []
    key = path.rsplit(".", 1)[-1].removesuffix("[]")
    if "string" in types:
        return "2026-01-05T08:00:00+01:00" if _DATE_HINT.search(key) else f"<{key}>"
    if "number" in types:
        return 1
    if "bool" in types:
        return True
    return None
