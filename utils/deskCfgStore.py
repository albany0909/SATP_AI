"""AI_DESK_CFG — 매매창 설정을 DB에 동기 (톰캣 조회용)."""

import json
from pathlib import Path

from utils.dbConn import openDbConn
from utils.termLog import termLog

_SQL = Path(__file__).resolve().parents[1] / "sql" / "deskCfg.sql"
_KEY_SETTINGS = "settings"
_tableOk = False


def ensureDeskCfgTable():
    global _tableOk
    if _tableOk:
        return True
    try:
        from utils.dbConn import isLocalDbEnabled

        if not isLocalDbEnabled():
            return False
    except Exception:
        pass
    try:
        sql = _SQL.read_text(encoding="utf-8")
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                for stmt in sql.split(";"):
                    s = stmt.strip()
                    if s:
                        cur.execute(s)
            conn.commit()
            _tableOk = True
            return True
        finally:
            conn.close()
    except Exception as err:
        try:
            from utils.dbConn import DbDisabledError, DbDownError

            if isinstance(err, (DbDisabledError, DbDownError)):
                return False
        except Exception:
            pass
        termLog(f"[deskCfg] 테이블 생성 실패: {err}")
        return False


def upsertDeskSettings(settings: dict):
    """설정 dict 를 AI_DESK_CFG 에 저장. 실패해도 호출측을 막지 않는다."""
    if not isinstance(settings, dict):
        return False
    if not ensureDeskCfgTable():
        return False
    try:
        raw = json.dumps(settings, ensure_ascii=False)
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO AI_DESK_CFG (CFG_KEY, CFG_JSON)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE CFG_JSON=VALUES(CFG_JSON)
                    """,
                    (_KEY_SETTINGS, raw),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as err:
        termLog(f"[deskCfg] 설정 UPSERT 실패: {err}")
        return False


def loadDeskSettingsFromDb():
    """DB 설정 dict. 없으면 None."""
    if not ensureDeskCfgTable():
        return None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CFG_JSON FROM AI_DESK_CFG WHERE CFG_KEY=%s LIMIT 1",
                (_KEY_SETTINGS,),
            )
            row = cur.fetchone()
        if not row:
            return None
        raw = row.get("CFG_JSON") if isinstance(row, dict) else row[0]
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else None
    except Exception as err:
        termLog(f"[deskCfg] 설정 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
