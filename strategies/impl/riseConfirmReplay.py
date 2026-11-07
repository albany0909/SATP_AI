"""RCS01 격리 리플레이.

공식 연구 픽스처(schema rcs01.research-parity.v1)를 앱 판단 경로에만 넣는다.
reference engine 을 호출하지 않고, synthetic 으로 바꾸지 않으며,
provenance 승인 입력을 만들지 않는다. 설정·주문·계좌에는 쓰지 않는다.

연구 리플레이는 손실 매도도 자동 체결한다. 라이브 손실 확인과는 별개다.
"""

from __future__ import annotations

import json
from pathlib import Path

from strategies.impl.riseConfirmScale import LOSS_BUDGET, DataError, RiseConfirmBook

SCHEMA = "rcs01.research-parity.v1"
SCHEMA_LOSS_100K = "rcs01.research-parity.loss100k.v1"
OCT_START = "20261001"
OCT_END = "20261106"
# 스펙 기본값. rcs01.research-parity.v1 픽스처의 loss=300000 은 과거 명시 파라미터다.
DEFAULT_LOSS_KRW = LOSS_BUDGET


def load_parity_fixture(path):
    """연구 동등성 픽스처. data_kind 를 합성으로 덮어쓰지 않는다."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DataError("픽스처가 객체가 아니다")
    if raw.get("schema_version") != SCHEMA:
        raise DataError(f"스키마가 {SCHEMA} 이 아니다: {raw.get('schema_version')}")
    kind = raw.get("data_kind") or raw.get("meta", {}).get("data_kind") if isinstance(raw.get("meta"), dict) else raw.get("data_kind")
    if kind == "synthetic_unit_test":
        raise DataError("공식 연구 픽스처를 synthetic 으로 취급하지 않는다")
    if raw.get("config", {}).get("toy_mode") is True:
        raise DataError("공식 연구 픽스처 toy_mode 를 승인하지 않는다")
    for key in ("calendar", "index", "universe", "stocks", "initial_states", "golden", "config", "window"):
        if key not in raw:
            raise DataError(f"픽스처 필드 없음: {key}")
    window = raw["window"]
    if window.get("start") != OCT_START or window.get("end") != OCT_END:
        raise DataError("10월 검증 창이 20261001~20261106 이 아니다")
    if len(raw["calendar"]) != 104:
        raise DataError("104세션(워밍업 79 + 검증 25)이 아니다")
    if raw["calendar"][78] != "20260930" or raw["calendar"][79] != OCT_START:
        raise DataError("9/30 은 워밍업 마지막이고 10/1 이 첫 검증 세션이어야 한다")
    return raw


def load_loss100k_fixture(path):
    """기본 손절예산 10만원과 같은 10월 A 픽스처. 스펙 파일은 수정하지 않는다."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DataError("픽스처가 객체가 아니다")
    if raw.get("schema_version") != SCHEMA_LOSS_100K:
        raise DataError(f"10만원 스키마가 아니다: {raw.get('schema_version')}")
    kind = raw.get("data_kind")
    if kind == "synthetic_unit_test" or raw.get("config", {}).get("toy_mode") is True:
        raise DataError("10만원 연구 픽스처를 synthetic 으로 취급하지 않는다")
    if int(raw.get("config", {}).get("loss") or 0) != 100_000:
        raise DataError("이 경로는 config.loss=100000 만 재생한다")
    if "B" in (raw.get("initial_states") or {}) or "B" in (raw.get("golden") or {}):
        raise DataError("10만원 실험은 A 한 경로만 있다")
    window = raw.get("window") or {}
    if window.get("start") != OCT_START or window.get("end") != OCT_END:
        raise DataError("10만원 실험도 같은 10/1~11/6 창이다")
    if len(raw.get("calendar") or []) != 104:
        raise DataError("104세션이 아니다")
    return raw


def _rankings(book):
    rows = []
    for row in book.history:
        day = row["date"]
        ranked = book.rankings(day)
        rows.append(
            {
                "day": day,
                "qualified": len(ranked),
                "top3": [[ret, turn, code] for ret, turn, code in ranked[:3]],
            }
        )
    return rows


