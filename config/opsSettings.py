"""운영 설정 — DB 배치 스케줄 on/off · 텔레그램 푸시 on/off"""

from config.settingsStore import loadSettings, saveSettings

# 기본값: 모두 ON (기존 동작 유지)
DEFAULT_BATCH_SCHED = {
    "usMorningLoad": True,
    "watchNewsDisc": True,
    "dayInqRank": True,
    "ntlFirst": True,
    "dayInvSr": True,
    "rankScore": True,
    # 16:00 순위20 ∪ 거래대금30 → 코스피/코스닥을 다음 거래일 이평터치 관리종목
    "maTouchUniv": True,
    # 거래일 16:05 알고리즘 당일 복기 (이벤트·청산·손익 스냅)
    "algDayReview": True,
    # 순위 종목 1년 일봉 — 자동은 dayInqRank 뒤에 붙어 돌고, 별도 수동 실행용
    "rankDayPrc": True,
    "watchDayPrc": True,
    # 장중값으로 굳은 일봉 정정 — 수동 실행 전용 (SMA 오염 복구)
    "prcRefix": True,
}

DEFAULT_TELEGRAM_PUSH = {
    "master": True,
    "boot": True,
    "batch": False,
    "wnews": False,
    "ordExec": False,
    "closePnl": False,
    "cnsr": False,
    "rsv": False,
    "stls": False,
    "tokenRenew": False,
    "btVal": False,
    "dayReview": False,
}

# 푸시 채널 목록 (화면 표시 순서). master는 맨 위.
PUSH_CHANNEL_DEFS = [
    {
        "id": "master",
        "label": "전체 푸시",
        "desc": "꺼지면 모든 알림성 푸시 차단. 켜도 아래 세부 채널 설정은 그대로 유지됩니다. 텔레그램 명령 응답은 그대로 전송됩니다.",
    },
    {
        "id": "boot",
        "label": "기동·로그인",
        "desc": "프로그램 시작, 자동 로그인 결과",
    },
    {
        "id": "batch",
        "label": "배치 적재 결과",
        "desc": "순위·신고가·수급·미국순위 등 스케줄 적재 완료/실패",
    },
    {
        "id": "wnews",
        "label": "관심 공시·뉴스",
        "desc": "관심종목 DART 공시·네이버 뉴스 신규",
    },
    {
        "id": "ordExec",
        "label": "주문 체결",
        "desc": "실시간 매수·매도 체결 알림 (기본 꺼짐 — 구독·매매는 푸시하지 않음)",
    },
    {
        "id": "closePnl",
        "label": "전량청산 손익",
        "desc": "이평터치 최종 청산 시 승패·수익률·손익금액만",
    },
    {
        "id": "cnsr",
        "label": "조건검색",
        "desc": "조건식 편입·이탈 실시간",
    },
    {
        "id": "rsv",
        "label": "예약 실행",
        "desc": "예약 명령 실행 알림·결과",
    },
    {
        "id": "stls",
        "label": "스탑로스",
        "desc": "익절·손절 발동 알림",
    },
    {
        "id": "tokenRenew",
        "label": "토큰 갱신",
        "desc": "지정 시각·만료 임박 토큰 재발급 결과",
    },
    {
        "id": "btVal",
        "label": "백테스트·검증",
        "desc": "bt/val 장시간 작업 진행·완료 (명령 응답과 별도)",
    },
    {
        "id": "dayReview",
        "label": "장마감 알고리즘 복기",
        "desc": "거래일 16:05 당일 승률·손익·문제점·개선힌트 요약",
    },
]


def _bool(raw, default=True):
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("1", "y", "yes", "true", "on"):
        return True
    if text in ("0", "n", "no", "false", "off"):
        return False
    return default


