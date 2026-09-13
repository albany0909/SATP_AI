"""텔레그램으로 메시지 전송 모듈"""

import requests

from config.config import telegram_chat_id, telegram_token


def sendTelegramMsg(msg, chatId=None, telegramToken=None, pushKind=None):
    """
    주어진 메시지를 텔레그램 채팅방으로 전송한다.
    chatId/telegramToken을 생략하면 config.py 값을 사용한다.

    pushKind: 알림성 푸시 채널 id (boot/batch/wnews/ordExec 등).
      None 이면 명령 응답으로 보고 항상 전송.
      지정 시 웹「텔레그램 푸시」on/off 를 따른다.
    """
    if pushKind:
        from config.opsSettings import isTelegramPushEnabled

        if not isTelegramPushEnabled(pushKind):
            return {"ok": True, "skipped": True, "pushKind": str(pushKind)}

    useChatId = chatId if chatId is not None else telegram_chat_id
    useToken = telegramToken if telegramToken is not None else telegram_token

    if not useToken:
        raise ValueError(
            "telegram_token이 비어 있습니다. 루트 .env 의 TELEGRAM_BOT_TOKEN 을 확인하세요."
        )
    if not useChatId:
        raise ValueError("telegram_chat_id가 비어 있습니다. config/config.py를 확인하세요.")

    url = f"https://api.telegram.org/bot{useToken}/sendMessage"
    payload = {
        "chat_id": useChatId,
        "text": msg,
    }
    response = requests.post(url, json=payload, timeout=30)
    body = response.json()

    if not body.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {body}")

    return body


if __name__ == "__main__":
    result = sendTelegramMsg("SATP_AI 텔레그램 전송 테스트")
    print("전송 성공:", result.get("result", {}).get("message_id"))
