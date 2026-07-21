from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import date, timedelta
import json

from database import Base, engine, get_db, User, Record
from auth import hash_password, verify_password, create_access_token, get_current_user

# 서버 시작 시 표(테이블)가 없으면 만든다
Base.metadata.create_all(bind=engine)

app = FastAPI(title="마이 헬스 로그 API", version="2.0")


# ===== 요청 검증용 모델 (Pydantic) =====

class UserCreate(BaseModel):
    username: str
    password: str


class RecordIn(BaseModel):
    date: str
    weight: float
    height: float
    systolic: int
    diastolic: int
    blood_sugar: int
    steps: int = 0
    sleep_hours: float = 0.0
    memo: str = ""


# ===== 건강 계산 함수들 =====

def calc_bmi(weight, height):
    height_m = height / 100
    return round(weight / (height_m ** 2), 1)


def classify_bmi(bmi):
    if bmi < 18.5:
        return "저체중"
    elif bmi < 23:
        return "정상"
    elif bmi < 25:
        return "과체중"
    else:
        return "비만"


def classify_bp(systolic, diastolic):
    if systolic >= 140 or diastolic >= 90:
        return "고혈압"
    elif systolic < 120 and diastolic < 80:
        return "정상"
    else:
        return "주의"


def classify_sugar(blood_sugar):
    if blood_sugar < 100:
        return "정상"
    elif blood_sugar < 126:
        return "공복혈당장애"
    else:
        return "당뇨 의심"


def make_warnings(bmi_cat, bp_cat, sugar_cat):
    warnings = []
    if bmi_cat == "비만":
        warnings.append("비만 주의")
    if bp_cat == "고혈압":
        warnings.append("고혈압 주의")
    if sugar_cat == "당뇨 의심":
        warnings.append("당뇨 의심 주의")
    return warnings


def compute_and_fill(record: Record, data: RecordIn):
    """RecordIn 값으로 Record 객체의 필드 + 계산값을 채운다."""
    record.date = data.date
    record.weight = data.weight
    record.height = data.height
    record.systolic = data.systolic
    record.diastolic = data.diastolic
    record.blood_sugar = data.blood_sugar
    record.steps = data.steps
    record.sleep_hours = data.sleep_hours
    record.memo = data.memo

    bmi = calc_bmi(data.weight, data.height)
    record.bmi = bmi
    record.bmi_category = classify_bmi(bmi)
    record.bp_category = classify_bp(data.systolic, data.diastolic)
    record.sugar_category = classify_sugar(data.blood_sugar)
    warnings = make_warnings(record.bmi_category, record.bp_category, record.sugar_category)
    record.warnings = json.dumps(warnings, ensure_ascii=False)
    return record


def record_to_dict(r: Record):
    """DB 기록 객체 → JSON 응답용 딕셔너리."""
    return {
        "id": r.id,
        "user_id": r.user_id,
        "date": r.date,
        "weight": r.weight,
        "height": r.height,
        "systolic": r.systolic,
        "diastolic": r.diastolic,
        "blood_sugar": r.blood_sugar,
        "steps": r.steps,
        "sleep_hours": r.sleep_hours,
        "memo": r.memo,
        "bmi": r.bmi,
        "bmi_category": r.bmi_category,
        "bp_category": r.bp_category,
        "sugar_category": r.sugar_category,
        "warnings": json.loads(r.warnings) if r.warnings else [],
    }


# ===== 기본 =====

@app.get("/")
def read_root():
    return {"message": "마이 헬스 로그 API"}


# ===== 인증 =====

@app.post("/signup")
def signup(user: UserCreate, db: Session = Depends(get_db)):
    exists = db.query(User).filter(User.username == user.username).first()
    if exists:
        raise HTTPException(status_code=400, detail="이미 존재하는 사용자입니다")
    new_user = User(username=user.username, hashed_password=hash_password(user.password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "username": new_user.username}


@app.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 틀립니다")
    token = create_access_token(user.username)
    return {"access_token": token, "token_type": "bearer"}


# ===== 기록 CRUD (로그인 필요) =====

@app.post("/records")
def add_record(record: RecordIn,
               current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    new = Record(user_id=current_user.id)
    compute_and_fill(new, record)
    db.add(new)
    db.commit()
    db.refresh(new)
    return record_to_dict(new)


@app.get("/records")
def get_records(current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    rows = db.query(Record).filter(Record.user_id == current_user.id).all()
    return {"count": len(rows), "records": [record_to_dict(r) for r in rows]}


@app.get("/records/{record_id}")
def get_one(record_id: int,
            current_user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    r = db.query(Record).filter(Record.id == record_id,
                                Record.user_id == current_user.id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다")
    return record_to_dict(r)


@app.put("/records/{record_id}")
def update_record(record_id: int, record: RecordIn,
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    r = db.query(Record).filter(Record.id == record_id,
                                Record.user_id == current_user.id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다")
    compute_and_fill(r, record)
    db.commit()
    db.refresh(r)
    return record_to_dict(r)


@app.delete("/records/{record_id}")
def delete_record(record_id: int,
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    r = db.query(Record).filter(Record.id == record_id,
                                Record.user_id == current_user.id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다")
    db.delete(r)
    db.commit()
    return {"message": "삭제됨", "id": record_id}


# ===== 검색 · 통계 · 리포트 (로그인 필요, 본인 기록만) =====

@app.get("/search")
def search_records(start: str, end: str,
                   current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    rows = db.query(Record).filter(
        Record.user_id == current_user.id,
        Record.date >= start,
        Record.date <= end,
    ).all()
    return {"count": len(rows), "records": [record_to_dict(r) for r in rows]}


@app.get("/stats")
def get_stats(current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    rows = db.query(Record).filter(Record.user_id == current_user.id).all()
    if not rows:
        return {"count": 0, "message": "기록이 없습니다"}
    weights = [r.weight for r in rows]
    bmis = [r.bmi for r in rows]
    return {
        "count": len(rows),
        "avg_weight": round(sum(weights) / len(weights), 1),
        "avg_bmi": round(sum(bmis) / len(bmis), 1),
        "min_weight": min(weights),
        "max_weight": max(weights),
    }


@app.get("/report/weekly")
def weekly_report(current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    rows = db.query(Record).filter(Record.user_id == current_user.id).all()
    today = date.today()

    def avg_between(start_day, end_day):
        ws = [r.weight for r in rows
              if start_day <= date.fromisoformat(r.date) <= end_day]
        return round(sum(ws) / len(ws), 1) if ws else None

    this_week = avg_between(today - timedelta(days=6), today)
    last_week = avg_between(today - timedelta(days=13), today - timedelta(days=7))
    change = round(this_week - last_week, 1) if (this_week is not None and last_week is not None) else None

    return {
        "user": current_user.username,
        "today": today.isoformat(),
        "this_week_avg_weight": this_week,
        "last_week_avg_weight": last_week,
        "change": change,
    }


# ===== HTML 화면 =====

import os

PAGE_PATH = os.path.join(os.path.dirname(__file__), "page.html")


@app.get("/ui", response_class=HTMLResponse)
def ui():
    with open(PAGE_PATH, "r", encoding="utf-8") as f:
        return f.read()

