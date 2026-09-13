"""신고가 눌림목매매1 유니버스 후보 점검"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from api.getToken import issueAccToken
from config.config import (
    paper_app_key,
    paper_app_secret,
    paper_host_url,
    real_app_key,
    real_app_secret,
    real_host_url,
)
from utils.dbConn import openDbConn
from utils.ntlFirstEntry import fetchFirst52wHighList
from utils.stkCdUtil import normStkCd


def issue():
    issued = issueAccToken(paper_host_url, paper_app_key, paper_app_secret)
    if issued.get("ok"):
        return paper_host_url, True, "paper", issued
    issued = issueAccToken(real_host_url, real_app_key, real_app_secret)
    if issued.get("ok"):
        return real_host_url, False, "real", issued
    return None, None, None, issued


def mktMeta(stkCd):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT STK_NM, MARKET_NAME, MRKT_TP FROM AI_STK_INFO WHERE STK_CD=%s",
                (stkCd,),
            )
            r = cur.fetchone()
            if not r:
                return "-", "-", False
            mn = str(r.get("MARKET_NAME") or "")
            low = mn.lower()
            tp = str(r.get("MRKT_TP") or "")
            if "코스닥" in mn or "kosdaq" in low or tp in ("10", "101"):
                mkt = "코스닥"
                ok = True
            elif "코스피" in mn or "kospi" in low or tp in ("0", "001"):
                mkt = "코스피"
                ok = True
            else:
                mkt = mn or tp or "-"
                ok = False
            return str(r.get("STK_NM") or "-"), mkt, ok
    finally:
        conn.close()


def dayRows(stkCd, tradeDt):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, CLOSE_PRC, OPEN_PRC, TRDE_QTY, FLU_RT,
                       FOR_NETPRPS, ORGN_NETPRPS, FRGN_VAL, ORGN_VAL
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT<=%s
                ORDER BY TRADE_DT DESC
                LIMIT 25
                """,
                (stkCd, tradeDt),
            )
            return cur.fetchall() or []
    finally:
        conn.close()


def main():
    host, isPaper, mode, issued = issue()
    if not host:
        print("토큰 실패:", (issued or {}).get("errMsg"))
        return 1
    print(f"세션: {mode}")

    # 명세: 최초 52주 + 등락 10% 이상
    got = fetchFirst52wHighList(
        host,
        issued["token"],
        tokenType=issued.get("tokenType") or "bearer",
        isPaper=isPaper,
        minFluRt=10.0,
        useClose=False,
        maxCand=80,
        maxOut=40,
    )
    print(
        f"ka10016 후보 {got.get('candCnt')} → 최초진입·flu>=10: {got.get('rowCnt')} "
        f"(일봉실패 {got.get('failCnt')}) baseDt={got.get('baseDt')}"
    )
    rows = got.get("rows") or []
    if not got.get("ok"):
        print("조회 실패:", got.get("errMsg"))
        return 1

    soft = []
    full = []
    print()
    print("=== 상세 ===")
    for r in rows:
        cd = normStkCd(str(r.get("stkCd") or "").split("_")[0])
        nmApi = r.get("stkNm") or cd
        nm, mkt, kosOk = mktMeta(cd)
        if nm == "-":
            nm = nmApi
        tdt = r.get("tradeDt") or "20260807"
        flu = float(r.get("fluRt") or 0)
        days = dayRows(cd, tdt)
        volOk = None
        supOk = None
        frgnV = orgnV = None
        if days:
            d0 = days[0]
            qty = float(d0.get("TRDE_QTY") or 0)
            prev = [float(x.get("TRDE_QTY") or 0) for x in days[1:21]]
            avg = sum(prev) / len(prev) if prev else 0.0
            volOk = bool(avg > 0 and qty >= avg * 1.5)
            try:
                forNet = int(d0.get("FOR_NETPRPS") or 0)
                orgNet = int(d0.get("ORGN_NETPRPS") or 0)
                frgnV = int(d0.get("FRGN_VAL") or 0)
                orgnV = int(d0.get("ORGN_VAL") or 0)
            except (TypeError, ValueError):
                forNet = orgNet = frgnV = orgnV = 0
            supOk = forNet > 0 or orgNet > 0 or frgnV > 0 or orgnV > 0
        item = {
            "cd": cd,
            "nm": nm,
            "mkt": mkt,
            "kosOk": kosOk,
            "tdt": tdt,
            "flu": flu,
            "cur": r.get("curPrc"),
            "volOk": volOk,
            "supOk": supOk,
        }
        print(
            f"{cd} {nm} [{mkt}] 포착 {tdt} 등락 {flu:+.2f}% "
            f"vol={volOk} 수급={supOk} (외{frgnV}/기{orgnV})"
        )
        if kosOk:
            soft.append(item)
            if volOk is True and supOk is True:
                full.append(item)

    print()
    print("=== 대상 (코스피/코스닥 + 최초52주 + 등락>=10%) ===")
    if not soft:
        print("(없음)")
    else:
        for x in soft:
            print(
                f"  {x['nm']}({x['cd']}) | {x['mkt']} | "
                f"포착 {x['tdt']} | {x['flu']:+.2f}% | 종가 {x.get('cur')}"
            )
    print(f"건수: {len(soft)}")

    print()
    print("=== 엄격 (위 + 거래량≥1.5배 + 기관/외인 유입, DB 일봉 있을 때) ===")
    if not full:
        print("(해당 없음 또는 일봉/수급 미적재)")
    else:
        for x in full:
            print(f"  {x['nm']}({x['cd']}) | {x['mkt']} | {x['tdt']}")
    print(f"건수: {len(full)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