def getBatchSchedMap():
    """jobId → enabled. 누락 키는 디폴트 ON."""
    settings = loadSettings()
    stored = settings.get("batchSched") or {}
    if not isinstance(stored, dict):
        stored = {}
    out = dict(DEFAULT_BATCH_SCHED)
    for k, v in stored.items():
        out[str(k)] = _bool(v, True)
    return out


def isBatchSchedEnabled(jobId):
    """배치 스케줄 실행 여부. 미등록 id는 True."""
    if not jobId:
        return True
    return bool(getBatchSchedMap().get(str(jobId), True))


def setBatchSchedEnabled(jobId, enabled):
    """단일 배치 on/off 저장."""
    jobId = str(jobId or "").strip()
    if jobId not in DEFAULT_BATCH_SCHED:
        return {"ok": False, "errMsg": f"알 수 없는 배치 id: {jobId}"}
    settings = loadSettings()
    batch = dict(settings.get("batchSched") or {})
    if not isinstance(batch, dict):
        batch = {}
    batch[jobId] = bool(enabled)
    from config.settingsStore import patchSettings

    patchSettings({"batchSched": batch})
    return {"ok": True, "jobId": jobId, "enabled": bool(enabled)}


def getTelegramPushMap():
    """channelId → enabled."""
    settings = loadSettings()
    stored = settings.get("telegramPush") or {}
    if not isinstance(stored, dict):
        stored = {}
    out = dict(DEFAULT_TELEGRAM_PUSH)
    for k, v in stored.items():
        out[str(k)] = _bool(v, True)
    return out


def isTelegramPushEnabled(pushKind):
    """
    알림성 푸시 전송 여부.
    master OFF 이면 모든 채널 False.
    pushKind 없거나 미등록이면 True (명령 응답은 이 함수를 타지 않음).
    """
    mp = getTelegramPushMap()
    if not mp.get("master", True):
        return False
    if not pushKind:
        return True
    return bool(mp.get(str(pushKind), True))


def setTelegramPushEnabled(channelId, enabled):
    """
    푸시 채널 on/off.
    master 는 게이트만 바꾼다(세부 채널 값은 덮지 않음).
    저장은 settings.json 에 즉시 반영된다.
    """
    channelId = str(channelId or "").strip()
    if channelId not in DEFAULT_TELEGRAM_PUSH:
        return {"ok": False, "errMsg": f"알 수 없는 푸시 id: {channelId}"}
    settings = loadSettings()
    # 파일에 저장된 값만 기준으로 덮어써서, 로드 시 merge 된 디폴트가
    # 의도치 않게 다시 쓰이지 않게 한다.
    raw = _readStoredTelegramPush()
    push = dict(raw)
    on = bool(enabled)
    push[channelId] = on
    # 알려진 키는 모두 명시 저장(누락 키 → 다음 로드 때 디폴트로 되살아나는 것 방지)
    for cid in DEFAULT_TELEGRAM_PUSH:
        if cid not in push:
            push[cid] = bool(DEFAULT_TELEGRAM_PUSH[cid])
    from config.settingsStore import patchSettings

    patchSettings({"telegramPush": push})
    return {"ok": True, "channelId": channelId, "enabled": on}


def _readStoredTelegramPush():
    """settings.json 의 telegramPush 만 읽는다(디폴트 merge 없이)."""
    try:
        from config.settingsStore import _SETTINGS_PATH, _readJson
        data = _readJson(_SETTINGS_PATH) or {}
        stored = data.get("telegramPush") or {}
        if isinstance(stored, dict):
            return {str(k): bool(v) for k, v in stored.items()}
    except Exception:
        pass
    return {}


def listTelegramPushChannels():
    """웹/API용 채널 목록 (설정 반영)."""
    mp = getTelegramPushMap()
    items = []
    for d in PUSH_CHANNEL_DEFS:
        cid = d["id"]
        items.append(
            {
                "id": cid,
                "label": d["label"],
                "desc": d["desc"],
                "enabled": bool(mp.get(cid, True)),
            }
        )
    return items
