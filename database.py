"""데이터베이스 설정 + 표(테이블) 정의"""
from datetime import datetime
from sqlalchemy import (create_engine, Column, Integer, String, Float, Boolean,
                        ForeignKey, Text, DateTime, Index)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# SQLite 파일 하나로 도는 DB (health.db)
DATABASE_URL = "sqlite:///./health.db"

# engine = DB와 실제 연결. check_same_thread=False 는 FastAPI에서 필요
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# SessionLocal = DB 작업 한 묶음(세션)을 만드는 공장
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Base = 모든 표(모델)의 부모 클래스
Base = declarative_base()


class User(Base):
    """사용자 표"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)  # 고유
    hashed_password = Column(String, nullable=False)                    # 암호화된 비번
    role = Column(String, default="user", nullable=False)              # user / admin / superadmin
    created_at = Column(DateTime, default=datetime.now)                # 가입일시
    status = Column(String, default="active", nullable=False)         # active(활성)/dormant(휴면)/withdrawn(탈퇴)

    # 인적 정보 (코호트 환자용. 직접 가입한 관리자 계정은 비어 있음)
    name = Column(String, index=True)          # 환자 한글 이름
    person_id = Column(Integer, index=True)    # 코호트 원본 person_id
    age = Column(Integer)                       # 나이 (첫 검진 기준)
    sex = Column(String)                        # M / F
    smoker = Column(Boolean)                    # 흡연 여부

    # 이 사용자가 가진 기록들 (1:N)
    records = relationship("Record", back_populates="owner", cascade="all, delete-orphan")
    # 이 사용자의 활동 로그들 (1:N)
    logs = relationship("ActivityLog", back_populates="user", cascade="all, delete-orphan")
    # 이 사용자의 건강검진 기록들 (1:N)
    checkups = relationship("Checkup", back_populates="owner", cascade="all, delete-orphan")


class Record(Base):
    """건강 기록 표"""
    __tablename__ = "records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # 누구 기록인지

    date = Column(String, nullable=False, index=True)
    weight = Column(Float, nullable=False)
    height = Column(Float, nullable=False)
    systolic = Column(Integer, nullable=False)
    diastolic = Column(Integer, nullable=False)
    blood_sugar = Column(Integer, nullable=False)
    steps = Column(Integer, default=0)
    sleep_hours = Column(Float, default=0.0)
    memo = Column(String, default="")

    # 서버가 계산해 저장하는 값들
    bmi = Column(Float)
    bmi_category = Column(String)
    bp_category = Column(String)
    sugar_category = Column(String)
    warnings = Column(Text, default="[]")  # 경고 목록을 JSON 문자열로 저장

    owner = relationship("User", back_populates="records")


class Checkup(Base):
    """건강검진 기록 표 (코호트 시계열 검진. 분기별 1건)"""
    __tablename__ = "checkups"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    quarter = Column(Integer, nullable=False, index=True)  # 0~19 (5년 × 분기)
    date = Column(String, nullable=False, index=True)      # yyyy-mm-dd

    # 검진 14지표
    bmi = Column(Float)
    waist = Column(Float)
    systolic = Column(Integer)
    diastolic = Column(Integer)
    fbs = Column(Integer)            # 공복혈당
    total_chol = Column(Integer)
    triglyceride = Column(Integer)
    hdl = Column(Integer)
    ldl = Column(Integer)
    hemoglobin = Column(Float)
    ast = Column(Integer)
    alt = Column(Integer)
    ggt = Column(Integer)
    creatinine = Column(Float)

    grade = Column(Integer, index=True)   # 0정상 / 1주의 / 2위험
    memo = Column(String, default="")     # 지표 편차 기반 생활습관 메모

    owner = relationship("User", back_populates="checkups")

    # 최신 분기 조회(회원별 max quarter)용 복합 인덱스
    __table_args__ = (Index("ix_checkups_user_quarter", "user_id", "quarter"),)


class ActivityLog(Base):
    """활동 로그 표 (로그인/기록 변경 등 기록)"""
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # 누가 (실패한 로그인은 없을 수 있음)
    username = Column(String)                                         # 로그용 이름 스냅샷
    action = Column(String, nullable=False)                          # signup / login / login_failed / create_record ...
    detail = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)              # 언제

    user = relationship("User", back_populates="logs")


def get_db():
    """요청마다 DB 세션을 열고, 끝나면 닫는다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
