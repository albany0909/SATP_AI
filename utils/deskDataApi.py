"""
매매창 공통 데이터 접근 (관심·설정·로그 조회).

main / client 모두 이 모듈만 본다.
- deskDataViaServer=false: 로컬 Store/DB
- deskDataViaServer=true: 톰캣 /STP/tran gramId (docs/deskStpApi.md)
- client: 항상 서버. 로컬 DB 폴백 없음.

배치 적재는 main 이 DB 직결 — 이 모듈은 조회(및 설정 패치)용.
"""

from __future__ import annotations

from utils.termLog import termLog


def _viaServer():
    try:
        from config.settingsStore import getDeskDataViaServer

        return bool(getDeskDataViaServer())
    except Exception:
        return False


def _isClient():
    try:
        from config.settingsStore import isDeskClient

        return bool(isDeskClient())
    except Exception:
        return False


def _stp(gramId, body):
    from utils.stpTranClient import sendStpTran

    return sendStpTran(gramId, body or {})


def _rowsOrNone(got):
    if not got or not got.get("ok"):
        return None
    rows = (got.get("body") or {}).get("ROWS")
    if isinstance(rows, list):
        return rows
    return None


# ----- 설정 -----


def loadDeskSettings():
    """매매창·운영 설정 전체."""
    if _viaServer():
        got = _stp("SATPAIIQ030", {})
        if got.get("ok"):
            body = got.get("body") or {}
            raw = body.get("SETTINGS_JSON")
            if raw:
                import json

                try:
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(data, dict):
                        return data
                except Exception as err:
                    termLog(f"[deskDataApi] settings JSON 파싱 실패: {err}")
        else:
            from utils.stpTranClient import termLogStpFail

            termLogStpFail("loadDeskSettings", got)
            if _isClient():
                from config.settingsStore import loadSettings

                return loadSettings()
            termLog("[deskDataApi] loadDeskSettings 로컬 폴백")
    from config.settingsStore import loadSettings

    return loadSettings()


def patchDeskSettings(updates: dict):
    """설정 부분 저장. 로컬 patch + (main 만) DB 동기. 서버 모드면 WR 도 시도."""
    from config.settingsStore import patchSettings

    cur = patchSettings(updates or {})
    # client 는 로컬 DB UPSERT 생략
    if not _isClient():
        try:
            from utils.deskCfgStore import upsertDeskSettings

            upsertDeskSettings(cur if isinstance(cur, dict) else {})
        except Exception as err:
            try:
                from utils.dbConn import DbDisabledError, DbDownError

                if not isinstance(err, (DbDisabledError, DbDownError)):
                    termLog(f"[deskDataApi] deskCfg 동기 실패: {err}")
            except Exception:
                termLog(f"[deskDataApi] deskCfg 동기 실패: {err}")
    if _viaServer():
        import json

        got = _stp(
            "SATPAIWR030",
            {"SETTINGS_JSON": json.dumps(cur or {}, ensure_ascii=False)},
        )
        if not got.get("ok"):
            from utils.stpTranClient import termLogStpFail

            termLogStpFail("patchDeskSettings", got)
    return cur


def getDeskTradeMode():
    settings = loadDeskSettings() or {}
    raw = str(settings.get("deskTradeMode") or "paper").strip().lower()
    if raw in ("real", "live", "실전"):
        return "real"
    return "paper"


def getDeskRole():
    from config.settingsStore import getDeskRole as _get

    return _get()


# ----- 관심종목 -----


def listActiveWatch(grpCd=None, useBtOnly=False, mktTp="KR"):
    if _viaServer():
        body = {
            "MKT_TP": str(mktTp or "KR"),
            "KIND": "ACTIVE",
            "USE_BT_ONLY": "Y" if useBtOnly else "N",
        }
        if grpCd:
            body["GRP_CD"] = str(grpCd)
        got = _stp("SATPAIIQ010", body)
        rows = _rowsOrNone(got)
        if rows is not None:
            return rows
        from utils.stpTranClient import termLogStpFail

        termLogStpFail("listActiveWatch", got or {})
        if _isClient():
            return []
        termLog("[deskDataApi] listActiveWatch 로컬 폴백")
    if _isClient():
        return []
    from utils.watchStkStore import listActiveWatch as _list

    return _list(grpCd=grpCd, useBtOnly=useBtOnly, mktTp=mktTp)


