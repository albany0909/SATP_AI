"""PBK01 v7 총평가 70% 한도, 동적 슬롯, KODEX200 3분할.

주문 함수는 없다. 모의 사이클이 이 계획만 받아 paper 호스트로 낸다.
실전 호스트로는 나가지 않는다.
"""

from __future__ import annotations

KODEX = "069500"
CAP_FRAC = 0.70
MAX_SLOTS = 6
SLOT_SIZE = 6_000_000
MIN_HEADROOM = 6_000_000
BOOST_SLOT = 9_000_000
STEP_N = 3
STEP_DAYS = 5
KODEX_BAND = 0.9
KODEX_MIN_DIFF = 100_000
CAP_HM = "085000"

NOTE_V7 = (
    "v7 모의 전용. 총평가 E의 70%가 한도. 신규는 최대 6슬롯, 머리룸 600만 미만이면 안 산다. "
    "KODEX200은 상승장에서 3단계(5거래일·MA20/MA60)로만 사고, 상승장 전액 주차는 하지 않는다. "
    "당일 주식 매도대금은 그날 KODEX 재매수에 쓰지 않는다. pbkFundSellKodex 기본 true."
)


def _won(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        from api.getAcntEvlt import parsePadNum

        return parsePadNum(raw)
    except Exception:
        text = str(raw).replace(",", "").strip()
        sign = -1 if text.startswith("-") else 1
        text = text.lstrip("+-")
        if "." in text:
            text = text.split(".", 1)[0]
        try:
            return sign * int(text or "0")
        except ValueError:
            return None


def fundSellEnabled(settings=None):
    """당일 매수 재원을 KODEX 매도로 채울지. 키가 없으면 켠다. 파일은 쓰지 않는다."""
    if settings is None:
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
    if not isinstance(settings, dict) or "pbkFundSellKodex" not in settings:
        return True
    return settings.get("pbkFundSellKodex") is True


def capWon(equity):
    try:
        return int(float(equity) * CAP_FRAC)
    except (TypeError, ValueError):
        return 0


def headroom(equity, exposure):
    """H = 0.70E − X. 음수면 그대로 둔다."""
    try:
        return int(float(equity) * CAP_FRAC - float(exposure))
    except (TypeError, ValueError):
        return 0


def nominalSlot(scale):
    """코스닥 역배열 1.5배는 900만. 그 외는 600만."""
    try:
        boosted = abs(float(scale) - 1.5) < 1e-9
    except (TypeError, ValueError):
        boosted = False
    return BOOST_SLOT if boosted else SLOT_SIZE


def reservedWon(positions, blockedCodes=None, slotKrw=SLOT_SIZE):
    """아직 안 산 2차. 대금 미달로 막힌 종목은 예약 0."""
    from strategies.impl.pullbackTwoStep import legMoney, scaledSlot

    blocked = {str(code) for code in (blockedCodes or [])}
    total = 0
    for pos in positions or []:
        if not isinstance(pos, dict) or not pos.get("leg1"):
            continue
        code = str(pos.get("stkCd") or "")
        if code in blocked or pos.get("amtOk") is False:
            continue
        leg2 = pos.get("leg2") if isinstance(pos.get("leg2"), dict) else {}
        filled = int(leg2.get("filledQty") or 0)
        remain = int(leg2.get("remainQty") or 0)
        if leg2.get("settled") and filled > 0 and remain <= 0:
            continue
        if leg2.get("settled") and remain <= 0 and filled <= 0 and not leg2:
            pass
        scale = pos.get("slotBoost") if pos.get("slotBoost") not in (None, "") else 1
        budget = scaledSlot(slotKrw, scale)
        planned = legMoney(budget, 40)
        filledWon = 0
        try:
            px = float(leg2.get("fillPx") or 0)
            filledWon = int(filled * px)
        except (TypeError, ValueError):
            filledWon = 0
        total += max(0, int(planned) - filledWon)
    return total


def splitHoldValues(holds, kodexPx=None):
    """보유 평가를 주식과 KODEX 로 나눈다."""
    stk = 0
    kodex = 0
    kodexQty = 0
    for row in holds or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or row.get("stk_cd") or "").replace("A", "")[-6:]
        evlt = _won(row.get("evltAmt"))
        if evlt is None:
            evlt = _won(row.get("evlt_amt"))
        qty = _won(row.get("qtyNum"))
        if qty is None:
            qty = _won(row.get("rmnd_qty")) or 0
        if evlt is None:
            px = _won(row.get("cur_prc")) or _won(row.get("curPrc"))
            if code == KODEX and px is None and kodexPx:
                px = _won(kodexPx)
            evlt = int(qty or 0) * int(px or 0)
        if code == KODEX:
            kodex += int(evlt or 0)
            kodexQty += int(qty or 0)
        else:
            stk += int(evlt or 0)
    return {"stkExpo": stk, "kodexVal": kodex, "kodexQty": kodexQty}


