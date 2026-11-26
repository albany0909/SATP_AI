"""로그에서 평문 토큰으로 보이는 줄 수만 센다.

값을 출력하지 않는다. 파일을 고치지 않는다.

  python tools/scanLogSecrets.py
  python tools/scanLogSecrets.py path/to/SATP_YYYYMMDD.log
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 이미 *** 또는 앞4자…(len=) 로 가린 값은 세지 않는다.
_SUSPECT = re.compile(
    r"(?:토큰|token|access_token|authorization)\s*[:=]\s*(?!\*{3})([A-Za-z0-9_\-\.~+/=]{8,})"
    r"|\bbearer\s+(?!\*{3})([A-Za-z0-9_\-\.~+/=]{8,})"
    r"|/bot\d{6,}(?::|%3[Aa]|%253[Aa])[A-Za-z0-9_-]{30,}",
    re.IGNORECASE,
)


def suspectLine(line):
    """한 줄이 평문 비밀처럼 보이면 True. 값은 반환하지 않는다."""
    return _SUSPECT.search(line or "") is not None


def scanPaths(paths):
    """파일별 의심 줄 수. 없거나 읽지 못하면 오류 수만 올린다."""
    rows = []
    for path in paths:
        file = Path(path)
        if not file.is_file():
            rows.append({"path": str(file), "lines": 0, "suspects": 0, "err": "없음"})
            continue
        suspects = 0
        lines = 0
        try:
            with file.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    lines += 1
                    if suspectLine(line):
                        suspects += 1
        except OSError:
            rows.append({"path": str(file), "lines": 0, "suspects": 0, "err": "읽기실패"})
            continue
        rows.append({"path": str(file), "lines": lines, "suspects": suspects, "err": ""})
    return rows


def defaultLogPaths():
    """저장소와 매매창 로그 폴더의 SATP_*.log. 없으면 빈 목록."""
    found = []
    bases = [ROOT / "config" / "data" / "logs"]
    env = __import__("os").environ.get("SATP_TRADE_APP_DIR", "").strip()
    if env:
        bases.append(Path(env) / "config" / "data" / "logs")
    for base in bases:
        if not base.is_dir():
            continue
        found.extend(sorted(base.glob("SATP_*.log")))
    return found


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [Path(item) for item in args] if args else defaultLogPaths()
    if not paths:
        print("검사 파일 0")
        print("의심 줄 0")
        return 0
    rows = scanPaths(paths)
    total = 0
    for row in rows:
        total += int(row["suspects"])
        err = f" {row['err']}" if row["err"] else ""
        print(f"{row['path']} 줄 {row['lines']} 의심 {row['suspects']}{err}")
    print(f"의심 줄 {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
