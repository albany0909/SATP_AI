"""
키움 REST 웹소켓 클라이언트.
LOGIN → REG/REMOVE → REAL 수신. 서버 PING에는 PONG으로 응답한다.
"""

import json
import threading

import websocket

from utils.termLog import termLog

WS_PATH = "/api/dostk/websocket"


class KiwoomWsClient:
    """
    키움 국내주식 실시간 웹소켓.
    onMessage(msgDict) 콜백으로 파싱된 JSON을 전달한다.
    """

    def __init__(self, socketUrl, token, onMessage=None, onOpen=None, onClose=None):
        self.wsUrl = socketUrl.rstrip("/") + WS_PATH
        self.token = token or ""
        self.onMessage = onMessage
        self.onOpenCb = onOpen
        self.onCloseCb = onClose
        self._ws = None
        self._thread = None
        self._loggedIn = False
        self._sendLock = threading.Lock()

    @property
    def isLoggedIn(self):
        return self._loggedIn

    def start(self):
        """백그라운드 스레드에서 run_forever 기동."""
        self._loggedIn = False
        self._ws = websocket.WebSocketApp(
            self.wsUrl,
            on_open=self._onOpen,
            on_message=self._onMessage,
            on_error=self._onError,
            on_close=self._onClose,
        )
        self._thread = threading.Thread(
            target=self._ws.run_forever,
            name="kiwoomWs",
            daemon=True,
            kwargs={"ping_interval": None},
        )
        self._thread.start()

    def stop(self):
        """연결 종료."""
        self._loggedIn = False
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def sendJson(self, payload):
        """JSON 객체를 전송한다. 실패 시 False."""
        if self._ws is None:
            return False
        try:
            text = json.dumps(payload, ensure_ascii=False)
            with self._sendLock:
                self._ws.send(text)
            return True
        except Exception as err:
            termLog(f"[WS] 전송 실패: {err}")
            return False

    def sendLogin(self):
        """접근 토큰으로 LOGIN."""
        return self.sendJson({"trnm": "LOGIN", "token": self.token})

    def sendReg(self, typeCodes, items, grpNo="1", refresh="1"):
        """
        실시간 등록(REG).
        typeCodes: ['0B'] 또는 ['04']
        items: 종목코드 리스트. 04는 ['']
        """
        types = typeCodes if isinstance(typeCodes, list) else [typeCodes]
        itemList = items if isinstance(items, list) else [items]
        return self.sendJson(
            {
                "trnm": "REG",
                "grp_no": str(grpNo),
                "refresh": str(refresh),
                "data": [{"item": itemList, "type": types}],
            }
        )

    def sendRemove(self, typeCodes, items, grpNo="1"):
        """실시간 해지(REMOVE)."""
        types = typeCodes if isinstance(typeCodes, list) else [typeCodes]
        itemList = items if isinstance(items, list) else [items]
        return self.sendJson(
            {
                "trnm": "REMOVE",
                "grp_no": str(grpNo),
                "data": [{"item": itemList, "type": types}],
            }
        )

    def _onOpen(self, ws):
        termLog("[WS] 연결됨 — LOGIN 전송")
        self.sendLogin()
        if self.onOpenCb:
            try:
                self.onOpenCb()
            except Exception as err:
                termLog(f"[WS] onOpen 콜백 오류: {err}")

    def _onMessage(self, ws, message):
        try:
            msg = json.loads(message) if isinstance(message, str) else message
        except Exception as err:
            termLog(f"[WS] JSON 파싱 실패: {err}")
            return

        if not isinstance(msg, dict):
            return

        trnm = str(msg.get("trnm") or "")

        # 서버 생존 확인 — PONG 응답
        if trnm == "PING":
            self.sendJson({"trnm": "PONG"})
            return

        if trnm == "LOGIN":
            # return_code 0 이면 로그인 성공
            try:
                codeOk = int(msg.get("return_code", 1)) == 0
            except (TypeError, ValueError):
                codeOk = False
            self._loggedIn = codeOk
            if codeOk:
                termLog("[WS] LOGIN 성공")
            else:
                termLog(f"[WS] LOGIN 실패: {msg.get('return_msg') or msg}")

        if self.onMessage:
            try:
                self.onMessage(msg)
            except Exception as err:
                termLog(f"[WS] onMessage 콜백 오류: {err}")

    def _onError(self, ws, error):
        termLog(f"[WS] 오류: {error}")

    def _onClose(self, ws, closeStatusCode=None, closeMsg=None):
        self._loggedIn = False
        termLog(f"[WS] 연결 종료 code={closeStatusCode} msg={closeMsg}")
        if self.onCloseCb:
            try:
                self.onCloseCb()
            except Exception as err:
                termLog(f"[WS] onClose 콜백 오류: {err}")
