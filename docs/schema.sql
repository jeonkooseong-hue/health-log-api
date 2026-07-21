-- 마이 헬스 로그 API - 데이터베이스 스키마 (ErdCloud import용 DDL)
-- database.py 의 SQLAlchemy 모델과 일치

CREATE TABLE users (
    id              INT          NOT NULL AUTO_INCREMENT COMMENT '사용자 고유번호',
    username        VARCHAR(50)  NOT NULL COMMENT '로그인 아이디 (고유)',
    hashed_password VARCHAR(255) NOT NULL COMMENT 'bcrypt 해시된 비밀번호',
    role            VARCHAR(20)  NOT NULL DEFAULT 'user' COMMENT '권한: user / admin',
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
);

CREATE TABLE records (
    id             INT          NOT NULL AUTO_INCREMENT COMMENT '기록 고유번호',
    user_id        INT          NOT NULL COMMENT '기록 주인 (users.id 참조)',
    date           VARCHAR(10)  NOT NULL COMMENT '측정일 YYYY-MM-DD',
    weight         FLOAT        NOT NULL COMMENT '몸무게(kg)',
    height         FLOAT        NOT NULL COMMENT '키(cm)',
    systolic       INT          NOT NULL COMMENT '수축기 혈압',
    diastolic      INT          NOT NULL COMMENT '이완기 혈압',
    blood_sugar    INT          NOT NULL COMMENT '공복 혈당(mg/dL)',
    steps          INT          DEFAULT 0 COMMENT '걸음 수',
    sleep_hours    FLOAT        DEFAULT 0 COMMENT '수면 시간',
    memo           VARCHAR(255) DEFAULT '' COMMENT '메모',
    bmi            FLOAT        COMMENT '계산값: 체질량지수',
    bmi_category   VARCHAR(20)  COMMENT '계산값: 저체중/정상/과체중/비만',
    bp_category    VARCHAR(20)  COMMENT '계산값: 정상/주의/고혈압',
    sugar_category VARCHAR(20)  COMMENT '계산값: 정상/공복혈당장애/당뇨 의심',
    warnings       TEXT         COMMENT '계산값: 경고 목록 (JSON 문자열)',
    PRIMARY KEY (id),
    CONSTRAINT fk_records_user FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE activity_logs (
    id         INT          NOT NULL AUTO_INCREMENT COMMENT '로그 고유번호',
    user_id    INT          NULL COMMENT '행위자 (users.id 참조, 실패 로그인은 NULL 가능)',
    username   VARCHAR(50)  COMMENT '행위자 이름 스냅샷',
    action     VARCHAR(30)  NOT NULL COMMENT 'signup/login/login_failed/create_record 등',
    detail     VARCHAR(255) DEFAULT '' COMMENT '부가 설명',
    created_at DATETIME     COMMENT '발생 시각',
    PRIMARY KEY (id),
    CONSTRAINT fk_logs_user FOREIGN KEY (user_id) REFERENCES users (id)
);
