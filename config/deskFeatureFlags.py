"""매매창 탭 표시 플래그.

RAM01(실계좌)과 LDR01(주도주)만 기본으로 숨긴다.
settings.deskFeatureFlags 를 true 로 바꾸면 코드 삭제 없이 되돌린다.
계좌 인증, 공통 리스크 관문, 시세 구독은 끄지 않는다.
"""

_DEFAULT = {"RAM01": False, "LDR01": False}
_WATCH_TO_ALG = {"acnt": "RAM01", "leader": "LDR01", "rank20": "LDR01"}


def resolveDeskFeatureFlags(settings=None):
    if settings is None:
        from config.settingsStore import loadSettings

        settings = loadSettings() or {}
    flags = dict(_DEFAULT)
    raw = settings.get("deskFeatureFlags") if isinstance(settings, dict) else None
    if isinstance(raw, dict):
        for key in _DEFAULT:
            if key in raw:
                flags[key] = bool(raw[key])
    return flags


def isDeskTabEnabled(algOrTab, settings=None):
    key = _WATCH_TO_ALG.get(str(algOrTab or ""), str(algOrTab or ""))
    flags = resolveDeskFeatureFlags(settings)
    if key in flags:
        return bool(flags[key])
    return True


def watchTabSpecs(settings=None):
    tabs = []
    if isDeskTabEnabled("RAM01", settings):
        tabs.append(("acnt", "실계좌", "#CE93D8"))
    if isDeskTabEnabled("LDR01", settings):
        tabs.append(("leader", "주도주", "#FFCC80"))
    return tabs
