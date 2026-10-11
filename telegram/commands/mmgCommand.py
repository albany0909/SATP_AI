"""mmg — MMG01 수동정찰 피라미딩 풀·상태

mmg list            풀·보유 현황
mmg add 코드|이름   풀 등록 (사용자, 만료 없음)
mmg del 코드|이름   풀 해제
mmg adopt 코드      계좌 보유분을 평단 그대로 편입
mmg scan            스크리너 즉시 실행 (백그라운드)
mmg status          설정·보유 요약
"""

import threading

from utils.stkCdUtil import normStkCd


def _usage():
    return (
        "사용법:\n"
        "mmg list — 풀·보유\n"
        "mmg add {코드|이름} — 풀 등록\n"
        "mmg del {코드|이름} — 풀 해제\n"
        "mmg adopt {코드} — 계좌 보유분 편입\n"
        "mmg scan — 스크리너 실행\n"
        "mmg status — 상태"
    )


def _resolveCd(ref):
    cd = normStkCd(ref)
    if cd and cd.isdigit() and len(cd) == 6:
        return cd
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT STK_CD FROM AI_STK_INFO WHERE STK_NM=%s LIMIT 1", (str(ref).strip(),))
                row = cur.fetchone()
                if row:
                    return normStkCd(row.get("STK_CD") if isinstance(row, dict) else row[0])
        finally:
            conn.close()
    except Exception:
        pass
    return ""


def handleMmg(rawParts):
    from trading import manualMgrLive as mm

    sub = rawParts[1].lower() if len(rawParts) >= 2 else ""
    arg = " ".join(rawParts[2:]).strip()
    if sub in ("", "help"):
        return _usage()
    if sub in ("list", "ls"):
        pool = mm.listPool(force=True)
        pos = mm.listMmgPositions()
        lines = [f"MMG01 풀 {len(pool)}종목 · 보유 {len(pos)}"]
        for cd, v in sorted(pool.items(), key=lambda kv: kv[1]["src"]):
            tag = "보유" if cd in pos else ""
            exp = f" ~{v['expYmd']}" if v.get("expYmd") else ""
            lines.append(f"- {v.get('nm') or cd}({cd}) {v['src']}{exp} {tag}".rstrip())
        return "\n".join(lines)
    if sub == "status":
        return mm.statusText()
    if sub == "scan":
        threading.Thread(target=mm._safeScreen, name="mmgScreenCmd", daemon=True).start()
        return "MMG01 스크리너 시작 (수십 분 걸릴 수 있음, 완료 시 알림)"
    if sub in ("add", "del", "adopt"):
        if not arg:
            return _usage()
        cd = _resolveCd(arg)
        if not cd:
            return f"종목을 찾지 못했습니다: {arg}"
        if sub == "add":
            ok, msg = mm.addPool(cd, src="USER", why="telegram")
            return f"MMG01 풀 등록: {msg}" if ok else f"등록 실패: {msg}"
        if sub == "del":
            if mm.holdsMmg(cd):
                return f"{cd} 는 MMG01 보유 중 — 청산 후 해제하세요"
            mm.delPool(cd)
            return f"MMG01 풀 해제: {cd}"
        ok, msg = mm.adoptFromAccount(cd)
        return f"MMG01 편입: {msg}" if ok else f"편입 실패: {msg}"
    return _usage()