def replay_track(fixture, track, research_auto_exit=True):
    if track not in ("A", "B"):
        raise DataError("track 은 A 또는 B")
    if research_auto_exit is not True:
        raise DataError("공식 숫자 비교는 연구 자동청산 경로만 사용한다")
    book = RiseConfirmBook(
        fixture,
        fixture["config"],
        fixture["initial_states"][track],
        research_auto_exit=True,
    )
    book.run()
    built = _build(book, fixture, track)
    diffs = _diffs(built, fixture["golden"][track], track)
    return {
        "track": track,
        "research_auto_exit": True,
        "live_loss_confirmation": "별도 라이브 경로는 손실 매도를 자동체결하지 않는다",
        "actual": built,
        "diffs": diffs,
        "rankings": _rankings(book),
        "confirms": list(book.confirms),
    }


def _build(book, fixture, track):
    boundary = book.boundary_equity()
    last = book.history[-1]
    final_equity = last["equity"]
    base = boundary["equity"]
    pnl = final_equity - base
    mdd, mdd_dates = _mdd(book.history, base, boundary["date"] if track == "B" else book.start)
    closed = [c for c in book.campaigns if c["status"] == "closed"]
    period_realized = sum(c["profit"] for c in closed)
    if book.active:
        gross_unreal = book.active["q"] * book.bar(book.active["code"], book.end)["mark_close"] - book.active["cost"]
    else:
        gross_unreal = 0
    costs = _costs(book.ledger)
    index_base = boundary["date"] if track == "B" else book.start
    index_ret = (fixture["index"][book.end] / fixture["index"][index_base] - 1) * 100
    adverse = _adverse_liquidation(book)
    summary = {
        "final_equity": final_equity,
        "profit": pnl,
        "return_percent": pnl / base * 100,
        "mdd_percent": mdd * 100,
        "realized": period_realized if track == "A" else None,
        "unrealized_mark": (pnl - period_realized) if track == "A" else gross_unreal,
        "unrealized_net_liquidation": (last["liquidation_equity"] - base - period_realized) if track == "A" else None,
        "net_liquidation_equity": last["liquidation_equity"],
        "completed_campaigns": len(closed),
        "campaigns_started": len(book.campaigns) if track == "A" else None,
        "win_rate_percent": (sum(c["profit"] > 0 for c in closed) / len(closed) * 100) if closed and track == "A" else (0.0 if track == "A" else None),
        "total_buy_cost": sum(c["cost"] for c in book.campaigns) if track == "A" else None,
        "peak_cost_exposure": max(row["cost_exposure"] for row in book.history),
        "mean_cost_exposure": sum(row["cost_exposure"] for row in book.history) / len(book.history),
        "index_price_return_percent": index_ret,
    }
    return {
        "boundary": boundary,
        "summary": summary,
        "history": book.history,
        "ledger": book.ledger,
        "campaigns": book.campaigns,
        "pending": book.pending,
        "period_realized": period_realized,
        "initial_gross_unrealized": boundary["gross_unrealized"],
        "final_gross_unrealized": gross_unreal,
        "unrealized_change": gross_unreal - boundary["gross_unrealized"],
        "mdd_dates": mdd_dates,
        "costs": costs,
        "terminal_fee_tax_only": last["liquidation_equity"],
        "terminal_with_adverse_tick": adverse,
        "index_return_percent": index_ret,
        "final_equity": final_equity,
        "pnl": pnl,
        "return_percent": pnl / base * 100,
        "mdd_percent": mdd * 100,
    }


def _mdd(history, start_equity, start_date):
    peak = start_equity
    peak_date = start_date
    worst = 0.0
    trough_date = start_date
    trough_equity = start_equity
    peak_at = start_date
    peak_eq = start_equity
    for row in history:
        equity = row["equity"]
        if equity > peak:
            peak = equity
            peak_date = row["date"]
        drop = equity / peak - 1
        if drop < worst:
            worst = drop
            trough_date = row["date"]
            trough_equity = equity
            peak_at = peak_date
            peak_eq = peak
    return worst, {
        "fraction": worst,
        "peak_date": peak_at,
        "peak_equity": peak_eq,
        "trough_date": trough_date,
        "trough_equity": trough_equity,
    }


