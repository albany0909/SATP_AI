"""
MMG01 실적 필터 — DART 다중회사 주요계정(fnlttMultiAcnt) 영업이익 YoY.

통과: 직전 공시 기준 영업이익 YoY ≥ +20% 또는 흑자전환.
공시 지연 가정(마감일): 1Q 5/15, 반기 8/14, 3Q 11/14, 사업보고서 익년 3/31.
캐시: config/data/mmgEarn.json (7일마다 갱신). API 키는 .env DART_API_KEY (출력하지 않음).
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path

from utils.termLog import termLog

_ROOT = Path(__file__).resolve().parents[1]
_CACHE = _ROOT / "config" / "data" / "mmgEarn.json"
_REFRESH_SEC = 7 * 86400
_LAG = {"11013": "0515", "11012": "0814", "11014": "1114", "11011": "0331"}
_mem = None


def _dartKey():
    key = os.environ.get("DART_API_KEY")
    if key:
        return key.strip()
    try:
        for line in (_ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("DART_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _num(raw):
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def availYmd(year, reprt):
    year = int(year)
    if reprt == "11011":
        return f"{year + 1}{_LAG[reprt]}"
    return f"{year}{_LAG[reprt]}"


def evalOk(th, fr):
    if th is None or fr is None:
        return False
    if fr > 0:
        return th >= 1.2 * fr
    return th > 0


def _load():
    global _mem
    if _mem is not None:
        return _mem
    try:
        _mem = json.loads(_CACHE.read_text(encoding="utf-8"))
    except Exception:
        _mem = {"at": 0, "rows": {}}
    return _mem


def refreshEarnings(stkCds, force=False, years=None):
    """DART 조회 → 캐시 저장. stkCds: 종목코드 목록. 반환 건수."""
    import requests
    import xml.etree.ElementTree as ET

    data = _load()
    if not force and time.time() - float(data.get("at") or 0) < _REFRESH_SEC:
        return len(data.get("rows") or {})
    key = _dartKey()
    if not key:
        termLog("[MMG01] DART 키 없음 — 실적 필터 갱신 생략")
        return 0
    z = zipfile.ZipFile(io.BytesIO(requests.get(
        "https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=60).content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    m = {}
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = e.findtext("corp_code")
    want = [m[c] for c in stkCds if c in m]
    nowY = datetime.now().year
    years = years or [nowY - 2, nowY - 1, nowY]
    rows = {}
    for y in years:
        for rc in ("11013", "11012", "11014", "11011"):
            for i in range(0, len(want), 100):
                try:
                    r = requests.get(
                        "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json",
                        params={"crtfc_key": key, "corp_code": ",".join(want[i:i + 100]),
                                "bsns_year": str(y), "reprt_code": rc},
                        timeout=30,
                    ).json()
                except Exception:
                    continue
                for x in r.get("list", []) or []:
                    if x.get("account_nm") != "영업이익":
                        continue
                    sc = x.get("stock_code")
                    k = f"{y}|{rc}"
                    prev = rows.setdefault(sc, {}).get(k)
                    if prev and prev.get("fs") == "CFS" and x.get("fs_div") != "CFS":
                        continue
                    rows[sc][k] = {"fs": x.get("fs_div"), "th": _num(x.get("thstrm_amount")),
                                   "fr": _num(x.get("frmtrm_amount"))}
                time.sleep(0.05)
    data = {"at": time.time(), "rows": rows}
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    global _mem
    _mem = data
    termLog(f"[MMG01] DART 실적 갱신 {len(rows)}종목")
    return len(rows)


def isEarnOk(stkCd, ymd=None, rows=None):
    """ymd 시점에 공개된 최신 보고서 기준 통과 여부."""
    ymd = str(ymd or datetime.now().strftime("%Y%m%d"))[:8]
    src = rows if rows is not None else (_load().get("rows") or {}).get(stkCd) or {}
    best = None
    for k, v in src.items():
        y, rc = k.split("|")
        av = availYmd(y, rc)
        if av <= ymd and (best is None or av > best[0]):
            best = (av, v)
    if not best:
        return False
    return evalOk(best[1].get("th"), best[1].get("fr"))