def equityFromBody(body, holds=None):
    """E = D+2 추정예수금 + 주식 평가 + KODEX 평가.

    추정예탁자산(prsm_dpst_aset_amt)은 D+2와 평가가 없을 때의 대체다.
    평가가 0이거나 TR 본문이 없으면 ok 는 False. 직전 E 는 넣지 않는다.
    """
    if not isinstance(body, dict) or not body:
        return {"ok": False, "reason": "TR", "E": None}
    parts = splitHoldValues(holds if holds is not None else body.get("stk_acnt_evlt_prst"))
    d2 = _won(body.get("d2_entra"))
    tot = _won(body.get("tot_est_amt"))
    prsm = _won(body.get("prsm_dpst_aset_amt"))
    if parts["stkExpo"] or parts["kodexVal"]:
        stk = parts["stkExpo"]
        kodex = parts["kodexVal"]
    elif tot:
        stk = int(tot)
        kodex = 0
    else:
        stk = 0
        kodex = 0
    if d2 is None and prsm is None:
        return {"ok": False, "reason": "TR", "E": None}
    if d2 is not None and (stk or kodex or tot):
        equity = int(d2) + int(stk) + int(kodex)
    elif prsm:
        equity = int(prsm)
        if tot and kodex == 0 and stk == 0:
            stk = int(tot)
    else:
        return {"ok": False, "reason": "TR", "E": None}
    if equity <= 0:
        return {"ok": False, "reason": "평가0", "E": 0, "stkExpo": stk, "kodexVal": kodex}
    orderable = None
    for key in ("ord_alow_amt", "100stk_ord_alow_amt", "50stk_ord_alow_amt", "d2_entra"):
        orderable = _won(body.get(key))
        if orderable is not None:
            break
    return {
        "ok": True,
        "reason": "",
        "E": int(equity),
        "cashD2": int(d2 or 0),
        "stkExpo": int(stk),
        "kodexVal": int(kodex),
        "kodexQty": int(parts["kodexQty"]),
        "orderable": int(orderable or 0),
    }


def exposureOf(stkExpo, reserved, kodexVal):
    return int(stkExpo or 0) + int(reserved or 0) + int(kodexVal or 0)


def admitNew(slots, equity, expo, scale, orderable, kodexQty=0, kodexPx=0, fundSell=True, n=0, code=""):
    """신규 1차 직전 판정. 기존 보유의 2차에는 쓰지 않는다.

    H < 600만이면 쪼개지 않는다. S 는 min(명목, H).
    """
    H = headroom(equity, expo)
    nominal = nominalSlot(scale)
    S = min(nominal, H) if H > 0 else nominal
    base = {
        "n": n,
        "stkCd": code,
        "H": H,
        "S": S,
        "slots": int(slots or 0),
    }
    if int(slots or 0) >= MAX_SLOTS:
        base["skip"] = "MAX_SLOT"
        return base
    if H < MIN_HEADROOM:
        base["skip"] = f"CAP_FULL({H},{nominal})"
        base["S"] = nominal
        return base
    cash = int(orderable or 0)
    if cash < S:
        if not fundSell:
            base["skip"] = "NO_FUND"
            return base
        short = S - cash
        qty = _sellQty(short, kodexPx, kodexQty)
        if qty <= 0:
            base["skip"] = "NO_FUND"
            return base
        proceeds = _sellValue(qty, kodexPx)
        if cash + proceeds < S:
            base["skip"] = "NO_FUND"
            return base
        base["fundSellQty"] = qty
        base["fundSellPx"] = int(kodexPx)
        base["fundSellWon"] = proceeds
    base["take"] = True
    base["budget"] = S
    base["slot6"] = int(slots or 0) == MAX_SLOTS - 1
    return base


