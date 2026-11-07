# SATP_AI 매매창 ↔ 톰캣 STP API (3단계)

공통: `POST {stpServerUrl}`  
기본 URL: `http://satp1.iptime.org:8080/STP/tran`  
(구 SATP 클라이언트 `Common.Tp=P` · `server_addr=satp1.iptime.org:8080` 과 동일)  
로컬 톰캣만 쓸 때: `http://127.0.0.1:8080/STP/tran`

요청 래퍼:

```json
{
  "envelope": {
    "header": {
      "gramId": "SATPAIIQ010",
      "rqstRspDvcd": "S",
      "TDT": "20260919",
      "TRNS_HR": "143000",
      "SYS_DV": "AI",
      "USER_ID": "satp_ai"
    },
    "body": { }
  }
}
```

응답: `envelope.body` (기존 STP와 동일). 성공 시 body에 데이터, 실패 시 프레임워크 오류 헤더.

---

## gramId

| Id | 구분 | Bean.Method | 설명 |
|---|---|---|---|
| `SATPAIIQ010` | 조회 | `satpAiDeskSvc.listWatch` | 관심종목 |
| `SATPAIIQ020` | 조회 | `satpAiDeskSvc.listAlgEvt` | 알고리즘 이벤트(거래로그) |
| `SATPAIIQ021` | 조회 | `satpAiDeskSvc.listBuyAlgMap` | 당일 매수→alg 맵 |
| `SATPAIIQ030` | 조회 | `satpAiDeskSvc.getSettings` | 설정 JSON |
| `SATPAIWR030` | 저장 | `satpAiDeskSvc.saveSettings` | 설정 JSON 저장 |
| `SATPAIWR050` | 저장 | `satpAiDeskSvc.claimDeskLogin` | 계정 로그인 세션 점유 |
| `SATPAIIQ050` | 조회 | `satpAiDeskSvc.checkDeskLogin` | 세션 유효·heartbeat |
| `SATPAIWR051` | 저장 | `satpAiDeskSvc.releaseDeskLogin` | 세션 해제 |

---

### SATPAIIQ010 listWatch

body 요청:
- `MKT_TP` — 기본 `KR`
- `KIND` — `ACTIVE` | `TRADE` | `ALG`
  - `ACTIVE`/`TRADE` → `AI_WATCH_STK` (TRADE=USE_TRADE=Y)
  - `ALG` → `AI_ALG_STK` (알고리즘 관리종목, client 매매창 탭)
- `ALG_CD` — KIND=ALG 일 때 필수 (예: MTS01)
- `GRP_CD` — 선택 (WATCH 만)
- `USE_BT_ONLY` — `Y`/`N` (WATCH 만)
- `LIMIT` — TRADE/ALG 상한

body 응답:
- `ROWS` — 배열. WATCH 키: `STK_CD`, `STK_NM`, … / ALG 키: `ALG_CD`, `STK_CD`, `STK_NM`, `SRC`, `NOTE`

### SATPAIIQ020 listAlgEvt

요청: `ALG_CD`, `TRADE_DT`(YYYYMMDD), `LIMIT`, `MODE`(paper|real|빈값), `EVT_TPS`(콤마구분, 선택)

응답: `ROWS` — `AI_ALG_EVT` 컬럼 그대로

### SATPAIIQ021 listBuyAlgMap

요청: `TRADE_DT`, `ALG_CDS`(콤마), `MODE`

응답: `MAP` — `{ "005930": "MTS01", ... }`

### SATPAIIQ040 listDaySma

body 요청:
- `STK_CDS` — 콤마 구분 종목코드 (최대 200)
- `END_DT` — YYYYMMDD (선택, 기본 오늘). `TRADE_DT < END_DT` 종가로 SMA

body 응답:
- `ROWS` — `{ STK_CD, SMA5, SMA10, SMA20, LAST_CLOSE, FLU_RT, LAST_DT }`

### SATPAIIQ041 listDayCloses

body 요청:
- `STK_CDS` — 콤마 구분 (최대 120)
- `NEED` — 봉 개수 (기본 250, 최대 250)
- `END_DT` — YYYYMMDD (선택)

body 응답:
- `ROWS` — `{ STK_CD, CLOSES("종가,종가,…"), LAST_DT, CNT }` (오래된→최신)

### SATPAIIQ042 listInvSrNet

body 요청:
- `STK_CDS` — 콤마 구분 (최대 200)
- `DAYS` — 최근 N거래일 (기본 20, 최대 60)
- `AMT_QTY_TP` / `TRDE_TP` / `UNIT_TP` (기본 2/0/1)

body 응답:
- `DTS` — 최근일자 콤마 (최신→과거)
- `ROWS` — `{ STK_CD, TRADE_DT, IND_VAL, FRGN_VAL, ORGN_VAL, PEN_VAL, INVT_VAL, SAMO_VAL, CUR_PRC }`

### SATPAIWR050 claimDeskLogin

계정 단일 로그인. `FORCE=Y`(기본) 이면 기존 세션을 덮어씀(다른 PC 종료).

요청: `LOG_ID`, `SESS_TOKEN`, `HOST_NM`, `FORCE`
응답: `CLAIMED=Y|N`, `PREV_KICKED=Y|N`, `DUPLICATE=Y`(FORCE=N 거부 시)

### SATPAIIQ050 checkDeskLogin

요청: `LOG_ID`, `SESS_TOKEN`  
응답: `VALID=Y|N`, `MSG`, `HOST_NM` — 유효 시 heartbeat 갱신

### SATPAIWR051 releaseDeskLogin

요청: `LOG_ID`, `SESS_TOKEN` — 본인 토큰만 삭제

요청(저장): `SETTINGS_JSON` — 설정 전체 JSON 문자열  
응답(조회): `SETTINGS_JSON`  
저장소: `AI_DESK_CFG` (`CFG_KEY='settings'`)

---

## Python

- `utils/stpTranClient.py` — envelope 송수신
- `utils/deskDataApi.py` — `deskDataViaServer=true` 이면 위 gramId 호출
- `utils/deskCfgStore.py` — 설정 DB 동기(로컬 patch 시 UPSERT)

## Java (STP) — Eclipse/Tomcat 경로

**반드시** `C:\DEV_Server\workspace\STP` 에 둔다 (이 경로를 Eclipse·Tomcat이 본다).

- `src/main/java/thunder/us/tbb/biz/stock/SatpAiDeskSvc.java`
- `src/main/resources/conf/transaction.xml` 등록
- DB: `thor_db` (`tbbDataSource`) · `AI_WATCH_STK` / `AI_ALG_EVT` / `AI_DESK_CFG`

## 켜는 법

1. `AI_DESK_CFG` 테이블 생성 (`sql/deskCfg.sql`) — 이미 있으면 스킵
2. Eclipse에서 STP 프로젝트 새로고침 후 Tomcat 재기동 (또는 Ant `D.package` 배포)
3. `settings.json`: `"deskDataViaServer": true`, `"stpServerUrl": "http://satp1.iptime.org:8080/STP/tran"`
4. main/client 재기동

서버 실패 시 Python은 **로컬 Store로 폴백**하고 로그를 남긴다.