def listTradeWatch(mktTp="KR", limit=180):
    if _viaServer():
        got = _stp(
            "SATPAIIQ010",
            {
                "MKT_TP": str(mktTp or "KR"),
                "KIND": "TRADE",
                "LIMIT": int(limit or 180),
            },
        )
        rows = _rowsOrNone(got)
        if rows is not None:
            return rows
        from utils.stpTranClient import termLogStpFail

        termLogStpFail("listTradeWatch", got or {})
        if _isClient():
            return []
        termLog("[deskDataApi] listTradeWatch 로컬 폴백")
    if _isClient():
        return []
    from utils.watchStkStore import listTradeWatch as _list

    return _list(mktTp=mktTp, limit=limit)


def listAlgStk(algCd, limit=300):
    """
    알고리즘 관리종목.
    서버: SATPAIIQ010 KIND=ALG.
    반환 형식은 algStkStore 와 동일 (stkCd/stkNm…).
    """
    alg = str(algCd or "").strip().upper()
    lim = max(1, min(int(limit or 300), 2000))
    if _viaServer() and alg:
        got = _stp(
            "SATPAIIQ010",
            {"KIND": "ALG", "ALG_CD": alg, "LIMIT": lim},
        )
        rows = _rowsOrNone(got)
        if rows is not None:
            return _normAlgStkRows(rows)
        # 구서버(KIND=ALG 미지원) — TRADE 관심으로 표시 (빈 화면 방지)
        if _isClient():
            from utils.stpTranClient import termLogStpFail

            termLogStpFail("listAlgStk", got or {})
            trade = listTradeWatch(mktTp="KR", limit=lim) or []
            if trade:
                termLog(f"[deskDataApi] listAlgStk 폴백 TRADE {len(trade)}건 (ALG API 확인)")
            return _normAlgStkRows(trade)
        termLog("[deskDataApi] listAlgStk 로컬 폴백")
    if _isClient():
        return []
    from utils.algStkStore import listAlgStk as _list

    return _list(algCd, limit=lim)