def _sellQty(short, px, qty):
    try:
        price = float(px)
        have = int(qty or 0)
        need = int(short)
    except (TypeError, ValueError):
        return 0
    if price <= 0 or have <= 0 or need <= 0:
        return 0
    from trading.pbkKodexPark import sharesForCash

    return min(have, sharesForCash(need, price, buy=False))


def _sellValue(qty, px):
    from trading.pbkKodexPark import tradeValue

    return tradeValue(qty, px, buy=False)


def maFlags(closes):
    """전일 종가와 전일까지 MA20·MA60. 봉이 부족하면 그 깃발은 False."""
    rows = [float(px) for px in (closes or []) if px]
    if not rows:
        return False, False
    last = rows[-1]
    conf = False
    weak = False
    if len(rows) >= 20:
        ma20 = sum(rows[-20:]) / 20.0
        conf = last > ma20
    if len(rows) >= 60:
        ma60 = sum(rows[-60:]) / 60.0
        weak = last < ma60
    return conf, weak


def restoreLevel(kodexVal, room):
    """저장이 없으면 평가/여유로 L 을 복원. 불가하면 0."""
    try:
        val = float(kodexVal)
        space = float(room)
    except (TypeError, ValueError):
        return 0, False
    if space <= 0 or val < 0:
        return 0, False
    level = int(round(val / space * STEP_N))
    return max(0, min(STEP_N, level)), True


def advanceStep(state, regime, conf, weak, ymd, kodexVal=0, room=0):
    """08:50 에 하루 한 번. 이미 그 날이면 L 을 유지한다."""
    state = dict(state or {})
    day = str(ymd or "")
    if state.get("L") in (None, "") and not state.get("stepYmd"):
        level, ok = restoreLevel(kodexVal, room)
        state["L"] = level
        state["cnt"] = int(state.get("cnt") or 0)
        state["restored"] = ok
    if day and str(state.get("stepYmd") or "") == day:
        state["L"] = max(0, min(STEP_N, int(state.get("L") or 0)))
        return state, []
    prev = str(state.get("regime") or "")
    level = max(0, min(STEP_N, int(state.get("L") or 0)))
    cnt = int(state.get("cnt") or 0)
    start = level
    kind = ""
    reason = ""
    bear = regime == "하락"
    bull = regime == "상승"
    if bear:
        level = 0
        cnt = 0
        reason = "하락장"
        kind = "KODEX_STEP_DOWN" if start != 0 else ""
    elif bull and prev != "상승":
        cnt = 0
        if conf:
            level = max(level, 1)
        reason = "하락→상승"
        if level > start:
            kind = "KODEX_STEP_UP"
        elif level < start:
            kind = "KODEX_STEP_DOWN"
        else:
            kind = "KODEX_STEP_REBAL"
    elif bull:
        cnt += 1
        reason = "상승지속"
        if cnt >= STEP_DAYS:
            cnt = 0
            if weak and level > 0:
                level -= 1
                kind = "KODEX_STEP_DOWN"
                reason = "WEAK"
            elif conf and level < STEP_N:
                level += 1
                kind = "KODEX_STEP_UP"
                reason = "CONF"
            else:
                kind = "KODEX_STEP_REBAL"
                reason = "유지"
    level = max(0, min(STEP_N, level))
    state["L"] = level
    state["cnt"] = cnt
    state["regime"] = regime if regime in ("상승", "하락") else prev
    if day and regime in ("상승", "하락"):
        state["stepYmd"] = day
    logs = []
    if kind:
        logs.append({"kind": kind, "fromL": start, "toL": level, "reason": reason})
    return state, logs


def kodexTarget(level, room, bear=False):
    if bear or int(level or 0) <= 0:
        return 0
    space = max(0, int(room or 0))
    return int(int(level) / STEP_N * space)


