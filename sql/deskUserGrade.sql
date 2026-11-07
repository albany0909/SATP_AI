-- 매매창 회원 등급: M=마스터(main) / C=클라이언트(client)
-- 실행 전 확인 후 적용.

ALTER TABLE tb_cusm_cust
  ADD COLUMN DESK_GRADE CHAR(1) NOT NULL DEFAULT 'C'
  COMMENT '매매창등급 M=master C=client'
  AFTER CUST_GRP_CD;

-- 기존 마스터 ID(구버전 MatherYn) 초기값
UPDATE tb_cusm_cust SET DESK_GRADE='M' WHERE LOG_ID IN ('albany09', 'albany82');
UPDATE tb_cusm_cust SET DESK_GRADE='C' WHERE DESK_GRADE IS NULL OR DESK_GRADE='' OR DESK_GRADE NOT IN ('M','C');
