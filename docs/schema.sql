-- 마이 헬스 로그 API - 데이터베이스 스키마 (ErdCloud import용 DDL)
-- database.py 의 SQLAlchemy 모델과 일치
-- ErdCloud: Import > SQL 에 붙여넣기 (MySQL 방언)

CREATE TABLE users (
    id              INT          NOT NULL AUTO_INCREMENT,
    username        VARCHAR(50)  NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role            VARCHAR(20)  NOT NULL DEFAULT 'user',
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
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

ALTER TABLE activity_logs
    ADD CONSTRAINT fk_logs_user
    FOREIGN KEY (user_id) REFERENCES users (id);
