"""RCS01 상승확인분할매매 — 앱 판단 경로.

연구 기준선과 같은 예산·체결·손절 규칙의 독립 구현이다.
reference engine 모듈을 호출하지 않는다.

- research_auto_exit=True: 연구 리플레이. 손실 매도도 모형가에 자동 체결.
- research_auto_exit=False: 라이브. 손실 매도는 예상손실·사유를 남기고
  명시 확인 전에는 체결하지 않는다.

10월 공식 픽스처는 strategies.impl.riseConfirmReplay 가 격리해서 읽는다.
이 모듈은 그 입력을 synthetic 으로 바꾸거나 출처 승인을 만들지 않는다.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR

ALG_CD = "RCS01"
STRAT_ID = "riseConfirmScale_v1"
NAME_EN = "Rise Confirmation Scale Trading"

ALLOCATIONS = (4_000_000, 3_000_000, 2_000_000, 1_000_000)
# 캠페인 손실 예산 기본값. 보장 상한이 아니다. 과거 30만원 픽스처는 명시 파라미터다.
LOSS_BUDGET = 100_000
BUY_FEE = 0.00015
SELL_FEE = 0.00015
SELL_TAX = 0.002


class DataError(ValueError):
    pass


def floor_money(amount, rate):
    """원화 수수료·세금. 원 미만 버림."""
    won = (Decimal(str(amount)) * Decimal(str(rate))).to_integral_value(rounding=ROUND_FLOOR)
    return int(won)


def tick_size(price):
    for end, size in (
        (2000, 1),
        (5000, 5),
        (20000, 10),
        (50000, 50),
        (200000, 100),
        (500000, 500),
    ):
        if price < end:
            return size
    return 1000


def ceil_tick(price):
    if price <= 0:
        return 1
    size = tick_size(price)
    return math.ceil(price / size - 1e-12) * size


def whole_money(value):
    if isinstance(value, bool):
        raise DataError("금액 플래그는 허용하지 않는다")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise DataError(f"정수 원화가 아니다: {value}")


class RiseConfirmBook:
    """한 계좌·한 캠페인. 판단과 모형 체결만 하고 주문을 보내지 않는다."""

    def __init__(self, market, config, seed, research_auto_exit=True):
        self.market = market
        self.calendar = list(market["calendar"])
        self.cal_ix = {day: i for i, day in enumerate(self.calendar)}
        self.start = str(config["start"])
        self.end = str(config["end"])
        self.allocations = tuple(whole_money(x) for x in config["allocations"])
        self.loss = whole_money(config.get("loss", LOSS_BUDGET))
        self.buy_fee = float(config["buy_fee"])
        self.sell_fee = float(config["sell_fee"])
        self.sell_tax = float(config["sell_tax"])
        self.slippage_ticks = int(config["slippage_ticks"])
        self.stop_mode = str(config["stop_mode"])
        self.exit_mode = str(config["exit_mode"])
        self.ignore_dividends = bool(config.get("ignore_dividends", True))
        self.research_auto_exit = bool(research_auto_exit)
        if self.research_auto_exit and self.stop_mode not in ("intraday", "close"):
            raise DataError("알 수 없는 stop_mode")
        if self.exit_mode not in ("bearish_cross", "any_below20"):
            raise DataError("알 수 없는 exit_mode")
        if sum(self.allocations) > 10_000_000:
            raise DataError("캠페인 배정 합이 1천만원을 넘는다")
        self.confirms = []
        self.ledger = []
        self.history = []
        self.pending = None
        self._load_seed(seed)
        self.opening_cash = self.cash
        self.opening_qty = self.active["q"] if self.active else 0
        self.opening_cost = self.active["cost"] if self.active else 0
        self.opening_code = self.active["code"] if self.active else None
        self.intents = set()
        self.loss_hold_day = None

    def _load_seed(self, seed):
        self.seed_asof = str(seed.get("asof") or "")
        self.cash = whole_money(seed.get("cash"))
        self.prior_campaign_count = int(seed.get("prior_campaign_count") or 0)
        self.realized_seed = whole_money(seed.get("realized") or 0)
        self.period_realized = 0
        active = seed.get("active")
        self.campaigns = []
        self.active = None
        if active:
            pos = self._copy_position(active)
            self.active = pos
            self.campaigns.append(pos)
        if seed.get("pending"):
            raise DataError("시드 미체결 주문은 이 리플레이가 지원하지 않는다")
        tag_date, _, tag = self.seed_asof.partition("_")
        self.boundary_date = tag_date if tag == "close" else None

    def _copy_position(self, raw):
        buys = []
        for buy in raw.get("buys") or []:
            buys.append(
                {
                    "date": str(buy["date"]),
                    "signal_date": str(buy["signal_date"]),
                    "price": whole_money(buy["price"]),
                    "q": whole_money(buy["q"]),
                    "cost": whole_money(buy["cost"]),
                    "stage": int(buy["stage"]),
                    "allocation": whole_money(buy["allocation"]),
                }
            )
        return {
            "id": int(raw["id"]),
            "code": str(raw["code"]),
            "name": str(raw.get("name") or self.market["stocks"][raw["code"]]["name"]),
            "entry": str(raw["entry"]),
            "entry_signal": str(raw["entry_signal"]),
            "q": whole_money(raw["q"]),
            "cost": whole_money(raw["cost"]),
            "notional": whole_money(raw["notional"]),
            "last_buy": whole_money(raw["last_buy"]),
            "stop": whole_money(raw["stop"]),
            "buys": buys,
            "status": str(raw.get("status") or "held"),
        }

    def bar(self, code, day):
        return self.market["stocks"][code]["bars"].get(day)

    def fee_rate_sell(self):
        return self.sell_fee + self.sell_tax

    def proceeds(self, qty, price):
        gross = qty * price
        return gross - floor_money(gross, self.sell_fee) - floor_money(gross, self.sell_tax)

    def buy_cost(self, qty, price):
        gross = qty * price
        return gross + floor_money(gross, self.buy_fee)

    def protective_stop(self, pos):
        raw = (pos["cost"] - self.loss) / (pos["q"] * (1 - self.fee_rate_sell()))
        price = ceil_tick(raw)
        while self.proceeds(pos["q"], price) - pos["cost"] < -self.loss:
            price = ceil_tick(price + tick_size(price))
        return max(pos.get("stop") or 0, price)

    def modeled_fill(self, raw, bar, side):
        slipped = raw + tick_size(raw) * self.slippage_ticks * (1 if side == "buy" else -1)
        return max(bar["low"], min(bar["high"], slipped))

    def signal_prices(self, code, day):
        stock = self.market["stocks"][code]
        actions = stock.get("actions") or []
        end = self.cal_ix[day]
        out = []
        for sess in self.calendar[: end + 1]:
            bar = stock["bars"].get(sess)
            if not bar:
                out.append(None)
                continue
            ratio = 1.0
            mult = 1.0
            for action in actions:
                when = str(action["date"])
                if not (sess < when <= day):
                    continue
                if action.get("kind") == "split":
                    ratio *= float(action["ratio"])
                    continue
                adj = action.get("signal_adjustment") or {}
                if adj.get("verified") is True and adj.get("historical_price_multiplier"):
                    mult *= float(adj["historical_price_multiplier"])
            if bar.get("suspended"):
                close = bar["mark_close"] / ratio * mult
                out.append({"open": 0.0, "high": 0.0, "low": 0.0, "close": close})
            else:
                out.append({k: bar[k] / ratio * mult for k in ("open", "high", "low", "close")})
        return out

    def indicators(self, code, day):
        prices = self.signal_prices(code, day)
        if len(prices) < 80 or any(row is None for row in prices[-80:]):
            return None
        close = [row["close"] for row in prices[-80:]]
        return {
            "ma60": sum(close[-60:]) / 60,
            "oldma60": sum(close[-80:-20]) / 60,
            "ma20": sum(close[-20:]) / 20,
            "prevma20": sum(close[-21:-1]) / 20,
            "prevclose": close[-2],
            "ret60": close[-1] / close[-61] - 1,
            "high20": max(row["high"] for row in prices[-21:-1]),
        }

    def rankings(self, day):
        """보유와 무관한 당일 자격 순위. (60일수익, 20일대금, 코드) 내림·코드 오름."""
        ix = self.cal_ix[day]
        index_ret = self.market["index"][day] / self.market["index"][self.calendar[ix - 60]] - 1
        found = []
        members = self.market["universe"][day]["codes"]
        window = self.calendar[ix - 19 : ix + 1]
        for code in members:
            bar = self.bar(code, day)
            if not bar or bar.get("suspended") or bar.get("managed") or not bar.get("can_buy"):
                continue
            ind = self.indicators(code, day)
            if not ind:
                continue
            bars = [self.bar(code, sess) for sess in window]
            if any(row is None for row in bars):
                continue
            turn = sum(row["turnover"] for row in bars) / 20
            raw_close = bar["close"]
            if (
                turn >= 10_000_000_000
                and raw_close > ind["ma60"]
                and ind["ma60"] > ind["oldma60"]
                and ind["ret60"] > index_ret
                and raw_close > ind["high20"]
            ):
                found.append((ind["ret60"], turn, code))
        found.sort(key=lambda row: (-row[0], -row[1], row[2]))
        return found

    def choose(self, day):
        ranked = self.rankings(day)
        return ranked[0][2] if ranked else None

    def log(self, day, kind, **fields):
        self.ledger.append({"date": day, "kind": kind, **fields})

    def _assert_held_rights(self, day):
        if not self.active:
            return
        code = self.active["code"]
        entry = self.active["entry"]
        for action in self.market["stocks"][code].get("actions") or []:
            when = str(action["date"])
            if not (entry < when <= day):
                continue
            kind = action.get("kind")
            if kind == "split":
                ratio = float(action.get("ratio") or 0)
                if ratio < 1 or not ratio.is_integer():
                    raise DataError(
                        f"보유 중 비정수·역분할은 처리하지 않는다 {code} {when}"
                    )
                continue
            if kind == "cash_dividend" and self.ignore_dividends:
                continue
            raise DataError(
                f"보유 중 권리 처리 불명으로 중단 {code} {when} {kind}; 미래 종목 제외는 하지 않는다"
            )

    def _apply_splits(self, day):
        if not self.active:
            return
        code = self.active["code"]
        for action in self.market["stocks"][code].get("actions") or []:
            if action.get("kind") != "split" or str(action["date"]) != day:
                continue
            ratio = float(action["ratio"])
            new_qty = self.active["q"] * ratio
            if abs(new_qty - round(new_qty)) > 1e-8:
                raise DataError(f"분할 단수주 {code} {day}")
            self.active["q"] = int(round(new_qty))
            self.active["last_buy"] = self.active["last_buy"] / ratio
            self.active["stop"] = ceil_tick(self.active["stop"] / ratio)
            self.log(day, "split", code=code, ratio=ratio, quantity=self.active["q"])
            if self.pending and self.pending.get("code") == code:
                self.pending["signal_close"] = self.pending["signal_close"] / ratio

    def _sell(self, day, raw, reason, signal_date=None):
        pos = self.active
        bar = self.bar(pos["code"], day)
        if bar.get("suspended") or not bar.get("can_sell"):
            self.pending = {
                "kind": "sell",
                "code": pos["code"],
                "signal_date": signal_date or day,
                "signal_close": bar["mark_close"],
                "reason": reason,
            }
            self.log(day, "sale_unfilled", code=pos["code"], reason=reason)
            return False
        price = whole_money(self.modeled_fill(raw, bar, "sell"))
        net = self.proceeds(pos["q"], price)
        profit = net - pos["cost"]
        if not self.research_auto_exit and profit < 0:
            notice_key = ("loss", pos["id"], reason, day)
            if notice_key not in self.intents:
                self.intents.add(notice_key)
                self.confirms.append(
                    {
                        "account_scope": "book",
                        "date": day,
                        "code": pos["code"],
                        "campaign": pos["id"],
                        "reason": reason,
                        "expected_profit": profit,
                        "expected_loss": -profit,
                        "modeled_price": price,
                        "signal_date": signal_date or day,
                        "standing": True,
                        "confirmed": False,
                    }
                )
                self.log(
                    day,
                    "sell_needs_confirm",
                    code=pos["code"],
                    reason=reason,
                    price=price,
                    profit=profit,
                    campaign=pos["id"],
                )
            self.loss_hold_day = day
            return False
        fill_key = ("sell", pos["id"], reason, day, pos["q"])
        if fill_key in self.intents:
            return False
        self.intents.add(fill_key)
        self.cash += net
        self.period_realized += profit
        pos.update(
            exit=day,
            exit_price=price,
            exit_reason=reason,
            profit=profit,
            return_on_cost=profit / pos["cost"] * 100,
            status="closed",
            calendar_days=self._elapsed_days(pos["entry"], day),
            sessions=self._sessions(pos["entry"], day),
        )
        overshoot = max(0, -profit - self.loss)
        self.log(
            day,
            "sell",
            code=pos["code"],
            quantity=pos["q"],
            price=price,
            proceeds=net,
            profit=profit,
            reason=reason,
            campaign=pos["id"],
            stop_price=pos["stop"],
            campaign_cost=pos["cost"],
            loss_budget=self.loss,
            loss_overshoot=overshoot,
            execution_model="daily_OHLC_with_adverse_tick",
        )
        self.active = None
        self.pending = None
        return True

    def _buy(self, day, order):
        code = order["code"]
        bar = self.bar(code, day)
        if bar.get("suspended") or bar.get("managed") or not bar.get("can_buy"):
            self.log(day, "buy_cancel", code=code, reason="not_executable")
            return False
        in_index = code in self.market["universe"][day]["codes"]
        if not in_index and self.active is None:
            self.log(day, "buy_cancel", code=code, reason="no_longer_member")
            return False
        price = whole_money(self.modeled_fill(bar["open"], bar, "buy"))
        pos = self.active
        stage = 0 if pos is None else len(pos["buys"])
        if stage >= 4:
            return False
        budget = self.allocations[stage]
        if pos is not None:
            average = pos["notional"] / pos["q"]
            gap_cap = order["signal_close"] * 1.03
            if price < average * 1.05 or price < pos["last_buy"] * 1.05 or price > gap_cap:
                self.log(day, "buy_cancel", code=code, reason="add_recheck_or_gap")
                return False
        if self.cash < budget:
            self.log(day, "buy_cancel", code=code, reason="insufficient_cash_for_stage")
            return False
        qty = int(budget // (price * (1 + self.buy_fee)))
        while qty and self.buy_cost(qty, price) > budget:
            qty -= 1
        while self.buy_cost(qty + 1, price) <= budget:
            qty += 1
        if not qty:
            return False
        cost = self.buy_cost(qty, price)
        if pos is None:
            pos = {
                "id": self.prior_campaign_count + len(self.campaigns) + 1,
                "code": code,
                "name": self.market["stocks"][code]["name"],
                "entry": day,
                "entry_signal": order["signal_date"],
                "q": 0,
                "cost": 0,
                "notional": 0,
                "stop": 0,
                "last_buy": 0,
                "buys": [],
                "status": "held",
            }
        if pos["cost"] + cost > sum(self.allocations):
            raise DataError("캠페인 1천만원 한도를 넘는다")
        proposed = {
            "q": pos["q"] + qty,
            "cost": pos["cost"] + cost,
            "notional": pos["notional"] + qty * price,
            "last_buy": price,
            "stop": pos.get("stop") or 0,
        }
        proposed["stop"] = self.protective_stop(
            {"q": proposed["q"], "cost": proposed["cost"], "stop": pos.get("stop") or 0}
        )
        if stage and proposed["stop"] >= bar["open"]:
            self.log(day, "buy_cancel", code=code, reason="post_add_stop_at_or_above_open")
            return False
        fill_key = ("buy", code, order["signal_date"], stage)
        if fill_key in self.intents:
            return False
        self.intents.add(fill_key)
        pos["q"] = proposed["q"]
        pos["cost"] = proposed["cost"]
        pos["notional"] = proposed["notional"]
        pos["last_buy"] = price
        pos["stop"] = proposed["stop"]
        pos["buys"].append(
            {
                "date": day,
                "signal_date": order["signal_date"],
                "price": price,
                "q": qty,
                "cost": cost,
                "stage": stage,
                "allocation": budget,
            }
        )
        self.cash -= cost
        self.active = pos
        if stage == 0:
            self.campaigns.append(pos)
        self.log(
            day,
            "buy",
            code=code,
            quantity=qty,
            price=price,
            cost=cost,
            stage=stage,
            stop=pos["stop"],
            campaign=pos["id"],
        )
        return True

    def _close_signals(self, day, traded_sell):
        if self.active and not self.pending:
            pos = self.active
            bar = self.bar(pos["code"], day)
            if not bar.get("suspended"):
                net = self.proceeds(pos["q"], bar["close"]) - pos["cost"]
                ind = self.indicators(pos["code"], day)
                bearish = bar["close"] < bar["open"] and ind and ind["prevclose"] >= ind["prevma20"]
                profit_exit = (
                    ind
                    and net > 0
                    and bar["close"] < ind["ma20"]
                    and (self.exit_mode == "any_below20" or bearish)
                )
                if self.stop_mode == "close" and bar["close"] <= pos["stop"]:
                    self.pending = {
                        "kind": "sell",
                        "code": pos["code"],
                        "signal_date": day,
                        "signal_close": bar["close"],
                        "reason": "stop_close",
                    }
                elif profit_exit:
                    self.pending = {
                        "kind": "sell",
                        "code": pos["code"],
                        "signal_date": day,
                        "signal_close": bar["close"],
                        "reason": "profit_ma20",
                    }
                elif (
                    not traded_sell
                    and len(pos["buys"]) < 4
                    and bar["close"] >= pos["notional"] / pos["q"] * 1.05
                    and bar["close"] >= pos["last_buy"] * 1.05
                ):
                    self.pending = {
                        "kind": "buy",
                        "code": pos["code"],
                        "signal_date": day,
                        "signal_close": bar["close"],
                    }
        if self.active is None and not self.pending:
            code = self.choose(day)
            if code:
                self.pending = {
                    "kind": "buy",
                    "code": code,
                    "signal_date": day,
                    "signal_close": self.bar(code, day)["close"],
                }

    def step(self, day):
        if day not in self.market["universe"]:
            raise DataError(f"검증 구간 편입 종목이 없다 {day}")
        self._assert_held_rights(day)
        self._apply_splits(day)
        traded_sell = False
        order = self.pending
        self.pending = None
        if order and order["kind"] == "sell":
            bar = self.bar(order["code"], day)
            raw = bar.get("open") or bar["mark_close"]
            traded_sell = self._sell(day, raw, order["reason"], order.get("signal_date"))
        if self.active and not self.pending and self.stop_mode == "intraday":
            pos = self.active
            bar = self.bar(pos["code"], day)
            if not bar.get("suspended") and bar["open"] <= pos["stop"]:
                traded_sell = self._sell(day, bar["open"], "stop_gap", day) or traded_sell
        if order and order["kind"] == "buy" and not traded_sell and not self.pending:
            if self.active is None or self.active["code"] == order["code"]:
                self._buy(day, order)
        if self.active and not self.pending and self.loss_hold_day != day and self.stop_mode == "intraday":
            pos = self.active
            bar = self.bar(pos["code"], day)
            if not bar.get("suspended") and bar["low"] <= pos["stop"]:
                raw = min(bar["open"], pos["stop"])
                traded_sell = self._sell(day, raw, "stop_intraday", day) or traded_sell
        self._close_signals(day, traded_sell)
        self._mark(day)

    def _mark(self, day):
        market_value = 0
        liquidation = 0
        used = 0
        stop = None
        qty = 0
        if self.active:
            pos = self.active
            mark = self.bar(pos["code"], day)["mark_close"]
            market_value = pos["q"] * mark
            liquidation = self.proceeds(pos["q"], mark)
            used = pos["cost"]
            stop = pos["stop"]
            qty = pos["q"]
        self.history.append(
            {
                "date": day,
                "cash": self.cash,
                "market_value": market_value,
                "equity": self.cash + market_value,
                "liquidation_equity": self.cash + liquidation,
                "cost_exposure": used,
                "stop_price": stop,
                "quantity": qty,
            }
        )

    def run(self):
        days = [day for day in self.calendar if self.start <= day <= self.end]
        if not days or days[0] != self.start or days[-1] != self.end:
            raise DataError("시작·종료 세션이 달력에 없다")
        if self.cal_ix[self.start] < 79:
            raise DataError("MA60 비교에 필요한 80세션이 없다")
        for day in days:
            self.step(day)
        self._finish_open()
        return self

    def _finish_open(self):
        if not self.active:
            return
        pos = self.active
        pos["end"] = self.end
        mark = self.bar(pos["code"], self.end)["mark_close"]
        profit = self.proceeds(pos["q"], mark) - pos["cost"]
        pos["profit"] = profit
        pos["return_on_cost"] = profit / pos["cost"] * 100
        pos["calendar_days"] = self._elapsed_days(pos["entry"], self.end)
        pos["sessions"] = self._sessions(pos["entry"], self.end)

    def _elapsed_days(self, start, end):
        from datetime import datetime

        return (datetime.strptime(end, "%Y%m%d") - datetime.strptime(start, "%Y%m%d")).days

    def _sessions(self, start, end):
        if start not in self.cal_ix or end not in self.cal_ix:
            raise DataError(f"세션 경과일 계산 불가 {start}..{end}")
        return self.cal_ix[end] - self.cal_ix[start] + 1

    def boundary_equity(self):
        """구간 시작 직전 평가. A는 현금 1천만원, B는 경계일 종가 평가."""
        if self.boundary_date and self.opening_code:
            mark = self.bar(self.opening_code, self.boundary_date)["mark_close"]
            market_value = self.opening_qty * mark
            equity = self.opening_cash + market_value
            return {
                "date": self.boundary_date,
                "cash": self.opening_cash,
                "market_value": market_value,
                "equity": equity,
                "cost": self.opening_cost,
                "quantity": self.opening_qty,
                "gross_unrealized": market_value - self.opening_cost,
            }
        return {
            "date": self.start,
            "cash": self.opening_cash,
            "market_value": 0,
            "equity": self.opening_cash,
            "cost": 0,
            "quantity": 0,
            "gross_unrealized": 0,
        }
