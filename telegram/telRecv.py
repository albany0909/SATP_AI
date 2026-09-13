"""텔레그램 getUpdates 롱폴링으로 메시지 지속 수신"""

import json
import time

import requests

from config.config import telegram_chat_id, telegram_token
from telegram.cmdRouter import routeCommand
from telegram.commands.offCommand import isShutdownRequested
from telegram.telSend import sendTelegramMsg
from utils.termLog import termLog


def fetchUpdates(offset, timeoutSec=30):
    """
    Bot API getUpdates 호출.
    long polling(timeout)으로 새 업데이트가 올 때까지 대기한다.
    """
    url = f"https://api.telegram.org/bot{telegram_token}/getUpdates"
    params = {
        "offset": offset,
        "timeout": timeoutSec,
        # 배열은 JSON 문자열로 전달해야 Bot API가 올바르게 인식함
        "allowed_updates": json.dumps(["message"]),
    }
    response = requests.get(url, params=params, timeout=timeoutSec + 10)
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"getUpdates 실패: {body}")
    return body.get("result", [])


def skipPendingUpdates():
    """
    기동 직전 밀린 업데이트는 처리하지 않고 offset만 최신으로 맞춘다.
    (이전 실행의 off 등이 재시작 직후 다시 실행되는 것을 방지)
    """
    offset = 0
    while True:
        # timeout=0 — 대기 없이 현재 큐만 비움
        updates = fetchUpdates(offset, timeoutSec=0)
        if not updates:
            break
        offset = max(offset, max(u.get("update_id", 0) for u in updates) + 1)
    return offset


def confirmOffset(offset):
    """다음 getUpdates에 offset을 넘겨 서버에 수신 완료를 확정한다."""
    try:
        fetchUpdates(offset, timeoutSec=0)
    except Exception as err:
        termLog(f"오프셋 확정 실패(무시): {err}")


def handleMessage(msg):
    """
    단일 message 객체를 처리한다.
    설정된 chat_id와 다르면 무시하고, 명령 결과는 해당 채팅으로 응답한다.
    반환: 종료 요청 여부(bool)
    """
    chat = msg.get("chat") or {}
    chatId = str(chat.get("id", ""))
    text = msg.get("text") or ""

    # 본인 채팅만 처리 — 다른 사용자의 명령 실행 방지
    if telegram_chat_id and chatId != str(telegram_chat_id):
        return False

    resultMsg = routeCommand(text)
    if resultMsg:
        sendTelegramMsg(resultMsg, chatId=chatId)

    # off 명령이면 응답 전송 후 호출측에서 루프 종료
    return isShutdownRequested()


def startPolling():
    """
    텔레그램 메시지를 지속적으로 수신하는 메인 루프.
    offset을 갱신해 동일 업데이트를 중복 처리하지 않는다.
    """
    if not telegram_token:
        raise ValueError(
            "telegram_token이 비어 있습니다. 루트 .env 의 TELEGRAM_BOT_TOKEN 을 확인하세요."
        )

    # 재시작 시 이전 off 등 밀린 명령이 바로 실행되지 않도록 큐를 비움
    offset = skipPendingUpdates()
    termLog("텔레그램 수신 대기 중... (Ctrl+C 또는 off 명령으로 종료)")

    while True:
        try:
            updates = fetchUpdates(offset)
            shouldStop = False
            for upd in updates:
                updId = upd.get("update_id", 0)
                # 다음 폴링부터 이 업데이트는 제외
                offset = max(offset, updId + 1)

                msg = upd.get("message")
                if msg and handleMessage(msg):
                    shouldStop = True
                    break

            if shouldStop:
                # 종료 전 offset을 서버에 확정해, 다음 실행에서 off가 재처리되지 않게 함
                confirmOffset(offset)
                termLog("off 명령으로 수신 루프 종료")
                break
        except KeyboardInterrupt:
            termLog("수신 루프 종료")
            break
        except Exception as err:
            # 일시적 네트워크 오류 등은 잠시 대기 후 재시도
            termLog(f"수신 루프 오류: {err}")
            time.sleep(3)
