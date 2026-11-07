"""
매매창 앱 버전 bump만 (알고리즘 버전과 별개).
배포(버전+zip+FTP)는 scripts/deployTradeApp.py 사용.

  python scripts/bumpAppVersion.py          # minor +1
  python scripts/bumpAppVersion.py --major  # major +1 (관리자)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.appVersion import bumpMajor, bumpMinor, formatAppVersion


def main():
    ap = argparse.ArgumentParser(description="SATP_AI 매매창 앱 버전 bump")
    ap.add_argument("--major", action="store_true", help="메이저 상향 (minor=0)")
    args = ap.parse_args()
    if args.major:
        label = bumpMajor()
        print(f"major bump → {label}")
    else:
        label = bumpMinor()
        print(f"minor bump → {label}")
    print(f"current {formatAppVersion()}")


if __name__ == "__main__":
    main()
