"""
Open DART 법인코드 매핑 (종목코드 → corp_code).
corpCode.xml 은 최초 1회 내려받아 로컬 캐시.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from config.config import dart_api_key
from config.loadEnv import ensureEnvLoaded
from utils.termLog import termLog

ensureEnvLoaded()

_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
_CACHE_XML = _CACHE_DIR / "dartCorpCode.xml"
_memMap = None  # stock_code(6) -> {corpCode, corpNm}


def _downloadCorpXml():
    key = (dart_api_key or "").strip()
    if not key:
        return False, "DART_API_KEY 없음"
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"
    try:
        res = requests.get(url, timeout=120)
    except Exception as err:
        return False, str(err)
    if res.status_code != 200:
        return False, f"HTTP {res.status_code}"
    # zip
    try:
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = zf.namelist()
        name = names[0] if names else "CORPCODE.xml"
        data = zf.read(name)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_XML.write_bytes(data)
        return True, ""
    except Exception as err:
        # plain xml?
        if b"<result>" in res.content[:200] or b"<list>" in res.content[:500]:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_XML.write_bytes(res.content)
            return True, ""
        return False, f"압축 해제 실패: {err}"


def _loadMapFromXml():
    global _memMap
    if not _CACHE_XML.is_file():
        ok, err = _downloadCorpXml()
        if not ok:
            termLog(f"[dartCorpCode] 다운로드 실패: {err}")
            _memMap = {}
            return _memMap
    try:
        root = ET.parse(str(_CACHE_XML)).getroot()
    except Exception as err:
        termLog(f"[dartCorpCode] 파싱 실패: {err}")
        _memMap = {}
        return _memMap
    m = {}
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        nm = (el.findtext("corp_name") or "").strip()
        if stock and corp and len(stock) == 6 and stock.isdigit():
            m[stock] = {"corpCode": corp, "corpNm": nm}
    _memMap = m
    termLog(f"[dartCorpCode] 상장 매핑 {len(m)}건")
    return _memMap


def resolveCorpCode(stkCd):
    """
    종목코드 → {ok, corpCode, corpNm, errMsg?}
    """
    code = str(stkCd or "").strip()
    if code.isdigit() and len(code) <= 6:
        code = code.zfill(6)
    global _memMap
    if _memMap is None:
        _loadMapFromXml()
    hit = (_memMap or {}).get(code)
    if not hit:
        # 캐시 재다운로드 1회
        ok, err = _downloadCorpXml()
        if ok:
            _memMap = None
            _loadMapFromXml()
            hit = (_memMap or {}).get(code)
        if not hit:
            return {
                "ok": False,
                "errMsg": err if not hit else "종목 법인코드 없음",
                "corpCode": "",
                "corpNm": "",
            }
    return {
        "ok": True,
        "corpCode": hit["corpCode"],
        "corpNm": hit.get("corpNm") or "",
        "stkCd": code,
    }
