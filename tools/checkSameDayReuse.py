"""모의 계좌의 예수금·주문가능금액을 읽기만 한다.

주문은 내지 않는다. 토큰·키·계좌번호는 출력하지 않는다.

  python tools/checkSameDayReuse.py

kt00001 예수금상세(추정조회)와 kt00010 주문인출가능금액을 모의 호스트에서 읽는다.
D+2 추정예수금이 주문가능금액보다 크면, 당일 매도 정산이 주문가능에는 아직 안 들어왔을 수 있다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHOW = (
    "entr",
    "d1_entra",
    "d2_entra",
    "ord_alow_amt",
    "pymn_alow_amt",
    "wthd_alowa",
    "20stk_ord_alow_amt",
    "50stk_ord_alow_amt",
    "100stk_ord_alow_amt",
    "prsm_dpst_aset_amt",
)


def _won(raw):
    from api.getAcntEvlt import parsePadNum

    return parsePadNum(raw)


def pickCash(body):
    """출력해도 되는 금액 필드만."""
    out = {}
    if not isinstance(body, dict):
        return out
    for key in SHOW:
        if key in body:
            out[key] = _won(body.get(key))
    return out


def reuseHint(fields):
    """D+2 와 주문가능금액을 비교한다. 주문은 하지 않는다."""
    d2 = fields.get("d2_entra")
    orderable = fields.get("ord_alow_amt")
    if orderable is None:
        orderable = fields.get("100stk_ord_alow_amt")
    if d2 is None or orderable is None:
        return "비교불가"
    gap = int(d2) - int(orderable)
    if gap > 0:
        return f"D+2가 주문가능보다 {gap}원 큼. 당일 매도정산이 주문가능에 없을 수 있음"
    if gap < 0:
        return f"주문가능이 D+2보다 {-gap}원 큼"
    return "D+2와 주문가능이 같음"


def _post(host, token, tokenType, apiId, body):
    import requests

    auth = (tokenType or "bearer").strip()
    if auth.lower() == "bearer":
        auth = "Bearer"
    res = requests.post(
        host.rstrip("/") + "/api/dostk/acnt",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"{auth} {token}",
            "api-id": apiId,
            "cont-yn": "N",
            "next-key": "",
        },
        json=body,
        timeout=30,
    )
    try:
        payload = res.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return res.status_code, payload


def readPaperCash(session=None, poster=None):
    """모의 호스트만. 실전 호스트면 조회하지 않는다."""
    from config.config import paper_host_url, real_host_url

    sess = session
    if sess is None:
        from utils.dayPrcLoadService import acquireKiwoomSession

        got = acquireKiwoomSession(preferMode="paper")
        if not got:
            return {"ok": False, "reason": "NO_SESSION"}
        mode, host, token, tokenType = got
        sess = {"mode": mode, "host": host, "token": token, "tokenType": tokenType}
    host = str(sess.get("host") or "")
    if sess.get("mode") != "paper" or not host or host == str(real_host_url) or "mockapi.kiwoom.com" not in host:
        return {"ok": False, "reason": "PAPER_ONLY"}
    if not sess.get("token"):
        return {"ok": False, "reason": "NO_TOKEN"}
    send = poster or _post
    code1, body1 = send(host, sess["token"], sess.get("tokenType"), "kt00001", {"qry_tp": "3"})
    code10, body10 = send(host, sess["token"], sess.get("tokenType"), "kt00010", {"io_amt": "", "stk_cd": "", "trde_tp": "2"})
    fields = pickCash(body1)
    fields.update({key: val for key, val in pickCash(body10).items() if fields.get(key) is None})
    ok = code1 == 200 and int(body1.get("return_code") or 0) == 0
    return {
        "ok": bool(ok),
        "kt00001": code1,
        "kt00010": code10,
        "fields": fields,
        "hint": reuseHint(fields),
        "msg1": str(body1.get("return_msg") or "")[:80],
        "msg10": str(body10.get("return_msg") or "")[:80],
    }


def main():
    got = readPaperCash()
    if not got.get("ok") and got.get("reason"):
        print(f"조회 안 함 {got.get('reason')}")
        return 1
    print(f"kt00001 HTTP {got.get('kt00001')} {got.get('msg1')}")
    print(f"kt00010 HTTP {got.get('kt00010')} {got.get('msg10')}")
    for key in SHOW:
        if key in (got.get("fields") or {}):
            val = got["fields"][key]
            print(f"{key} {val if val is not None else '-'}")
    print(got.get("hint") or "")
    return 0 if got.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
