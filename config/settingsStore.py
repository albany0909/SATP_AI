"""
동적 설정(settings.json) 로드·저장.
디폴트는 settings-default.json — 설정 파일이 없거나 깨지면 디폴트로 복구한다.
"""

import json
import re
from pathlib import Path

# config/data — 이 모듈 기준 상대 경로
_DATA_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_PATH = _DATA_DIR / "settings-default.json"
_SETTINGS_PATH = _DATA_DIR / "settings.json"

# 코드에 내장한 최후 폴백 (디폴트 JSON도 없을 때)
_FALLBACK = {
    "mktOpenHm": "09:00",
    "mktCloseHm": "15:30",
    # 체결알림 구간 — 장전·NXT 포함 (정규 mkhr와 분리)
    "notifyOpenHm": "08:00",
    "notifyCloseHm": "20:00",
    "tprPct": 5,
    "slrPct": -10,
    "maTouchLive": {
        "enabled": True,
        "paperOnly": True,
        "buyAmt5": 1000000,
        "buyAmt10": 1000000,
        "buyAmt15": 1000000,
    },
    "deskTradeExch": "KRX",
    # 매매창·자동매매 세션. paper | real
    "deskTradeMode": "paper",
    # 매매창 전략 탭·하단 운영알고리즘 표시 순서
    "deskAlgTabs": ["MTS01", "MSE01"],
    "stratLive": {
        "MTS01": {
            "buyAmt5": 1000000,
            "buyAmt10": 1000000,
            "buyAmt15": 1000000,
        },
        "MSE01": {
            "enabled": True,
            "buyAmtWon": 2000000,
        },
    },
    "batchSched": {
        "usMorningLoad": True,
        "watchNewsDisc": True,
        "dayInqRank": True,
        "ntlFirst": True,
        "dayInvSr": True,
    },
    "telegramPush": {
        "master": True,
        "boot": True,
        "batch": True,
        "wnews": True,
        "ordExec": False,
        "closePnl": True,
        "cnsr": True,
        "rsv": True,
        "stls": True,
        "tokenRenew": True,
        "btVal": True,
    },
}


