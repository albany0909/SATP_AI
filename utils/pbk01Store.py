"""PBK01 상태. jsonl 에만 붙인다. 기존 줄은 고치거나 지우지 않는다."""

from __future__ import annotations

import json
from pathlib import Path


def pbkPath(name):
    from utils.appPaths import configDataDir

    return configDataDir() / name


def appendRow(path, row):
    filePath = Path(path)
    filePath.parent.mkdir(parents=True, exist_ok=True)
    with filePath.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def readRows(path):
    filePath = Path(path)
    if not filePath.is_file():
        return []
    out = []
    try:
        text = filePath.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def latestByKind(path, kind):
    found = None
    for row in readRows(path):
        if row.get("kind") == kind:
            found = row
    return found