def _costs(ledger):
    buys = [row for row in ledger if row["kind"] == "buy"]
    sells = [row for row in ledger if row["kind"] == "sell"]
    buy_fee = 0
    sell_fee = 0
    sell_tax = 0
    for row in buys:
        gross = row["quantity"] * row["price"]
        buy_fee += row["cost"] - gross
    for row in sells:
        gross = row["quantity"] * row["price"]
        from strategies.impl.riseConfirmScale import floor_money

        sell_fee += floor_money(gross, 0.00015)
        sell_tax += floor_money(gross, 0.002)
    return {
        "buys": len(buys),
        "sells": len(sells),
        "cancelled": sum(1 for row in ledger if row["kind"] == "buy_cancel"),
        "buy_cost": sum(row["cost"] for row in buys),
        "sell_net": sum(row["proceeds"] for row in sells),
        "buy_fee": buy_fee,
        "sell_fee": sell_fee,
        "sell_tax": sell_tax,
    }


def _adverse_liquidation(book):
    if not book.active:
        return book.cash
    pos = book.active
    bar = book.bar(pos["code"], book.end)
    price = book.modeled_fill(bar["mark_close"], bar, "sell")
    from strategies.impl.riseConfirmScale import whole_money

    return book.cash + book.proceeds(pos["q"], whole_money(price))


def _diffs(actual, golden, track):
    diffs = []
    if track == "A":
        _cmp_summary(diffs, actual["summary"], golden["summary"])
        _cmp_rows(diffs, "history", actual["history"], golden["history"], ("date", "cash", "market_value", "equity", "liquidation_equity", "cost_exposure", "stop_price", "quantity"))
        _cmp_ledger(diffs, actual["ledger"], golden["ledger"])
        _cmp_campaigns(diffs, actual["campaigns"], golden["campaigns"])
        _cmp_value(diffs, "pending", actual["pending"], golden.get("pending_unexecuted_at_end"))
    else:
        _cmp_value(diffs, "final_equity", actual["final_equity"], golden["final_equity"])
        _cmp_value(diffs, "pnl", actual["pnl"], golden["pnl"])
        _cmp_value(diffs, "return_percent", actual["return_percent"], golden["return_percent"])
        _cmp_value(diffs, "mdd_percent", actual["mdd_percent"], golden["mdd_percent"])
        _cmp_value(diffs, "period_realized", actual["period_realized"], golden["realized_closed_campaign_profit"])
        _cmp_value(diffs, "initial_gross_unrealized", actual["initial_gross_unrealized"], golden["initial_gross_unrealized"])
        _cmp_value(diffs, "final_gross_unrealized", actual["final_gross_unrealized"], golden["final_gross_unrealized"])
        _cmp_value(diffs, "unrealized_change", actual["unrealized_change"], golden["unrealized_change"])
        _cmp_value(diffs, "index_return_percent", actual["index_return_percent"], golden["index_return_percent"])
        _cmp_value(diffs, "terminal_fee_tax_only", actual["terminal_fee_tax_only"], golden["terminal_fee_tax_only"])
        _cmp_value(diffs, "terminal_with_adverse_tick", actual["terminal_with_adverse_tick"], golden["terminal_with_adverse_tick"])
        _cmp_value(diffs, "pending", actual["pending"], golden.get("pending"))
        for key in ("buys", "sells", "cancelled", "buy_cost", "sell_net", "buy_fee", "sell_fee", "sell_tax"):
            _cmp_value(diffs, f"costs.{key}", actual["costs"][key], golden["costs"][key])
        for key in ("fraction", "peak_date", "peak_equity", "trough_date", "trough_equity"):
            _cmp_value(diffs, f"mdd.{key}", actual["mdd_dates"][key], golden["mdd_dates"][key])
        _cmp_rows(diffs, "history", actual["history"], golden["history"], ("date", "cash", "market_value", "equity", "liquidation_equity", "cost_exposure", "stop_price", "quantity"))
        _cmp_ledger(diffs, actual["ledger"], golden["ledger"])
        _cmp_campaigns(diffs, actual["campaigns"], golden["campaigns"])
    return diffs


def _cmp_summary(diffs, actual, golden):
    for key, expected in golden.items():
        _cmp_value(diffs, f"summary.{key}", actual.get(key), expected)


