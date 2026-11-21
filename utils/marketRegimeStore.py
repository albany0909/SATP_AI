"""국면 판정 저장. 테이블이 없으면 jsonl만 남기고 DB는 만들지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

from utils.marketRegime import changeLine, judgmentLine
from utils.termLog import termLog

_TABLE = "AI_MKT_REGIME"


def defaultJsonlPath():
    from utils.appPaths import configDataDir

    return configDataDir() / "marketRegime.jsonl"


def appendJsonl(row, path):
    """같은 날짜가 이미 있으면 다시 붙이지 않는다. 기존 줄은 지우지 않는다."""
    filePath = Path(path)
    day = str((row or {}).get("tradeDt") or "")[:8]
    if len(day) != 8:
        return False
    if filePath.is_file():
        last = ""
        try:
            text = filePath.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.strip():
                    last = line
        except OSError:
            last = ""
        if last:
            try:
                prev = json.loads(last)
            except json.JSONDecodeError:
                prev = {}
            if str(prev.get("tradeDt") or "")[:8] == day:
                return False
    filePath.parent.mkdir(parents=True, exist_ok=True)
    with filePath.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def lastJsonl(path):
    filePath = Path(path)
    if not filePath.is_file():
        return None
    last = ""
    try:
        for line in filePath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = line
    except OSError:
        return None
    if not last:
        return None
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return None


def _insertDb(row):
    from utils.dbConn import openDbConn

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO AI_MKT_REGIME (
                  TRADE_DT, STK_CD, REGIME, REASON, CLOSE_PRC, SMA200,
                  ADX, PDI, MDI, VOL20, DD_PCT, DIST200, REG01
                ) VALUES (
                  %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    str(row.get("tradeDt") or "")[:8],
                    str(row.get("stkCd") or "069500")[:6],
                    str(row.get("regime") or ""),
                    str(row.get("reason") or "")[:200],
                    row.get("close"),
                    row.get("sma200"),
                    row.get("adx"),
                    row.get("plusDi"),
                    row.get("minusDi"),
                    row.get("vol20Ann"),
                    row.get("ddPct"),
                    row.get("dist200Pct"),
                    row.get("reg01"),
                ),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def recordJudgment(view, path=None, sender=None, useDb=True):
    """확정 판정을 하루 한 번 남긴다. 국면이 바뀌면 로그와 텔레그램을 보낸다."""
    if not view or not view.get("ok"):
        return {"saved": False, "changed": False}
    day = str(view.get("asOf") or view.get("tradeDt") or "")[:8]
    if len(day) != 8:
        return {"saved": False, "changed": False}
    filePath = Path(path) if path else defaultJsonlPath()
    prev = lastJsonl(filePath)
    if prev and str(prev.get("tradeDt") or "")[:8] == day:
        return {"saved": False, "changed": False, "reason": "already"}
    regime = str(view.get("regime") or "")
    reason = str(view.get("reason") or "")
    termLog(judgmentLine(regime, day, reason))
    changed = bool(prev) and str(prev.get("regime") or "") not in ("", regime)
    if changed:
        line = changeLine(prev.get("regime"), regime, day, reason)
        termLog(line)
        if sender is not None:
            try:
                sender(line)
            except Exception as err:
                termLog(f"[marketRegime] 텔레그램 실패: {err}")
    row = {
        "tradeDt": day,
        "stkCd": str(view.get("stkCd") or "069500"),
        "regime": regime,
        "reason": reason,
        "close": view.get("close"),
        "sma200": view.get("sma200"),
        "adx": view.get("adx"),
        "plusDi": view.get("plusDi"),
        "minusDi": view.get("minusDi"),
        "vol20Ann": view.get("vol20Ann"),
        "ddPct": view.get("ddPct"),
        "dist200Pct": view.get("dist200Pct"),
        "reg01": view.get("reg01"),
    }
    appendJsonl(row, filePath)
    if useDb:
        try:
            _insertDb(row)
        except Exception as err:
            text = str(err)
            if "1146" in text or "doesn't exist" in text.lower() or "존재하지" in text:
                termLog("[marketRegime] DB 테이블 없음 — jsonl에만 기록")
            else:
                termLog("[marketRegime] DB 저장 건너뜀 — jsonl에만 기록")
    return {"saved": True, "changed": changed}