def planKodexOrders(level, prevLevel, room, kodexQty, px, cash, bear=False, levelFell=False):
    """목표 대비 매수·매도. 한도만 줄었다고 팔지 않는다.

    매수 현금은 호출자가 준 08:50 잔액이다. 당일 주식 매도대금은 넣지 않는다.
    """
    from trading.pbkKodexPark import sharesForCash, tradeValue

    target = kodexTarget(level, room, bear=bear)
    try:
        price = float(px or 0)
        qty = max(0, int(kodexQty or 0))
        money = max(0, int(cash or 0))
    except (TypeError, ValueError):
        return [], target
    cur = int(qty * price) if price > 0 else 0
    orders = []
    fell = bool(levelFell or bear or (prevLevel is not None and int(level) < int(prevLevel)))
    if fell and price > 0 and cur > target and qty > 0:
        excess = cur - target
        sellQty = min(qty, sharesForCash(excess, price, buy=False))
        if sellQty > 0:
            orders.append({
                "stkCd": KODEX,
                "side": "SELL",
                "qty": sellQty,
                "limitPx": int(price),
                "reason": "KODEX_STEP_DOWN" if bear or int(level) < int(prevLevel or level) else "KODEX_STEP_REBAL",
                "trdeTp": "0",
            })
        return orders, target
    gap = target - cur
    if (not bear) and target > 0 and price > 0 and cur < target * KODEX_BAND and gap >= KODEX_MIN_DIFF:
        buyMoney = min(money, gap)
        buyQty = sharesForCash(buyMoney, price, buy=True)
        if buyQty > 0 and tradeValue(buyQty, price, buy=True) <= money:
            orders.append({
                "stkCd": KODEX,
                "side": "BUY",
                "qty": buyQty,
                "limitPx": int(price),
                "reason": "KODEX_STEP_UP",
                "trdeTp": "0",
            })
    return orders, target


def capLine(snap):
    """CAP70 한 줄. 비밀은 넣지 않는다."""
    snap = snap or {}
    return (
        "CAP70|{ymd}|{E}|{cap}|{stk}|{res}|{kodex}|{room}|{L}|{cnt}|{target}|{conf}|{weak}|{bear}".format(
            ymd=snap.get("ymd") or "",
            E=int(snap.get("E") or 0),
            cap=int(snap.get("cap") or 0),
            stk=int(snap.get("stkExpo") or 0),
            res=int(snap.get("reserved") or 0),
            kodex=int(snap.get("kodexVal") or 0),
            room=int(snap.get("room") or 0),
            L=int(snap.get("L") or 0),
            cnt=int(snap.get("cnt") or 0),
            target=int(snap.get("target") or 0),
            conf=1 if snap.get("conf") else 0,
            weak=1 if snap.get("weak") else 0,
            bear=1 if snap.get("bear") else 0,
        )
    )


def skipLine(verdict):
    verdict = verdict or {}
    return (
        f"[PBK01] skip {verdict.get('skip')} #{verdict.get('n') or '-'} "
        f"{verdict.get('stkCd') or ''} H={verdict.get('H')} S={verdict.get('S')}"
    )


def stepLine(row, won=0):
    return f"{row.get('kind')}|{row.get('fromL')}→{row.get('toL')}|{int(won or 0)}|{row.get('reason')}"


def shadowV6(names, regime, cash, px, taken):
    """v6 라면 슬롯 5·한도 없이, 상승장이면 남은 현금 전액을 KODEX. 주문은 없다."""
    from trading.pbkKodexPark import sharesForCash

    want = [str(code) for code in (names or []) if code][:5]
    kodex = 0
    if regime == "상승" and px:
        kodex = sharesForCash(cash or 0, px, buy=True)
    held = ",".join(str(code) for code in (taken or [])[:8])
    return f"V6SHADOW|slot5|nocap|buys={','.join(want)}|kodexQty={kodex}|v7={held}|orders=0"


