"""Small, dependency-free file/provenance helpers."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import platform
import statistics
import sys
from pathlib import Path
from typing import Any

class DataError(ValueError):
    """Input evidence is missing, ambiguous, or inconsistent."""

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))

def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

def number(value: Any, name: str) -> float:
    if value is None or str(value).strip() == "" or isinstance(value, bool):
        raise DataError(f"{name}: missing numeric value")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{name}: not numeric") from exc
    if not math.isfinite(result):
        raise DataError(f"{name}: non-finite value")
    return result

def integer(value: Any, name: str, minimum: int = 0) -> int:
    result = number(value, name)
    if not result.is_integer() or result < minimum:
        raise DataError(f"{name}: expected integer >= {minimum}")
    return int(result)

def stats(values: list[float]) -> dict[str, Any]:
    return {"n": len(values), "mean": statistics.mean(values) if values else None,
            "sd": statistics.stdev(values) if len(values) > 1 else None,
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}

def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        names = list(reader.fieldnames or [])
        if not names or len(names) != len(set(names)):
            raise DataError("CSV has no header or duplicate column names")
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise DataError("CSV row length disagrees with its header")
        return names, rows

def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

def new_output(path: Path, inputs: list[Path] = ()) -> Path:
    path = path.resolve()
    # Never create analysis inside any source evidence directory supplied as input.
    for source in inputs:
        root = source.resolve()
        if path == root or path in root.parents or (source.is_dir() and root in path.parents):
            raise DataError(f"Output must not replace or be inside source directory: {root}")
    if path.exists():
        raise DataError(f"Refusing to overwrite existing output: {path}")
    path.mkdir(parents=True, exist_ok=False)
    return path

def manifest(directory: Path) -> None:
    paths = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
    text = "".join(f"{sha256(p)}  {p.relative_to(directory).as_posix()}\n" for p in paths)
    (directory / "SHA256SUMS.txt").write_text(text, encoding="ascii")

def runtime() -> dict:
    return {"python": sys.version, "platform": platform.platform(),
            "tool_version": "0.1.0", "source_sha256": {
                p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))}}
