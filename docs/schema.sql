-- 마이 헬스 로그 API - 데이터베이스 스키마 (ErdCloud import용 DDL)
-- database.py 의 SQLAlchemy 모델과 일치
-- ErdCloud: Import > SQL 에 붙여넣기 (MySQL 방언)

CREATE TABLE users (
    id              INT          NOT NULL AUTO_INCREMENT,
    username        VARCHAR(50)  NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role            VARCHAR(20)  NOT NULL DEFAULT 'user',  -- user / admin / superadmin
    created_at      DATETIME,                              -- 가입일시
    status          VARCHAR(20)  NOT NULL DEFAULT 'active', -- active(활성)/dormant(휴면)/withdrawn(탈퇴)
    name            VARCHAR(30),                            -- 환자 한글 이름 (코호트 적재분)
    person_id       INT,                                    -- 코호트 원본 person_id
    age             INT,                                    -- 나이 (첫 검진 기준)
    sex             VARCHAR(1),                             -- M / F
    smoker          TINYINT(1),                             -- 흡연 여부
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username),
    KEY idx_users_name (name),
    KEY idx_users_person (person_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE records (
    id             INT          NOT NULL AUTO_INCREMENT,
    user_id        INT          NOT NULL,
    date           VARCHAR(10)  NOT NULL,
    weight         FLOAT        NOT NULL,
    height         FLOAT        NOT NULL,
    systolic       INT          NOT NULL,
    diastolic      INT          NOT NULL,
    blood_sugar    INT          NOT NULL,
    steps          INT          DEFAULT 0,
    sleep_hours    FLOAT        DEFAULT 0,
    memo           VARCHAR(255) DEFAULT '',
    bmi            FLOAT,
    bmi_category   VARCHAR(20),
    bp_category    VARCHAR(20),
    sugar_category VARCHAR(20),
    warnings       TEXT,
    PRIMARY KEY (id),
    KEY idx_records_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE checkups (
    id           INT   NOT NULL AUTO_INCREMENT,
    user_id      INT   NOT NULL,
    quarter      INT   NOT NULL,               -- 0~19 (5년 × 분기)
    date         VARCHAR(10) NOT NULL,         -- yyyy-mm-dd
    bmi          FLOAT,
    waist        FLOAT,
    systolic     INT,
    diastolic    INT,
    fbs          INT,                          -- 공복혈당
    total_chol   INT,
    triglyceride INT,
    hdl          INT,
    ldl          INT,
    hemoglobin   FLOAT,
    ast          INT,
    alt          INT,
    ggt          INT,
    creatinine   FLOAT,
    grade        INT,                          -- 0정상 / 1주의 / 2위험
    memo         VARCHAR(255) DEFAULT '',      -- 지표 편차 기반 생활습관 메모
    PRIMARY KEY (id),
    KEY idx_checkups_user (user_id),
    KEY idx_checkups_quarter (quarter),
    KEY idx_checkups_grade (grade)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE activity_logs (
    id         INT          NOT NULL AUTO_INCREMENT,
    user_id    INT          NULL,
    username   VARCHAR(50),
    action     VARCHAR(30)  NOT NULL,
    detail     VARCHAR(255) DEFAULT '',
    created_at DATETIME,
    PRIMARY KEY (id),
    KEY idx_logs_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 관계 (FK) : ErdCloud가 이 줄들을 읽어 선을 그린다
ALTER TABLE records
    ADD CONSTRAINT fk_records_user
    FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE checkups
    ADD CONSTRAINT fk_checkups_user
    FOREIGN KEY (user_id) REFERENCES users (id);

ALTER TABLE activity_logs
    ADD CONSTRAINT fk_logs_user
    FOREIGN KEY (user_id) REFERENCES users (id);
