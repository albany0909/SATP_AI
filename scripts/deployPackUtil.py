"""
배포 패키지 공통: 제외 목록 + 핵심 모듈 .pyc 난독화 스테이징.

deployTradeApp / buildTradeSetup 가 동일 규칙을 쓴다.
클라이언트 runtime 은 Python 3.12 — .pyc 도 3.12 로 컴파일해야 한다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# zip·Setup tradeApp 에 넣지 않을 디렉터리
SKIP_DIR_NAMES = {
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".cursor",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "tradeApp",
    "node_modules",
    "key",
    "agent-transcripts",
    ".cache",
    # 슬림화 (매매창 불필요)
    "docs",
    "examples",
    "research",
    "financeAgent",
    "web",
    "tr_docs",
    # sql 은 포함 — deskLog/algEvt 등 테이블 ensure 에 필요
    "scripts",
}

SKIP_FILE_NAMES = {
    ".env",
    "loginLocal.json",
    "deskLoginSession.json",
    "_update_download.zip",
    ".DS_Store",
    "Thumbs.db",
    "loginMain.py",
    "project_structure_guide.md",
    "pyrightconfig.json",
    "CLAUDE.md",
}

SKIP_SUFFIXES = {".pyo", ".pyd", ".log", ".zip", ".md"}

# .py 제거 후 .pyc 만 배포
OBFUSCATE_PREFIXES = (
    "trading",
    "strategies/impl",
)


def shouldSkipRel(relPosix: str, *, allowPyc=False, skipRuntime=True) -> bool:
    parts = [p for p in relPosix.replace("\\", "/").split("/") if p]
    if not parts:
        return True
    for p in parts[:-1]:
        if p in SKIP_DIR_NAMES or p.endswith(".egg-info"):
            return True
    name = parts[-1]
    if name in SKIP_FILE_NAMES or name in SKIP_DIR_NAMES:
        return True
    if name.startswith(".env"):
        return True
    if name.startswith("_tmp"):
        return True
    low = name.lower()
    for suf in SKIP_SUFFIXES:
        if low.endswith(suf):
            return True
    if low.endswith(".pyc") and not allowPyc:
        return True
    if skipRuntime and parts[0] == "runtime":
        return True
    return False


def resolveCompilePython(srcRoot: Path) -> Path:
    """
    클라이언트 embed(3.12) python.exe.
    없으면 호스트 python (버전 불일치 경고).
    """
    cands = [
        srcRoot / "dist" / "tradeFull" / "runtime" / "python.exe",
        srcRoot / "dist" / "payloadRoot" / "tradeApp" / "runtime" / "python.exe",
        Path(r"C:\SATP_AI\tradeApp\runtime\python.exe"),
    ]
    for c in cands:
        if c.is_file():
            return c
    print(
        "WARNING: embed 3.12 runtime 없음 — 호스트 python 으로 컴파일 "
        f"({sys.executable}). 클라이언트에서 import 실패할 수 있음."
    )
    return Path(sys.executable)


def stageAppTree(srcRoot: Path, destRoot: Path, compilePy: Path | None = None) -> int:
    """
    소스를 dest 에 복사한 뒤 trading·strategies/impl 은
    형제 .pyc(모듈.pyc)만 남긴다. (__pycache__ 방식은 소스 없이 import 실패)
    """
    if destRoot.exists():
        shutil.rmtree(destRoot, ignore_errors=True)
    destRoot.mkdir(parents=True, exist_ok=True)

    count = 0
    for path in sorted(srcRoot.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(srcRoot).as_posix()
        if shouldSkipRel(rel, allowPyc=False, skipRuntime=True):
            continue
        target = destRoot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1

    pyExe = Path(compilePy or resolveCompilePython(srcRoot))
    print(f"obfuscate compile with: {pyExe}")

    # embed python 으로 형제 .pyc 생성 후 .py 삭제 (__init__.py 유지)
    obfuscatePy = (
        "import py_compile, pathlib, shutil, sys\n"
        "root = pathlib.Path(sys.argv[1])\n"
        "removed = kept = 0\n"
        "for pyPath in sorted(root.rglob('*.py')):\n"
        "    if pyPath.name == '__init__.py':\n"
        "        kept += 1\n"
        "        continue\n"
        "    cfile = pyPath.with_suffix('.pyc')\n"
        "    py_compile.compile(str(pyPath), cfile=str(cfile), doraise=True,\n"
        "        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)\n"
        "    pyPath.unlink()\n"
        "    removed += 1\n"
        "for cache in list(root.rglob('__pycache__')):\n"
        "    shutil.rmtree(cache, ignore_errors=True)\n"
        "n = len(list(root.rglob('*.pyc')))\n"
        "print('py_removed=%s init_kept=%s sibling_pyc=%s' % (removed, kept, n))\n"
    )

    for prefix in OBFUSCATE_PREFIXES:
        folder = destRoot / Path(prefix)
        if not folder.is_dir():
            continue
        print(f"obfuscate {prefix} ...")
        subprocess.check_call(
            [str(pyExe), "-c", obfuscatePy, str(folder)],
            cwd=str(srcRoot),
        )

    return count + len(list(destRoot.rglob("*.pyc")))


def iterStagedFiles(stageRoot: Path):
    for path in sorted(stageRoot.rglob("*")):
        if path.is_file():
            yield path, path.relative_to(stageRoot).as_posix()