def settleCapDay(saved, ymd, hm, account, regime, closes, reserved, fundSell=True, kodexPx=0, todayExpo=0):
    """그날 E 는 한 번만. 08:50 전이고 오늘 값이 없으면 기다린다.

    저장된 오늘 E 가 있으면 장중에 다시 계산하지 않는다.
    """
    saved = dict(saved or {})
    day = str(ymd or "")
    textHm = "".join(ch for ch in str(hm or "") if ch.isdigit()).zfill(6)[-6:]
    prior = saved.get("cap70") if isinstance(saved.get("cap70"), dict) else {}
    reuse = str(prior.get("ymd") or "") == day and prior.get("ok") and prior.get("E")
    if not reuse and textHm < CAP_HM:
        return {"ok": False, "wait": True, "reason": "CAP_WAIT", "cap70": prior, "logs": [], "state": saved}
    if reuse:
        equity = dict(prior)
    else:
        equity = equityFromBody(account.get("body") if isinstance(account, dict) and "body" in account else account, account.get("holds") if isinstance(account, dict) else None)
        if not equity.get("ok"):
            return {
                "ok": False,
                "wait": False,
                "reason": equity.get("reason") or "TR",
                "cap70": {},
                "logs": [],
                "state": saved,
                "alert": True,
            }
    stk = int(equity.get("stkExpo") or 0)
    if reuse:
        stk += int(todayExpo or 0)
    kodexVal = int(equity.get("kodexVal") or 0)
    res = int(reserved or 0)
    expo = exposureOf(stk, res, kodexVal)
    room = max(0, headroom(equity["E"], stk + res))
    conf, weak = maFlags(closes)
    bear = regime == "하락"
    state, stepLogs = advanceStep(
        saved, regime, conf, weak, day, kodexVal=kodexVal, room=room,
    )
    level = int(state.get("L") or 0)
    target = kodexTarget(level, room, bear=bear)
    snap = {
        "ok": True,
        "ymd": day,
        "E": int(equity["E"]),
        "cap": capWon(equity["E"]),
        "stkExpo": stk,
        "reserved": res,
        "kodexVal": kodexVal,
        "kodexQty": int(equity.get("kodexQty") or saved.get("kodexQty") or 0),
        "room": int(room),
        "L": level,
        "cnt": int(state.get("cnt") or 0),
        "target": int(target),
        "conf": bool(conf),
        "weak": bool(weak),
        "bear": bool(bear),
        "orderable": int(equity.get("orderable") or 0),
        "cashD2": int(equity.get("cashD2") or 0),
        "fundSell": bool(fundSell),
        "kodexPx": int(kodexPx or 0),
        "prevL": int(prior.get("L") if prior.get("L") not in (None, "") else level),
    }
    state["cap70"] = snap
    state["L"] = level
    state["cnt"] = snap["cnt"]
    logs = [capLine(snap)]
    for row in stepLogs:
        logs.append(stepLine(row, won=target))
    if state.get("restored") and not reuse:
        logs.append(f"KODEX_LEVEL_RESTORE|L={level}")
    return {"ok": True, "wait": False, "reason": "", "cap70": snap, "logs": logs, "state": state, "alert": False}


def makeCapGate(snap, slots, kodexQty=0, kodexPx=0):
    """한 사이클 안에서 신규가 통과할 때마다 X 와 슬롯을 올린다."""
    book = {
        "X": exposureOf(snap.get("stkExpo"), snap.get("reserved"), snap.get("kodexVal")),
        "orderable": int(snap.get("orderable") or 0),
        "slots": int(slots or 0),
        "kodexQty": int(kodexQty if kodexQty else snap.get("kodexQty") or 0),
        "E": snap.get("E"),
    }
    fund = snap.get("fundSell") is not False

    def gate(info):
        info = info or {}
        if not snap.get("ok"):
            return {"skip": snap.get("reason") or "CAP_NODATA", "n": info.get("n"), "stkCd": info.get("stkCd"), "H": None, "S": None}
        verdict = admitNew(
            book["slots"],
            book["E"],
            book["X"],
            info.get("scale"),
            book["orderable"],
            kodexQty=book["kodexQty"],
            kodexPx=kodexPx or snap.get("kodexPx") or 0,
            fundSell=fund,
            n=info.get("n"),
            code=info.get("stkCd"),
        )
        if verdict.get("take"):
            book["X"] += int(verdict.get("budget") or 0)
            book["slots"] += 1
            sold = int(verdict.get("fundSellWon") or 0)
            book["orderable"] = max(0, book["orderable"] + sold - int(verdict.get("budget") or 0))
            if verdict.get("fundSellQty"):
                book["kodexQty"] = max(0, book["kodexQty"] - int(verdict["fundSellQty"]))
        return verdict

    return gate