def _cmp_rows(diffs, label, actual, golden, fields):
    if len(actual) != len(golden):
        diffs.append(f"{label} length {len(actual)} != {len(golden)}")
        return
    for i, (got, exp) in enumerate(zip(actual, golden)):
        for field in fields:
            _cmp_value(diffs, f"{label}[{i}].{field}", got.get(field), exp.get(field))


def _cmp_ledger(diffs, actual, golden):
    if len(actual) != len(golden):
        diffs.append(f"ledger length {len(actual)} != {len(golden)}")
    for i, exp in enumerate(golden):
        if i >= len(actual):
            diffs.append(f"ledger[{i}] missing {exp.get('date')} {exp.get('kind')}")
            continue
        got = actual[i]
        for key, expected in exp.items():
            _cmp_value(diffs, f"ledger[{i}].{key}", got.get(key), expected)


def _cmp_campaigns(diffs, actual, golden):
    if len(actual) != len(golden):
        diffs.append(f"campaigns length {len(actual)} != {len(golden)}")
    for i, exp in enumerate(golden):
        if i >= len(actual):
            diffs.append(f"campaign[{i}] missing")
            continue
        got = actual[i]
        for key, expected in exp.items():
            if key == "buys":
                _cmp_buys(diffs, i, got.get("buys") or [], expected)
            else:
                _cmp_value(diffs, f"campaign[{i}].{key}", got.get(key), expected)


def _cmp_buys(diffs, index, actual, golden):
    if len(actual) != len(golden):
        diffs.append(f"campaign[{index}].buys length {len(actual)} != {len(golden)}")
        return
    for j, exp in enumerate(golden):
        for key, expected in exp.items():
            _cmp_value(diffs, f"campaign[{index}].buys[{j}].{key}", actual[j].get(key), expected)


def _cmp_value(diffs, label, got, expected):
    if _same(got, expected):
        return
    diffs.append(f"{label}: got {got!r} expected {expected!r}")


def _same(got, expected):
    if got is None or expected is None:
        return got is None and expected is None
    if isinstance(expected, str):
        return got == expected
    if isinstance(expected, bool):
        return got is expected
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        if isinstance(expected, float) and not float(expected).is_integer():
            scale = abs(expected) if expected else 1.0
            return abs(float(got) - float(expected)) <= 1e-6 * max(1.0, scale)
        return int(round(float(got))) == int(round(float(expected)))
    return got == expected


def causality_issues(actual, track, calendar, start=OCT_START):
    """신규 체결의 종가 신호 → 바로 다음 세션 시가. 9/30 신호를 새로 만들지 않는다."""
    nxt = {left: right for left, right in zip(calendar, calendar[1:])}
    issues = []
    for camp in actual["campaigns"]:
        for buy in camp["buys"]:
            if buy["date"] < start:
                continue
            if buy["signal_date"] < start:
                issues.append(f"{track} signal before window {buy['signal_date']} -> {buy['date']}")
            if buy["signal_date"] == "20260930":
                issues.append(f"{track} Sep30 signal used")
            if nxt.get(buy["signal_date"]) != buy["date"]:
                issues.append(
                    f"{track} not next session {buy['signal_date']} -> {buy['date']} expected {nxt.get(buy['signal_date'])}"
                )
    if track == "A":
        buys = [buy for camp in actual["campaigns"] for buy in camp["buys"]]
        if not buys or buys[0]["signal_date"] != OCT_START or buys[0]["date"] != "20261002":
            issues.append(f"A first fill is not Oct1 close -> Oct2 open: {buys[:1]}")
    return issues


def replay_official_fixture(path):
    """스키마로 기본 10만원 창과 과거 30만원 명시 파라미터 창을 가른다."""
    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    if blob.get("schema_version") == SCHEMA_LOSS_100K:
        return run_october_loss100k(path)
    return run_october_parity(path)


