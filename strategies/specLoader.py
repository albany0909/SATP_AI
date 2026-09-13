"""strategies/specs/*.json 로드"""

import json
from pathlib import Path

_SPECS_DIR = Path(__file__).resolve().parent / "specs"
_ALG_CD_LEN = 5


def normAlgCd(raw):
    """알고리즘코드 5자리 (영문3 + 버전2). 예: MTS01."""
    text = str(raw or "").strip().upper()
    if len(text) != _ALG_CD_LEN:
        return ""
    return text


def getAlgCd(stratId):
    """stratId → 고정 5자리 알고리즘코드. 스펙 algCd."""
    spec = loadStratSpec(stratId) or {}
    return normAlgCd(spec.get("algCd"))


def loadStratSpec(stratId):
    """
    stratId → spec dict.
    실패 시 None.
    """
    path = _SPECS_DIR / f"{stratId}.json"
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def listStratSpecs():
    """등록된 스펙 파일 목록 [{seq, stratId, name, status, path}, ...] — seq는 1부터."""
    rows = []
    if not _SPECS_DIR.is_dir():
        return rows
    for path in sorted(_SPECS_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        rows.append(
            {
                "stratId": data.get("stratId") or path.stem,
                "algCd": normAlgCd(data.get("algCd")),
                "name": data.get("name") or "",
                "status": data.get("status") or "",
                "path": str(path),
            }
        )
    for i, row in enumerate(rows, start=1):
        row["seq"] = i
    return rows


def resolveStratRef(raw):
    """
    전략 참조 → stratId.
    - 숫자: bt list 시퀀스(1..)
    - 5자리: 알고리즘코드 (MTS01)
    - 그 외: stratId 문자열
    반환: {ok, stratId, seq?, name?, algCd?, errMsg?}
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return {"ok": False, "errMsg": "전략ID/번호가 비어 있습니다."}

    specs = listStratSpecs()
    if text.isdigit():
        seq = int(text)
        for s in specs:
            if int(s.get("seq") or 0) == seq:
                return {
                    "ok": True,
                    "stratId": s["stratId"],
                    "algCd": s.get("algCd") or "",
                    "seq": seq,
                    "name": s.get("name") or "",
                }
        return {
            "ok": False,
            "errMsg": (
                f"전략 번호 {seq} 없음. bt list 로 확인하세요."
            ),
        }

    wantAlg = normAlgCd(text)
    if wantAlg:
        for s in specs:
            if s.get("algCd") == wantAlg:
                return {
                    "ok": True,
                    "stratId": s["stratId"],
                    "algCd": wantAlg,
                    "seq": s.get("seq"),
                    "name": s.get("name") or "",
                }

    # stratId 정확 일치
    for s in specs:
        if s["stratId"] == text:
            return {
                "ok": True,
                "stratId": s["stratId"],
                "algCd": s.get("algCd") or "",
                "seq": s.get("seq"),
                "name": s.get("name") or "",
            }

    # 파일로 로드 가능한지 (list에 없어도 json 있으면)
    if loadStratSpec(text) is not None:
        return {
            "ok": True,
            "stratId": text,
            "algCd": getAlgCd(text),
            "seq": None,
            "name": "",
        }

    return {
        "ok": False,
        "errMsg": f"알 수 없는 전략: {text}\nbt list 로 번호/코드를 확인하세요.",
    }


def resolveAlgRef(raw):
    """
    참조(코드/번호/구 파일명) → 알고리즘코드 기준 해석.
    스펙 파일명은 내부 로드용 stratId 로만 유지한다.
    """
    got = resolveStratRef(raw)
    if not got.get("ok"):
        return got
    sid = got.get("stratId") or ""
    alg = normAlgCd(got.get("algCd")) or getAlgCd(sid)
    got["algCd"] = alg
    if not alg:
        return {"ok": False, "errMsg": f"알고리즘코드 없음: {raw}"}
    return got


def stratIdByAlgCd(algCd):
    """알고리즘코드 → 스펙 파일명(stratId). 없으면 빈 문자열."""
    code = normAlgCd(algCd)
    if not code:
        return ""
    for row in listStratSpecs():
        if row.get("algCd") == code:
            return row.get("stratId") or ""
    return ""
