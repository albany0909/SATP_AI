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


def listAlgVerHist(algCd=None):
    """
    알고리즘 변경 이력.
    algCd 가 있으면 해당 코드만, 없으면 스펙에 있는 전 알고리즘.
    """
    from strategies.specLoader import listStratSpecs, loadStratSpec, normAlgCd

    want = normAlgCd(algCd) if algCd else ""
    specs = listStratSpecs() or []
    algs = []
    items = []
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
        algs.append(
            {
                "algCd": cd,
                "name": name,
                "stratId": sid,
                "curVer": curVer,
                "status": spec.get("status") or s.get("status") or "",
            }
        )
        if want and cd != want:
            continue
        path = _mdPath(spec) or _mdPath(s)
        hist = []
        if path is not None:
            try:
                hist = parseDocVerHist(path.read_text(encoding="utf-8"))
            except Exception:
                hist = []
        # 최신이 위로. 같은 날이면 문서 역순(뒤에 나온 버전이 위)
        for row in reversed(hist):
            ver = row.get("ver")
            items.append(
                {
                    "algCd": cd,
                    "name": name,
                    "curVer": curVer,
                    "ymd": row.get("ymd") or "",
                    "ver": ver,
                    "note": row.get("note") or "",
                    "isCur": bool(curVer and ver == curVer),
                }
            )
    return {
        "ok": True,
        "algCd": want,
        "algs": algs,
        "cnt": len(items),
        "items": items,
    }
