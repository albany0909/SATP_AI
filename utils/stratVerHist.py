"""전략 문서의 변경 이력을 버전 그리드용으로 읽는다."""

import html
import re
from pathlib import Path

_VER_RE = re.compile(r"(?:\*\*)?v(\d+)(?:\*\*)?", re.I)
_SEP_RE = re.compile(r"^\|\s*-+")
_ROW_RE = re.compile(r"^\|\s*([^|]+)\s*\|\s*(.+?)\s*\|?\s*$")
_ROOT = Path(__file__).resolve().parents[1]
_DOCS_DIR = _ROOT / "strategies" / "docs"


def _mdPath(spec):
    """스펙 docPath 또는 strategies/docs/{stratId}.md."""
    rel = str((spec or {}).get("docPath") or "").strip().replace("\\", "/")
    if rel:
        path = _ROOT / rel
        if path.is_file():
            return path
    sid = str((spec or {}).get("stratId") or "").strip()
    if sid:
        path = _DOCS_DIR / f"{sid}.md"
        if path.is_file():
            return path
    return None


def _plainNote(raw):
    """마크다운 강조·엔티티를 그리드 문구로 푼다."""
    text = str(raw or "").strip()
    text = html.unescape(text)
    text = text.replace("**", "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return " ".join(text.split())


def parseDocVerHist(mdText):
    """
    '변경 이력' 표 → [{ymd, ver, note}].
    문서 순서(오래된 것부터)를 유지한다.
    """
    lines = str(mdText or "").splitlines()
    inHist = False
    rows = []
    for line in lines:
        stripped = line.strip()
        if (not inHist) and "변경 이력" in stripped and stripped.startswith("#"):
            inHist = True
            continue
        if inHist and stripped.startswith("#"):
            break
        if not inHist or not stripped.startswith("|"):
            continue
        if "일자" in stripped and "내용" in stripped:
            continue
        if _SEP_RE.match(stripped):
            continue
        matched = _ROW_RE.match(stripped)
        if not matched:
            continue
        ymd = matched.group(1).strip()
        note = _plainNote(matched.group(2))
        if not ymd or not note:
            continue
        verM = _VER_RE.search(note)
        ver = int(verM.group(1)) if verM else None
        rows.append({"ymd": ymd, "ver": ver, "note": note})
    return rows


def listAlgVerHist(algCd=None, preferDb=True):
    """
    알고리즘 변경 이력.
    기본은 DB(AI_ALG_VER_HIST) 우선, 비어 있으면 문서 표로 폴백.
    """
    from strategies.specLoader import listStratSpecs, loadStratSpec, normAlgCd

    want = normAlgCd(algCd) if algCd else ""
    specs = listStratSpecs() or []
    algs = []
    meta = {}
    for s in specs:
        cd = s.get("algCd") or ""
        if not cd:
            continue
        sid = s.get("stratId") or ""
        spec = loadStratSpec(sid) or {}
        try:
            curVer = int(spec.get("version") or 0) or None
        except (TypeError, ValueError):
            curVer = None
        name = spec.get("name") or s.get("name") or cd
        row = {
            "algCd": cd,
            "name": name,
            "stratId": sid,
            "curVer": curVer,
            "status": spec.get("status") or s.get("status") or "",
        }
        algs.append(row)
        meta[cd] = row

    items = []
    src = "doc"
    if preferDb:
        try:
            from utils.algVerHistStore import listAlgVerHistRows

            dbRows = listAlgVerHistRows(want or None)
            if dbRows:
                src = "db"
                for row in dbRows:
                    cd = row.get("algCd") or ""
                    if want and cd != want:
                        continue
                    m = meta.get(cd) or {}
                    ver = row.get("ver")
                    curVer = m.get("curVer")
                    items.append(
                        {
                            "algCd": cd,
                            "name": m.get("name") or cd,
                            "curVer": curVer,
                            "ymd": row.get("ymd") or "",
                            "ver": ver,
                            "note": row.get("note") or "",
                            "gitHash": row.get("gitHash") or "",
                            "src": row.get("src") or "",
                            "isCur": bool(curVer and ver == curVer),
                        }
                    )
        except Exception:
            items = []
            src = "doc"

    if not items:
        src = "doc"
        for s in specs:
            cd = s.get("algCd") or ""
            if not cd:
                continue
            if want and cd != want:
                continue
            sid = s.get("stratId") or ""
            spec = loadStratSpec(sid) or {}
            m = meta.get(cd) or {}
            curVer = m.get("curVer")
            path = _mdPath(spec) or _mdPath(s)
            hist = []
            if path is not None:
                try:
                    hist = parseDocVerHist(path.read_text(encoding="utf-8"))
                except Exception:
                    hist = []
            for row in reversed(hist):
                ver = row.get("ver")
                items.append(
                    {
                        "algCd": cd,
                        "name": m.get("name") or cd,
                        "curVer": curVer,
                        "ymd": row.get("ymd") or "",
                        "ver": ver,
                        "note": row.get("note") or "",
                        "gitHash": "",
                        "src": "doc",
                        "isCur": bool(curVer and ver == curVer),
                    }
                )

    return {
        "ok": True,
        "algCd": want,
        "algs": algs,
        "cnt": len(items),
        "items": items,
        "histSrc": src,
    }


def recordAlgVerBump(algCd, algVer, note, ymd=None, stratId=None, gitHash=None):
    """버전 bump 시 DB 이력 1건 기록 (문서와 병행)."""
    from datetime import datetime

    from utils.algVerHistStore import upsertAlgVerHist

    day = ymd or datetime.now().strftime("%Y-%m-%d")
    return upsertAlgVerHist(
        algCd,
        algVer,
        day,
        note,
        stratId=stratId,
        gitHash=gitHash,
        src="bump",
    )
