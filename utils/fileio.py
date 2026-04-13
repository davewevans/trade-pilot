"""File I/O utilities."""

import json
import os
from pathlib import Path


def atomic_json_write(path: Path, data, indent: int = 2) -> None:
    """Write JSON data atomically: write to .tmp, then rename.

    Prevents corruption from crashes mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=indent, default=str), encoding="utf-8")
    os.replace(str(tmp), str(path))