def parseHm(timeStr):
    """
    24시간제 시각 문자열을 HH:MM으로 정규화한다.
    예: '9:30', '09:30', '9:0' → '09:30'
    잘못된 형식이면 None.
    """
    raw = (timeStr or "").strip()
    matched = re.fullmatch(r"(\d{1,2}):(\d{1,2})", raw)
    if not matched:
        return None
    hour = int(matched.group(1))
    minute = int(matched.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def parsePctInput(raw):
    """
    퍼센트 입력 문자열을 float로 변환한다.
    '5', '+5', '-5', '5%', '5.5' 허용. 실패 시 None.
    0은 익절/손절 기준으로 무의미하므로 거부.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace("%", "").replace(",", "")
    if text == "":
        return None
    try:
        val = float(text)
    except ValueError:
        return None
    if val == 0:
        return None
    return val


def _toStoredPct(val):
    """정수면 int, 아니면 float로 JSON에 깔끔히 저장."""
    if val is None:
        return None
    if float(val).is_integer():
        return int(val)
    return float(val)


def _readJson(path):
    """JSON 파일 로드. 실패 시 None."""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _writeJson(path, data):
    """JSON 파일 저장. 디렉터리가 없으면 생성."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def loadDefaultSettings():
    """디폴트 설정 dict. 파일 없으면 내장 폴백."""
    data = _readJson(_DEFAULT_PATH)
    if data is None:
        return dict(_FALLBACK)
    # 필수 키가 없으면 폴백으로 채움
    merged = dict(_FALLBACK)
    merged.update(data)
    return merged


def loadSettings():
    """
    현재 설정 dict.
    settings.json이 없거나 깨지면 디폴트로 생성 후 반환한다.
    """
    data = _readJson(_SETTINGS_PATH)
    if data is None:
        defaults = loadDefaultSettings()
        _writeJson(_SETTINGS_PATH, defaults)
        return dict(defaults)

    defaults = loadDefaultSettings()
    # 누락 키는 디폴트로 보완 (구버전 settings.json 호환)
    merged = dict(defaults)
    merged.update(data)
    # 중첩 dict는 키 단위로 합침 (배치/푸시 on·off)
    for nestKey in ("batchSched", "telegramPush", "maTouchLive"):
        base = dict(defaults.get(nestKey) or {})
        extra = data.get(nestKey)
        if isinstance(extra, dict):
            base.update(extra)
        merged[nestKey] = base
    # 전략별 금액은 알고리즘코드(ALG_CD) 단위로 한 겹 더 합친다
    liveBase = dict(defaults.get("stratLive") or {})
    liveExtra = data.get("stratLive")
    if isinstance(liveExtra, dict):
        for key, cfg in liveExtra.items():
            child = dict(liveBase.get(key) or {})
            if isinstance(cfg, dict):
                child.update(cfg)
            liveBase[str(key)] = child
    # 구 파일명 키(maTouchScale_v1) → 알고리즘코드로 복사 (관리는 코드만)
    try:
        from strategies.specLoader import listStratSpecs

        for spec in listStratSpecs():
            oldSid = spec.get("stratId") or ""
            alg = spec.get("algCd") or ""
            if oldSid and alg and oldSid in liveBase and alg not in liveBase:
                liveBase[alg] = dict(liveBase[oldSid])
    except Exception:
        pass
    merged["stratLive"] = liveBase
    return merged


def saveSettings(data):
    """현재 설정을 settings.json에 저장한다."""
    _writeJson(_SETTINGS_PATH, data)


def resetSettingsToDefault():
    """
    settings-default.json 내용으로 settings.json을 덮어쓴다.
    반환: 복원된 설정 dict
    """
    defaults = loadDefaultSettings()
    saveSettings(defaults)
    return dict(defaults)


def getMktHours():
    """
    장 시작·종료 시각(HH:MM) 반환.
    반환: (mktOpenHm, mktCloseHm)
    """
    settings = loadSettings()
    openHm = parseHm(settings.get("mktOpenHm")) or _FALLBACK["mktOpenHm"]
    closeHm = parseHm(settings.get("mktCloseHm")) or _FALLBACK["mktCloseHm"]
    return openHm, closeHm


def setMktHours(openHm, closeHm):
    """
    장 시작·종료 시각을 설정하고 settings.json에 저장한다.
    openHm/closeHm은 이미 HH:MM 정규화된 값이어야 한다.
    반환: 저장된 (openHm, closeHm)
    """
    settings = loadSettings()
    settings["mktOpenHm"] = openHm
    settings["mktCloseHm"] = closeHm
    saveSettings(settings)
    return openHm, closeHm


def isWithinMktHours(hm=None):
    """
    주어진 HH:MM(또는 현재 시각)이 장 시간 구간에 포함되는지.
    시작 이상 · 종료 이하 (양끝 포함).
    """
    from datetime import datetime

    openHm, closeHm = getMktHours()
    cur = hm if hm is not None else datetime.now().strftime("%H:%M")
    # 문자열 비교는 HH:MM 정규화 전제에서 안전
    return openHm <= cur <= closeHm


def getNotifyHours():
    """
    체결알림 시작·종료 시각(HH:MM).
    반환: (notifyOpenHm, notifyCloseHm)
    """
    settings = loadSettings()
    openHm = parseHm(settings.get("notifyOpenHm")) or _FALLBACK["notifyOpenHm"]
    closeHm = parseHm(settings.get("notifyCloseHm")) or _FALLBACK["notifyCloseHm"]
    return openHm, closeHm


def setNotifyHours(openHm, closeHm):
    """체결알림 구간 저장. 값은 HH:MM 정규화본."""
    settings = loadSettings()
    settings["notifyOpenHm"] = openHm
    settings["notifyCloseHm"] = closeHm
    saveSettings(settings)
    return openHm, closeHm


def isWithinNotifyHours(hm=None):
    """
    체결알림 가능 시각인지 (시간만, 주말·공휴일 제외는 호출측에서).
    시작 이상 · 종료 이하.
    """
    from datetime import datetime

    openHm, closeHm = getNotifyHours()
    cur = hm if hm is not None else datetime.now().strftime("%H:%M")
    return openHm <= cur <= closeHm


def isNotifySessionNow(dt=None):
    """
    체결알림 가동 여부.
    거래일(월~금, 공휴일 제외) + notifyOpen~notifyClose.
    """
    from datetime import datetime

    from config.krHoliday import isTradingDay

    day = dt or datetime.now()
    if not isTradingDay(day):
        return False
    return isWithinNotifyHours(day.strftime("%H:%M"))


def getTprPct():
    """익절 기준(%) — 항상 양수."""
    settings = loadSettings()
    try:
        val = abs(float(settings.get("tprPct", _FALLBACK["tprPct"])))
    except (TypeError, ValueError):
        val = float(_FALLBACK["tprPct"])
    return _toStoredPct(val)


def getSlrPct():
    """손절 기준(%) — 항상 음수."""
    settings = loadSettings()
    try:
        val = -abs(float(settings.get("slrPct", _FALLBACK["slrPct"])))
    except (TypeError, ValueError):
        val = float(_FALLBACK["slrPct"])
    return _toStoredPct(val)


def setTprPct(rawVal):
    """
    익절 기준 설정. 입력 부호와 무관하게 양수로 저장.
    성공: (True, 저장된값), 실패: (False, None)
    """
    parsed = parsePctInput(rawVal)
    if parsed is None:
        return False, None
    stored = _toStoredPct(abs(parsed))
    settings = loadSettings()
    settings["tprPct"] = stored
    saveSettings(settings)
    return True, stored


def getMaTouchLive():
    """이평선터치 모의 실시간 설정. 금액은 전략별 stratLive 를 우선한다."""
    settings = loadSettings()
    raw = settings.get("maTouchLive") or {}
    fb = _FALLBACK.get("maTouchLive") or {}
    if not isinstance(raw, dict):
        raw = {}
    amts = getStratLiveAmts("MTS01")
    steps = [20]
    try:
        from strategies.specLoader import loadStratSpec

        spec = loadStratSpec("maTouchScale_v1") or {}
        rawSteps = (spec.get("params") or {}).get("maSteps") or steps
        parsed = []
        for x in rawSteps:
            parsed.append(int(x))
        if parsed:
            steps = parsed
    except Exception:
        pass
    seq = [amts["buyAmt5"], amts["buyAmt10"], amts["buyAmt15"]]
    byStep = {}
    for i, step in enumerate(steps):
        byStep[int(step)] = seq[i] if i < len(seq) else 1000000
    halfPct = None
    if raw.get("halfTpPct") is not None:
        try:
            halfPct = float(raw.get("halfTpPct"))
        except (TypeError, ValueError):
            halfPct = None
    return {
        "enabled": bool(raw.get("enabled", fb.get("enabled", True))),
        "paperOnly": bool(raw.get("paperOnly", fb.get("paperOnly", True))),
        "buyAmt5": amts["buyAmt5"],
        "buyAmt10": amts["buyAmt10"],
        "buyAmt15": amts["buyAmt15"],
        "buyAmtByStep": byStep,
        "halfTpPct": halfPct,
        "halfTpFromStat": bool(raw.get("halfTpFromStat", False)),
        "halfTpStatYmd": str(raw.get("halfTpStatYmd") or ""),
        "halfTpStatFromYmd": str(raw.get("halfTpStatFromYmd") or ""),
        "halfTpStatN": int(raw.get("halfTpStatN") or 0),
    }


def setMaTouchHalfTpFromStat(pct, tradeDt=None, sampleN=0, fromYmd=None):
    """장마감 MFE 권고를 실매매 1차에 저장. spec 파일은 수정하지 않는다."""
    try:
        val = float(pct)
    except (TypeError, ValueError):
        return None
    settings = loadSettings()
    raw = settings.get("maTouchLive") or {}
    if not isinstance(raw, dict):
        raw = {}
    raw["halfTpPct"] = val
    raw["halfTpFromStat"] = True
    raw["halfTpStatYmd"] = str(tradeDt or "")[:8]
    raw["halfTpStatFromYmd"] = str(fromYmd or "")[:8]
    raw["halfTpStatN"] = int(sampleN or 0)
    settings["maTouchLive"] = raw
    saveSettings(settings)
    return val


def getDeskTradeMode():
    """매매 모드. paper(모의) | real(실전). 기본 paper."""
    settings = loadSettings()
    raw = str(settings.get("deskTradeMode") or "paper").strip().lower()
    if raw in ("real", "live", "실전"):
        return "real"
    if raw not in ("paper", "mock", "모의"):
        settings["deskTradeMode"] = "paper"
        saveSettings(settings)
    return "paper"


def setDeskTradeMode(mode):
    """매매 모드 저장. paper | real."""
    raw = str(mode or "").strip().lower()
    if raw in ("real", "live", "실전"):
        val = "real"
    else:
        val = "paper"
    settings = loadSettings()
    settings["deskTradeMode"] = val
    saveSettings(settings)
    return val


def getDeskTradeExch():
    """매매창 거래소. KRX | NXT. 기본·예전 off 는 KRX."""
    settings = loadSettings()
    raw = str(settings.get("deskTradeExch") or "KRX").strip().upper()
    if raw == "NXT":
        return "NXT"
    if raw != "KRX":
        settings["deskTradeExch"] = "KRX"
        saveSettings(settings)
    return "KRX"


def setDeskTradeExch(exch):
    """매매 거래소를 저장한다. 반환: 저장된 값."""
    val = "NXT" if str(exch or "").strip().upper() == "NXT" else "KRX"
    settings = loadSettings()
    settings["deskTradeExch"] = val
    saveSettings(settings)
    return val


def _manToWon(manRaw):
    try:
        man = int(float(str(manRaw).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    if man < 1 or man > 10000:
        return None
    return man * 10000


def _wonToMan(won):
    try:
        return max(0, int(won) // 10000)
    except (TypeError, ValueError):
        return 0


def _resolveLiveRef(ref):
    """금액 저장 키 = 알고리즘코드. 구 파일명도 받는다."""
    text = str(ref or "").strip()
    try:
        from strategies.specLoader import resolveAlgRef

        got = resolveAlgRef(text or "MTS01")
        if got.get("ok"):
            return got.get("stratId") or "", got.get("algCd") or ""
    except Exception:
        pass
    if text in ("maTouchScale_v1", "MTS01"):
        return "maTouchScale_v1", "MTS01"
    if text in ("maSupportEod_v1", "MSE01"):
        return "maSupportEod_v1", "MSE01"
    return text, text


def getStratLiveAmts(stratId):
    """
    알고리즘코드별 매수 금액.
    amtKind: byStep(1/2/3차 이평) | won(단일금액) | none
    """
    sid, alg = _resolveLiveRef(stratId)
    spec = {}
    try:
        from strategies.specLoader import loadStratSpec

        spec = loadStratSpec(sid) or {}
    except Exception:
        spec = {}
    params = spec.get("params") or {}
    settings = loadSettings()
    storedAll = settings.get("stratLive") or {}
    stored = {}
    if isinstance(storedAll, dict):
        # 코드 키 우선, 구 파일명 키는 하위호환
        stored = storedAll.get(alg) or storedAll.get(sid) or {}
    if not isinstance(stored, dict):
        stored = {}
    # 구버전 maTouchLive 금액 호환
    if alg == "MTS01" and not stored:
        mt = settings.get("maTouchLive") or {}
        if isinstance(mt, dict):
            stored = {
                "buyAmt5": mt.get("buyAmt5"),
                "buyAmt10": mt.get("buyAmt10"),
                "buyAmt15": mt.get("buyAmt15"),
            }

    specBy = params.get("buyAmtByStep") if isinstance(params.get("buyAmtByStep"), dict) else {}
    hasStep = bool(specBy) or "buyAmt5" in params or alg == "MTS01" or sid.startswith(
        "maTouchScale"
    )
    hasWon = params.get("buyAmtWon") is not None

    def _amt(key, default):
        for src in (stored, specBy, params):
            if not isinstance(src, dict):
                continue
            raw = src.get(key)
            if raw is None and key.isdigit():
                raw = src.get(int(key)) if not isinstance(src.get(int(key)), dict) else None
            try:
                if raw is not None:
                    return max(10000, int(raw))
            except (TypeError, ValueError):
                continue
        return int(default)

    amt5 = _amt("buyAmt5", _amt("5", 1000000))
    amt10 = _amt("buyAmt10", _amt("10", 1000000))
    amt15 = _amt("buyAmt15", _amt("15", 1000000))
    amtWon = _amt("buyAmtWon", 2000000)
    if hasStep:
        kind = "byStep"
    elif hasWon:
        kind = "won"
    else:
        kind = "none"
    return {
        "amtKind": kind,
        "buyAmt5": amt5,
        "buyAmt10": amt10,
        "buyAmt15": amt15,
        "buyAmtWon": amtWon,
        "buyAmt5Man": _wonToMan(amt5),
        "buyAmt10Man": _wonToMan(amt10),
        "buyAmt15Man": _wonToMan(amt15),
        "buyAmtWonMan": _wonToMan(amtWon),
    }


def setStratLiveAmts(stratId, amt5Man=None, amt10Man=None, amt15Man=None, amtWonMan=None):
    """알고리즘코드별 매수 금액(만원) 저장."""
    sid, alg = _resolveLiveRef(stratId)
    if not alg:
        return {"ok": False, "errMsg": "알고리즘코드 필수"}
    cur = getStratLiveAmts(alg)
    kind = cur.get("amtKind") or "none"
    settings = loadSettings()
    allLive = dict(settings.get("stratLive") or {})
    if not isinstance(allLive, dict):
        allLive = {}
    row = dict(allLive.get(alg) or allLive.get(sid) or {})
    if not isinstance(row, dict):
        row = {}

    if kind == "byStep":
        w5 = _manToWon(amt5Man)
        w10 = _manToWon(amt10Man)
        w15 = _manToWon(amt15Man)
        if w5 is None or w10 is None or w15 is None:
            return {"ok": False, "errMsg": "금액은 1~10000만원 정수로 입력하세요."}
        row["buyAmt5"] = w5
        row["buyAmt10"] = w10
        row["buyAmt15"] = w15
    elif kind == "won":
        w = _manToWon(amtWonMan)
        if w is None:
            return {"ok": False, "errMsg": "금액은 1~10000만원 정수로 입력하세요."}
        row["buyAmtWon"] = w
    else:
        return {"ok": False, "errMsg": "이 전략은 매수 금액 설정이 없습니다."}

    allLive[alg] = row
    # 구 파일명 키는 더 이상 쓰지 않는다
    if sid and sid in allLive and sid != alg:
        allLive.pop(sid, None)
    settings["stratLive"] = allLive
    # 이평터치는 기존 maTouchLive 키도 맞춰 둔다 (구 프로세스 호환)
    if alg == "MTS01" and kind == "byStep":
        mt = dict(settings.get("maTouchLive") or {})
        mt["buyAmt5"] = row["buyAmt5"]
        mt["buyAmt10"] = row["buyAmt10"]
        mt["buyAmt15"] = row["buyAmt15"]
        settings["maTouchLive"] = mt
    saveSettings(settings)
    out = getStratLiveAmts(alg)
    out["ok"] = True
    out["algCd"] = alg
    return out


def setMaTouchLiveAmts(amt5Man, amt10Man, amt15Man):
    """이평선터치 매차 금액. 알고리즘코드 저장으로 위임."""
    return setStratLiveAmts(
        "MTS01",
        amt5Man=amt5Man,
        amt10Man=amt10Man,
        amt15Man=amt15Man,
    )


def setSlrPct(rawVal):
    """
    손절 기준 설정. 입력 부호와 무관하게 음수로 저장.
    성공: (True, 저장된값), 실패: (False, None)
    """
    parsed = parsePctInput(rawVal)
    if parsed is None:
        return False, None
    stored = _toStoredPct(-abs(parsed))
    settings = loadSettings()
    settings["slrPct"] = stored
    saveSettings(settings)
    return True, stored