def pending_without_fixture(startDt=OCT_START, endDt=OCT_END):
    """픽스처가 없으면 손익 숫자를 만들지 않는다."""
    pending = {
        "ok": False,
        "status": "PARITY_PENDING",
        "passed": False,
        "summary": None,
        "stratId": "riseConfirmScale_v1",
        "errMsg": (
            "RCS01 공식 10/1~11/6 픽스처가 없다. "
            "실데이터 검증 통과로 보지 않는다. 분봉 엔진 손익을 쓰지 않는다."
        ),
    }
    if str(startDt or "") not in ("", OCT_START) or str(endDt or "") not in ("", OCT_END):
        pending["errMsg"] = "RCS01 리플레이 창은 20261001~20261106 고정이다."
    return pending


def run_october_parity(path):
    fixture = load_parity_fixture(path)
    report = {
        "status": "FAIL",
        "passed": False,
        "schema": SCHEMA,
        "path": str(path),
        "research_replay_auto_exit": True,
        "live_requires_loss_confirmation": True,
        "provenance_approved": False,
        "relabelled_synthetic": False,
        "tracks": {},
        "diffs": [],
        "causality": [],
    }
    try:
        for track in ("A", "B"):
            ran = replay_track(fixture, track, research_auto_exit=True)
            issues = causality_issues(ran["actual"], track, fixture["calendar"])
            report["tracks"][track] = {
                "diff_count": len(ran["diffs"]),
                "final_equity": ran["actual"]["final_equity"],
                "return_percent": ran["actual"]["return_percent"],
                "causality": issues,
            }
            report["diffs"].extend(f"{track} {row}" for row in ran["diffs"])
            report["causality"].extend(issues)
            report["tracks"][track]["rankings"] = ran["rankings"]
    except DataError as exc:
        report["status"] = "HALTED"
        report["error"] = str(exc)
        return report
    if report["diffs"] or report["causality"]:
        report["status"] = "FAIL"
        report["passed"] = False
    else:
        report["status"] = "PASS"
        report["passed"] = True
    return report


def run_october_loss100k(path):
    """기본 손절예산 10만원으로 10월 A를 재생한다. 스펙·설정 파일은 쓰지 않는다."""
    fixture = load_loss100k_fixture(path)
    report = {
        "status": "FAIL",
        "passed": False,
        "schema": SCHEMA_LOSS_100K,
        "loss_krw": 100_000,
        "default_loss_krw": DEFAULT_LOSS_KRW,
        "default_changed": False,
        "path": str(path),
        "research_replay_auto_exit": True,
        "live_loss_auto_exec": False,
        "provenance_approved": False,
        "relabelled_synthetic": False,
        "diffs": [],
        "causality": [],
        "pending": None,
        "final_equity": None,
        "return_percent": None,
    }
    try:
        ran = replay_track(fixture, "A", research_auto_exit=True)
    except DataError as exc:
        report["status"] = "HALTED"
        report["error"] = str(exc)
        return report
    actual = ran["actual"]
    diffs = list(ran["diffs"])
    issues = causality_issues(actual, "A", fixture["calendar"])
    pending = actual.get("pending") or {}
    if pending.get("reason") != "profit_ma20" or pending.get("signal_date") != OCT_END:
        diffs.append(f"terminal pending is not unfilled Nov6 profit_ma20: {pending}")
    if any(row.get("kind") == "sell" and row.get("date") == OCT_END for row in actual["ledger"]):
        diffs.append("Nov6 profit_ma20 was executed inside the window")
    isu = [row for row in actual["ledger"] if row.get("code") == "457190" and row.get("kind") in ("buy", "sell")]
    if [row.get("kind") for row in isu] != ["buy", "sell"] or len({row["date"] for row in isu}) != 1:
        diffs.append(f"ISU same-day buy then stop missing: {isu}")
    overshoots = [
        row.get("loss_overshoot")
        for row in actual["ledger"]
        if row.get("kind") == "sell"
    ]
    if not overshoots or any((value or 0) <= 0 for value in overshoots):
        diffs.append(f"100k stop was treated as a guaranteed cap: {overshoots}")
    report["diffs"] = diffs
    report["causality"] = issues
    report["pending"] = pending
    report["final_equity"] = actual["final_equity"]
    report["return_percent"] = actual["return_percent"]
    report["overshoots"] = overshoots
    report["passed"] = not diffs and not issues
    report["status"] = "PASS" if report["passed"] else "FAIL"
    return report