def _normAlgStkRows(rows):
    """STP/WATCH 행 → {algCd,stkCd,stkNm,…} 또는 UI 가 읽는 STK_CD 혼용."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cd = str(r.get("STK_CD") or r.get("stkCd") or "").strip()
        if not cd:
            continue
        nm = r.get("STK_NM") or r.get("stkNm") or cd
        out.append(
            {
                "algCd": str(r.get("ALG_CD") or r.get("algCd") or "").strip(),
                "stkCd": cd,
                "stkNm": nm,
                "STK_CD": cd,
                "STK_NM": nm,
                "src": r.get("SRC") or r.get("src") or "",
                "note": r.get("NOTE") or r.get("note") or "",
            }
        )
    return out


# ----- 알고리즘 이벤트 -----


def listAlgEvt(algCd, tradeDt, limit=500, mode=None, evtTps=None):
    if _viaServer():
        body = {
            "ALG_CD": str(algCd or "").strip().upper(),
            "TRADE_DT": str(tradeDt or "").replace("-", "")[:8],
            "LIMIT": int(limit or 500),
        }
        if mode is not None:
            body["MODE"] = str(mode)
        if evtTps:
            body["EVT_TPS"] = ",".join(str(t).strip().upper() for t in evtTps if t)
        got = _stp("SATPAIIQ020", body)
        rows = _rowsOrNone(got)
        if rows is not None:
            return rows
        from utils.stpTranClient import termLogStpFail

        termLogStpFail("listAlgEvt", got or {})
        if _isClient():
            return []
        termLog("[deskDataApi] listAlgEvt 로컬 폴백")
    if _isClient():
        return []
    from utils.algEvtStore import listAlgEvt as _list

    return _list(algCd, tradeDt, limit=limit, mode=mode, evtTps=evtTps)


def listBuyAlgMap(tradeDt, algCds, mode=None):
    if _viaServer():
        cds = [str(c).strip().upper() for c in (algCds or []) if c]
        body = {
            "TRADE_DT": str(tradeDt or "").replace("-", "")[:8],
            "ALG_CDS": ",".join(cds),
        }
        if mode is not None:
            body["MODE"] = str(mode)
        got = _stp("SATPAIIQ021", body)
        if got.get("ok"):
            mp = (got.get("body") or {}).get("MAP")
            if isinstance(mp, dict):
                return _normBuyAlgMap(mp)
        from utils.stpTranClient import termLogStpFail

        termLogStpFail("listBuyAlgMap", got or {})
        if _isClient():
            return {}
        termLog("[deskDataApi] listBuyAlgMap 로컬 폴백")
    if _isClient():
        return {}
    from utils.algEvtStore import listBuyAlgMap as _map

    return _map(tradeDt, algCds, mode=mode)


def _normBuyAlgMap(mp):
    """서버 MAP 값(문자열·콤마·list)을 {stkCd: [algCd,...]} 로 맞춤."""
    out = {}
    for rawCd, rawAlgs in (mp or {}).items():
        cd = str(rawCd or "").strip()
        if not cd:
            continue
        algs = []
        if isinstance(rawAlgs, (list, tuple)):
            src = rawAlgs
        else:
            src = str(rawAlgs or "").split(",")
        for a in src:
            alg = str(a or "").strip().upper()
            if alg and alg not in algs:
                algs.append(alg)
        if algs:
            out[cd] = algs
    return out


# ----- 일봉 SMA·종가 (client 그리드/시드) -----


def loadPriorSmaMap(stkCds, periods=None, endDt=None):
    """
    {stkCd: {sma5,sma10,sma20,lastClose,fluRt,lastDt}}
    서버: SATPAIIQ040. client 는 로컬 DB 폴백 없음.
    """
    from utils.stkCdUtil import normStkCd

    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {}

    if _viaServer():
        body = {"STK_CDS": ",".join(codes)}
        if endDt:
            body["END_DT"] = str(endDt).replace("-", "")[:8]
        got = _stp("SATPAIIQ040", body)
        rows = _rowsOrNone(got)
        if rows is not None:
            return _normDaySmaRows(rows)
        from utils.stpTranClient import termLogStpFail

        termLogStpFail("loadPriorSmaMap", got or {})
        if _isClient():
            return {}
        termLog("[deskDataApi] loadPriorSmaMap 로컬 폴백")

    if _isClient():
        return {}
    from utils.daySmaSnap import loadPriorSmaMap as _local

    return _local(codes, periods=periods, endDt=endDt, _forceLocal=True)


def _normDaySmaRows(rows):
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cd = str(r.get("STK_CD") or r.get("stkCd") or "").strip()
        if not cd:
            continue

        def _num(key, *alts):
            for k in (key,) + alts:
                v = r.get(k)
                if v is None or v == "":
                    continue
                try:
                    return int(round(float(v)))
                except (TypeError, ValueError):
                    continue
            return None

        flu = r.get("FLU_RT")
        try:
            fluRt = float(flu) if flu is not None and str(flu).strip() != "" else None
        except (TypeError, ValueError):
            fluRt = None
        out[cd] = {
            "sma5": _num("SMA5", "sma5"),
            "sma10": _num("SMA10", "sma10"),
            "sma20": _num("SMA20", "sma20"),
            "lastClose": _num("LAST_CLOSE", "lastClose"),
            "fluRt": fluRt,
            "lastDt": str(r.get("LAST_DT") or r.get("lastDt") or "")[:8],
        }
    return out


def loadRsiCloseHist(stkCds, need=None, endDt=None):
    """
    RSI용 일봉 종가. {stkCd: {closes:[...], lastDt}}
    서버: SATPAIIQ041.
    """
    from utils.stkCdUtil import normStkCd

    # Wilder RSI14 안정화용 — STP 상한 250
    lim = max(int(need or 250), 16)
    if lim > 250:
        lim = 250
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {}

    if _viaServer():
        body = {"STK_CDS": ",".join(codes), "NEED": lim}
        if endDt:
            body["END_DT"] = str(endDt).replace("-", "")[:8]
        got = _stp("SATPAIIQ041", body)
        rows = _rowsOrNone(got)
        if rows is not None:
            return _normDayCloseRows(rows)
        from utils.stpTranClient import termLogStpFail

        termLogStpFail("loadRsiCloseHist", got or {})
        if _isClient():
            return {}
        termLog("[deskDataApi] loadRsiCloseHist 로컬 폴백")

    if _isClient():
        return {}
    from utils.daySmaSnap import loadRsiCloseHist as _local

    return _local(codes, need=lim, endDt=endDt, _forceLocal=True)


def _normDayCloseRows(rows):
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cd = str(r.get("STK_CD") or "").strip()
        if not cd:
            continue
        raw = str(r.get("CLOSES") or "").strip()
        closes = []
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    px = abs(float(part))
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    closes.append(px)
        out[cd] = {
            "closes": closes,
            "lastDt": str(r.get("LAST_DT") or "")[:8],
        }
    return out


def loadNetSumLastNDays(stkCds, days=20, amtQtyTp="2", trdeTp="0", unitTp="1"):
    """
    최근 N일 순매수. 서버: SATPAIIQ042 → Python 집계.
    """
    from utils.stkCdUtil import normStkCd

    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {}

    if _viaServer():
        got = _stp(
            "SATPAIIQ042",
            {
                "STK_CDS": ",".join(codes),
                "DAYS": int(days or 20),
                "AMT_QTY_TP": str(amtQtyTp),
                "TRDE_TP": str(trdeTp),
                "UNIT_TP": str(unitTp),
            },
        )
        if got.get("ok"):
            body = got.get("body") or {}
            rows = body.get("ROWS")
            dtsRaw = str(body.get("DTS") or "")
            if isinstance(rows, list):
                return _aggInvSrNet(rows, dtsRaw, days)
        from utils.stpTranClient import termLogStpFail

        termLogStpFail("loadNetSumLastNDays", got or {})
        if _isClient():
            return {}
        termLog("[deskDataApi] loadNetSumLastNDays 로컬 폴백")

    if _isClient():
        return {}
    from utils.dayInvSrStore import loadNetSumLastNDays as _local

    return _local(
        codes, days=days, amtQtyTp=amtQtyTp, trdeTp=trdeTp, unitTp=unitTp, _forceLocal=True
    )


def _aggInvSrNet(rows, dtsRaw, days):
    """SATPAIIQ042 ROWS → loadNetSumLastNDays 형식."""
    from utils.stkCdUtil import normStkCd
    from utils.dayInvSrStore import _attachHalfWindow

    try:
        nDays = max(1, min(int(days or 20), 60))
    except (TypeError, ValueError):
        nDays = 20
    recentDts = [d.strip() for d in str(dtsRaw or "").split(",") if d.strip()]
    if not recentDts:
        # ROWS 에서 일자 추출
        dts = sorted(
            {str(r.get("TRADE_DT") or "")[:8] for r in rows or [] if r.get("TRADE_DT")},
            reverse=True,
        )
        recentDts = dts[:nDays]
    recSet = set(recentDts)
    byCd = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cd = normStkCd(r.get("STK_CD"))
        dt = str(r.get("TRADE_DT") or "")[:8]
        if not cd or not dt or dt not in recSet:
            continue
        rec = byCd.setdefault(
            cd,
            {
                "ind": 0,
                "frgn": 0,
                "orgn": 0,
                "pen": 0,
                "invt": 0,
                "samo": 0,
                "orgnEtc": 0,
                "dayCnt": 0,
                "byDt": {},
            },
        )
        indV = int(r.get("IND_VAL") or 0)
        frgnV = int(r.get("FRGN_VAL") or 0)
        orgnV = int(r.get("ORGN_VAL") or 0)
        penV = int(r.get("PEN_VAL") or 0)
        invtV = int(r.get("INVT_VAL") or 0)
        samoV = int(r.get("SAMO_VAL") or 0)
        orgnEtcV = orgnV - penV - invtV - samoV
        try:
            curPrc = abs(int(r.get("CUR_PRC") or 0))
        except (TypeError, ValueError):
            curPrc = 0
        rec["ind"] += indV
        rec["frgn"] += frgnV
        rec["orgn"] += orgnV
        rec["pen"] += penV
        rec["invt"] += invtV
        rec["samo"] += samoV
        rec["orgnEtc"] += orgnEtcV
        rec["dayCnt"] += 1
        rec["byDt"][dt] = {
            "ind": indV,
            "frgn": frgnV,
            "orgn": orgnV,
            "pen": penV,
            "invt": invtV,
            "samo": samoV,
            "orgnEtc": orgnEtcV,
            "curPrc": curPrc,
        }
    out = {}
    for cd, rec in byCd.items():
        byDt = rec.pop("byDt", {}) or {}
        rec["days"] = []
        for dt in recentDts:
            cell = byDt.get(dt)
            if cell is None:
                rec["days"].append(
                    {
                        "dt": dt,
                        "ind": None,
                        "frgn": None,
                        "orgn": None,
                        "pen": None,
                        "invt": None,
                        "samo": None,
                        "orgnEtc": None,
                        "curPrc": None,
                    }
                )
            else:
                rec["days"].append(
                    {
                        "dt": dt,
                        "ind": cell.get("ind"),
                        "frgn": cell.get("frgn"),
                        "orgn": cell.get("orgn"),
                        "pen": cell.get("pen"),
                        "invt": cell.get("invt"),
                        "samo": cell.get("samo"),
                        "orgnEtc": cell.get("orgnEtc"),
                        "curPrc": cell.get("curPrc"),
                    }
                )
        _attachHalfWindow(rec, nDays)
        out[cd] = rec
    return out
