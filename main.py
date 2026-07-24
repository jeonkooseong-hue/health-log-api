from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import date, timedelta
from collections import Counter
import json

from database import Base, engine, get_db, User, Record, ActivityLog, Checkup
from auth import (hash_password, verify_password, create_access_token,
                 get_current_user, get_current_admin, get_current_superadmin)
import ml
import stats as behavior
import llm

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


def log_action(db: Session, action: str, user: User = None, username: str = "", detail: str = ""):
    """활동 로그 한 줄 기록."""
    entry = ActivityLog(
        user_id=user.id if user else None,
        username=user.username if user else username,
        action=action,
        detail=detail,
    )
    db.add(entry)
    db.commit()


# ===== 기본 =====

@app.get("/")
def read_root():
    return {"message": "마이 헬스 로그 API"}


# ===== 인증 =====

@app.post("/signup")
def signup(user: UserCreate, db: Session = Depends(get_db)):
    exists = db.query(User).filter(User.username == user.username).first()
    if exists:
        if exists.status == "active":
            raise HTTPException(status_code=400, detail="이미 존재하는 사용자입니다")
        # 휴면/탈퇴 회원이 같은 아이디로 재가입 → 계정 복구 + 비밀번호 갱신 (기록 보존)
        exists.status = "active"
        exists.hashed_password = hash_password(user.password)
        db.commit()
        db.refresh(exists)
        log_action(db, "reactivate", user=exists, detail="재가입으로 계정 복구")
        return {"id": exists.id, "username": exists.username, "role": exists.role, "reactivated": True}
    # 첫 번째 가입자는 자동으로 슈퍼관리자 (부트스트랩)
    is_first = db.query(User).count() == 0
    role = "superadmin" if is_first else "user"
    new_user = User(username=user.username,
                    hashed_password=hash_password(user.password),
                    role=role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    log_action(db, "signup", user=new_user, detail=f"role={role}")
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role}


@app.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        log_action(db, "login_failed", username=form.username)
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 틀립니다")
    if user.status == "dormant":
        raise HTTPException(status_code=403, detail="휴면 계정입니다. 관리자에게 문의하세요")
    if user.status == "withdrawn":
        raise HTTPException(status_code=403, detail="탈퇴한 회원입니다")
    token = create_access_token(user.username)
    log_action(db, "login", user=user)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "username": current_user.username, "role": current_user.role}


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
    log_action(db, "create_record", user=current_user, detail=f"record#{new.id}")
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
    log_action(db, "update_record", user=current_user, detail=f"record#{r.id}")
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
    log_action(db, "delete_record", user=current_user, detail=f"record#{record_id}")
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


# ===== 관리자 (admin 전용) =====

def _health_level(r):
    """최신 기록으로 현재 건강 상태 판정: 위험 / 주의 / 정상 / None."""
    if r is None:
        return None
    if _warn_count(r) > 0:
        return "위험"
    if (r.bmi_category in ("저체중", "과체중")
            or r.bp_category == "주의" or r.sugar_category == "공복혈당장애"):
        return "주의"
    return "정상"


_GRADE_LABEL = {0: "정상", 1: "주의", 2: "위험"}


@app.get("/admin/users")
def admin_users(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    users = db.query(User).all()
    # 전건 로딩 대신 SQL 집계 (대용량 대응)
    counts = dict(db.query(Checkup.user_id, func.count(Checkup.id)).group_by(Checkup.user_id).all())
    latest = _latest_checkup_sql(db, active_only=False)      # {user_id: Checkup}
    logins = dict(db.query(ActivityLog.user_id, func.max(ActivityLog.created_at))
                    .filter(ActivityLog.action == "login")
                    .group_by(ActivityLog.user_id).all())
    result = []
    for u in users:
        c = latest.get(u.id)
        result.append({
            "id": u.id, "username": u.username, "role": u.role, "status": u.status,
            "name": u.name, "age": u.age, "sex": u.sex, "smoker": u.smoker,
            "record_count": counts.get(u.id, 0),
            "health": _GRADE_LABEL.get(c.grade) if c else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": logins[u.id].isoformat() if logins.get(u.id) else None,
        })
    return {"count": len(result), "users": result}


@app.get("/admin/users/{user_id}")
def admin_user_detail(user_id: int,
                      admin: User = Depends(get_current_admin),
                      db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    cks = db.query(Checkup).filter(Checkup.user_id == u.id).order_by(Checkup.quarter).all()
    logs = db.query(ActivityLog).filter(ActivityLog.user_id == u.id).order_by(ActivityLog.id.desc()).limit(10).all()

    stats = None
    if cks:
        last = cks[-1]
        bmis = [c.bmi for c in cks if c.bmi is not None]
        stats = {
            "avg_bmi": round(sum(bmis) / len(bmis), 1) if bmis else None,
            "latest_date": last.date,
            "latest_grade": _GRADE_LABEL.get(last.grade),
            "latest_systolic": last.systolic, "latest_fbs": last.fbs,
        }
    return {
        "id": u.id, "username": u.username, "role": u.role, "status": u.status,
        "name": u.name, "age": u.age, "sex": u.sex, "smoker": u.smoker,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "record_count": len(cks),
        "stats": stats,
        "checkups": [_checkup_dict(c) for c in cks],        # 전체 검진 (분기순)
        "recent_logs": [{
            "action": l.action, "detail": l.detail,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        } for l in logs],
    }


def _checkup_dict(c: Checkup) -> dict:
    return {k: getattr(c, k) for k in (
        "id", "quarter", "date", "bmi", "waist", "systolic", "diastolic", "fbs",
        "total_chol", "triglyceride", "hdl", "ldl", "hemoglobin", "ast", "alt",
        "ggt", "creatinine", "grade", "memo")}


@app.get("/admin/users/{user_id}/checkups")
def admin_user_checkups(user_id: int,
                        admin: User = Depends(get_current_admin),
                        db: Session = Depends(get_db)):
    """환자의 건강검진 시계열 (분기 오름차순)."""
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    cks = (db.query(Checkup).filter(Checkup.user_id == user_id)
             .order_by(Checkup.quarter).all())
    return {
        "user": {"id": u.id, "name": u.name, "age": u.age, "sex": u.sex,
                 "smoker": u.smoker, "person_id": u.person_id},
        "count": len(cks),
        "checkups": [_checkup_dict(c) for c in cks],
    }


@app.get("/admin/users/{user_id}/ai-insight")
def admin_user_ai_insight(user_id: int,
                          admin: User = Depends(get_current_admin),
                          db: Session = Depends(get_db)):
    """ML 위험 전환 확률 + 통계 행동 인사이트 + LLM 소견.

    - ML: 학습된 모델로 다음 분기 위험 전환 확률 (모델 없으면 available=False)
    - 통계: 메모–지표 효과를 n/CI/p(다중보정)로 검정
    - LLM: 위 숫자를 문장으로 (OPENAI_API_KEY 없으면 규칙 템플릿)
    """
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    cks = (db.query(Checkup).filter(Checkup.user_id == user_id)
             .order_by(Checkup.quarter).all())

    risk = ml.predict_transition(cks, u.age, u.sex, u.smoker)
    insights = behavior.behavior_insights(cks)
    payload = {
        "patient": {"name": u.name, "age": u.age, "sex": u.sex, "smoker": u.smoker},
        "risk": risk,
        "insights": insights,
    }
    narrative = llm.make_narrative(payload)
    return {"data": payload, "narrative": narrative}


@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int,
                      admin: User = Depends(get_current_superadmin),
                      db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다")
    if u.role == "superadmin" and db.query(User).filter(User.role == "superadmin").count() <= 1:
        raise HTTPException(status_code=400, detail="마지막 슈퍼관리자는 탈퇴 처리할 수 없습니다")
    # 소프트 삭제: DB에서 지우지 않고 탈퇴 표시 (기록은 보존)
    u.status = "withdrawn"
    db.commit()
    log_action(db, "withdraw_user", user=admin, detail=f"{u.username} 탈퇴 처리")
    return {"message": "탈퇴 처리됨", "id": user_id, "username": u.username}


class StatusUpdate(BaseModel):
    status: str  # active / dormant / withdrawn


@app.put("/admin/users/{user_id}/status")
def admin_set_status(user_id: int, body: StatusUpdate,
                     admin: User = Depends(get_current_admin),
                     db: Session = Depends(get_db)):
    """계정 상태 변경: 활성/휴면/탈퇴 (권한 변경과 별개)."""
    if body.status not in ("active", "dormant", "withdrawn"):
        raise HTTPException(status_code=400, detail="status는 active/dormant/withdrawn 중 하나여야 합니다")
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if target.id == admin.id and body.status != "active":
        raise HTTPException(status_code=400, detail="자기 자신은 비활성화할 수 없습니다")
    if target.role == "superadmin" and body.status != "active":
        if db.query(User).filter(User.role == "superadmin", User.status == "active").count() <= 1:
            raise HTTPException(status_code=400, detail="마지막 슈퍼관리자는 비활성화할 수 없습니다")
    target.status = body.status
    db.commit()
    log_action(db, "change_status", user=admin, detail=f"user#{target.id} -> {body.status}")
    return {"id": target.id, "username": target.username, "status": target.status}


@app.get("/admin/logs")
def admin_logs(admin: User = Depends(get_current_admin), db: Session = Depends(get_db), limit: int = 50):
    logs = db.query(ActivityLog).order_by(ActivityLog.id.desc()).limit(limit).all()
    return {
        "count": len(logs),
        "logs": [{
            "id": l.id, "username": l.username, "action": l.action,
            "detail": l.detail,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        } for l in logs],
    }


@app.get("/admin/stats")
def admin_stats(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return {
        "total_users": db.query(User).count(),
        "total_records": db.query(Record).count(),
        "total_logs": db.query(ActivityLog).count(),
    }


def _latest_per_user(records):
    """사용자별 가장 최근 기록만 추린다 (파이썬 리스트용)."""
    latest = {}
    for r in records:
        cur = latest.get(r.user_id)
        if cur is None or r.date > cur.date:
            latest[r.user_id] = r
    return latest


def _latest_records_sql(db: Session):
    """사용자별 최신 기록만 SQL로 (전건 로딩 방지). {user_id: Record}"""
    sub = (db.query(Record.user_id.label("uid"), func.max(Record.date).label("mx"))
             .group_by(Record.user_id).subquery())
    rows = (db.query(Record)
              .join(sub, (Record.user_id == sub.c.uid) & (Record.date == sub.c.mx))
              .all())
    return {r.user_id: r for r in rows}


def _monthly_avg_sql(db: Session, user_ids=None, active_only=True):
    """월별 평균 체중·BMI를 SQL 집계로."""
    q = db.query(func.substr(Record.date, 1, 7).label("m"),
                 func.avg(Record.weight), func.avg(Record.bmi))
    if user_ids is not None:
        q = q.filter(Record.user_id.in_(user_ids))
    elif active_only:
        q = q.join(User, User.id == Record.user_id).filter(User.status == "active")
    rows = q.group_by("m").order_by("m").all()
    return [{"month": m,
             "avg_weight": round(w, 1) if w is not None else None,
             "avg_bmi": round(b, 1) if b is not None else None} for m, w, b in rows]


def _latest_checkup_sql(db: Session, active_only=True):
    """사용자별 최신 분기 검진만 SQL로. {user_id: Checkup}"""
    sub = (db.query(Checkup.user_id.label("uid"), func.max(Checkup.quarter).label("mx"))
             .group_by(Checkup.user_id).subquery())
    q = (db.query(Checkup)
           .join(sub, (Checkup.user_id == sub.c.uid) & (Checkup.quarter == sub.c.mx)))
    if active_only:
        q = q.join(User, User.id == Checkup.user_id).filter(User.status == "active")
    return {c.user_id: c for c in q.all()}


@app.get("/admin/health-stats")
def admin_health_stats(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """회원 단위 건강 통계 + 분기별 위험비율 추이 (활성 회원, 최신 검진 기준)."""
    latest = list(_latest_checkup_sql(db, active_only=True).values())

    def bmi_cat(v):
        return "비만" if v >= 25 else "과체중" if v >= 23 else "저체중" if v < 18.5 else "정상"
    def bp_cat(c):
        return "고혈압" if (c.systolic >= 140 or c.diastolic >= 90) else \
               "주의" if (c.systolic >= 120 or c.diastolic >= 80) else "정상"
    def sugar_cat(v):
        return "당뇨 의심" if v >= 126 else "공복혈당장애" if v >= 100 else "정상"

    bmi_u = Counter(bmi_cat(c.bmi) for c in latest)
    bp_u = Counter(bp_cat(c) for c in latest)
    sugar_u = Counter(sugar_cat(c.fbs) for c in latest)
    grade_u = Counter(c.grade for c in latest)
    risk = {
        "obese": bmi_u.get("비만", 0),
        "hypertension": bp_u.get("고혈압", 0),
        "diabetes": sugar_u.get("당뇨 의심", 0),
        "any_warning": grade_u.get(2, 0),        # 종합판정 위험
    }

    # 분기별 위험(grade==2) 비율 추이 — SQL 집계 (활성 회원)
    total = (db.query(Checkup.quarter, func.count(Checkup.id))
               .join(User, User.id == Checkup.user_id).filter(User.status == "active")
               .group_by(Checkup.quarter).all())
    risky = (db.query(Checkup.quarter, func.count(Checkup.id))
               .join(User, User.id == Checkup.user_id)
               .filter(User.status == "active", Checkup.grade == 2)
               .group_by(Checkup.quarter).all())
    rd = dict(risky)
    monthly = [{"quarter": q, "risk_pct": round(rd.get(q, 0) / n * 100, 1) if n else 0}
               for q, n in sorted(total)]

    return {
        "user_count": len(latest),
        "risk": risk,
        "bmi_user_dist": dict(bmi_u),
        "bp_user_dist": dict(bp_u),
        "sugar_user_dist": dict(sugar_u),
        "grade_dist": {"정상": grade_u.get(0, 0), "주의": grade_u.get(1, 0), "위험": grade_u.get(2, 0)},
        "monthly": monthly,
    }


# ===== 메모 → 이벤트 태깅 + 인과 인사이트 =====

EVENT_KEYWORDS = [
    ("운동", ["조깅", "웨이트", "산책", "자전거", "홈트", "수영", "등산", "러닝", "요가", "계단", "운동"]),
    ("야식·과식", ["야식", "치킨", "라면", "과자", "간식", "빵", "떡볶이", "과식", "폭식", "많이 먹", "배부르", "식사량"]),
    ("음주", ["음주", "맥주", "소주", "술자리", "와인", "과음", "회식"]),
    ("스트레스", ["스트레스", "예민", "짜증", "긴장", "기분이 안"]),
    ("수면", ["숙면", "푹 잤", "일찍 자", "잘 자"]),
    ("피로", ["피곤", "피로", "수면이 부족", "몸이 무거"]),
    ("수분", ["물 2리터", "수분", "물 자주", "물 많이"]),
]


def tag_event(memo: str):
    """메모 자연어 → 이벤트 분류 (키워드 규칙)."""
    if not memo:
        return None
    for name, kws in EVENT_KEYWORDS:
        for k in kws:
            if k in memo:
                return name
    return None


@app.get("/admin/users/{user_id}/insights")
def admin_user_insights(user_id: int,
                        admin: User = Depends(get_current_admin),
                        db: Session = Depends(get_db)):
    """이 회원의 '어떤 행동이 어떤 지표를 얼마나 움직이는가' 분석 + 서사."""
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    recs = db.query(Record).filter(Record.user_id == user_id).all()
    if len(recs) < 10:
        return {"user_id": user_id, "username": u.username, "events": [], "narrative": ["분석할 기록이 부족합니다."]}

    sys_vals = sorted(r.systolic for r in recs)
    sug_vals = sorted(r.blood_sugar for r in recs)
    mid = len(recs) // 2
    sys_med, sug_med = sys_vals[mid], sug_vals[mid]

    agg = {}
    for r in recs:
        ev = tag_event(r.memo)
        if ev is None:
            continue
        a = agg.setdefault(ev, {"n": 0, "sys": 0.0, "sug": 0.0})
        a["n"] += 1
        a["sys"] += r.systolic - sys_med
        a["sug"] += r.blood_sugar - sug_med

    events = [{
        "event": ev, "n": a["n"],
        "sys_delta": round(a["sys"] / a["n"], 1),
        "sugar_delta": round(a["sug"] / a["n"], 1),
    } for ev, a in agg.items() if a["n"] >= 5]
    events.sort(key=lambda e: -abs(e["sugar_delta"]) - abs(e["sys_delta"]))

    # 서사 생성
    narrative = []
    if events:
        bp_best = min(events, key=lambda e: e["sys_delta"])
        bp_worst = max(events, key=lambda e: e["sys_delta"])
        sg_worst = max(events, key=lambda e: e["sugar_delta"])
        if bp_best["sys_delta"] <= -3:
            narrative.append(f"‘{bp_best['event']}’ 한 날 혈압이 평소보다 {abs(bp_best['sys_delta'])} 낮음 — 혈압 개선에 효과적입니다.")
        if bp_worst["sys_delta"] >= 4:
            narrative.append(f"‘{bp_worst['event']}’ 한 날 혈압이 평소보다 {bp_worst['sys_delta']} 높음 — 혈압 상승 요인입니다.")
        if sg_worst["sugar_delta"] >= 5:
            narrative.append(f"혈당 상승 주원인은 ‘{sg_worst['event']}’ (평소 대비 +{sg_worst['sugar_delta']}).")
        # 핵심: 운동은 듣는데 혈당이 안 잡히는 이유
        if (bp_best["sys_delta"] <= -3 and sg_worst["sugar_delta"] >= 5
                and bp_best["sugar_delta"] > -sg_worst["sugar_delta"] * 0.6):
            narrative.append(
                f"‘{bp_best['event']}’의 혈당 개선폭({bp_best['sugar_delta']})이 "
                f"‘{sg_worst['event']}’의 상승폭(+{sg_worst['sugar_delta']})을 상쇄하지 못합니다. "
                f"혈압은 잡히지만 혈당이 잡히지 않는 이유 → 식습관 개입이 필요합니다.")
    if not narrative:
        narrative.append("뚜렷한 행동–지표 연관이 관찰되지 않았습니다.")

    return {"user_id": user_id, "username": u.username,
            "baseline": {"systolic": sys_med, "blood_sugar": sug_med},
            "events": events, "narrative": narrative}


def _warn_count(r):
    try:
        return len(json.loads(r.warnings)) if r.warnings else 0
    except Exception:
        return 0


@app.get("/admin/health-group")
def admin_health_group(metric: str, category: str = "",
                       admin: User = Depends(get_current_admin),
                       db: Session = Depends(get_db)):
    """특정 위험군에 속한 회원(최신 검진 기준) + 그룹 분기별 추이."""
    latest = _latest_checkup_sql(db, active_only=True)     # {user_id: Checkup}
    names = {u.id: u for u in db.query(User).filter(User.person_id.isnot(None)).all()}

    # metric → (판정함수, 값필드, 단위)
    val_field = {"bmi": "bmi", "bp": "systolic", "sugar": "fbs", "warning": "grade"}.get(metric)
    unit = {"bmi": "BMI", "bp": "수축기 혈압", "sugar": "공복 혈당", "warning": "종합판정"}.get(metric)
    if val_field is None:
        raise HTTPException(status_code=400, detail="metric은 bmi/bp/sugar/warning 중 하나여야 합니다")

    def hit(c):
        if metric == "bmi":
            return c.bmi >= 25          # 비만
        if metric == "bp":
            return c.systolic >= 140 or c.diastolic >= 90   # 고혈압
        if metric == "sugar":
            return c.fbs >= 126         # 당뇨 의심
        return c.grade == 2             # warning: 종합판정 위험

    group = [c for c in latest.values() if hit(c)]
    user_ids = {c.user_id for c in group}
    users = []
    for c in group:
        u = names.get(c.user_id)
        users.append({"id": c.user_id,
                      "username": (u.name if u else None) or (u.username if u else "?"),
                      "value": getattr(c, val_field), "date": c.date})
    users.sort(key=lambda x: x["value"], reverse=True)

    # 그룹 회원 분기별 평균 — SQL 집계
    monthly = []
    if user_ids:
        col = getattr(Checkup, val_field)
        rows = (db.query(Checkup.quarter, func.avg(col))
                  .filter(Checkup.user_id.in_(user_ids))
                  .group_by(Checkup.quarter).order_by(Checkup.quarter).all())
        monthly = [{"month": f"Q{q}", "avg": round(v, 1) if v is not None else None} for q, v in rows]

    cat_label = {"bmi": "비만", "bp": "고혈압", "sugar": "당뇨 의심", "warning": "위험군"}[metric]
    return {"metric": metric, "category": category or cat_label, "unit": unit,
            "user_count": len(users), "users": users, "monthly": monthly}


class RoleUpdate(BaseModel):
    role: str  # "user" / "admin" / "superadmin"


@app.put("/admin/users/{user_id}/role")
def admin_set_role(user_id: int, body: RoleUpdate,
                   admin: User = Depends(get_current_superadmin),
                   db: Session = Depends(get_db)):
    if body.role not in ("user", "admin", "superadmin"):
        raise HTTPException(status_code=400, detail="role은 user, admin, superadmin 중 하나여야 합니다")
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    # 자기 자신 강등 방지 (락아웃 예방)
    if target.id == admin.id and body.role != "superadmin":
        raise HTTPException(status_code=400, detail="자기 자신의 슈퍼관리자 권한은 해제할 수 없습니다")
    # 마지막 슈퍼관리자 강등 방지
    if target.role == "superadmin" and body.role != "superadmin":
        if db.query(User).filter(User.role == "superadmin").count() <= 1:
            raise HTTPException(status_code=400, detail="마지막 슈퍼관리자는 강등할 수 없습니다")
    target.role = body.role
    db.commit()
    log_action(db, "change_role", user=admin, detail=f"user#{target.id} -> {body.role}")
    return {"id": target.id, "username": target.username, "role": target.role}


# ===== HTML 화면 =====

import os

BASE_DIR = os.path.dirname(__file__)
PAGE_PATH = os.path.join(BASE_DIR, "page.html")
DASHBOARD_PATH = os.path.join(BASE_DIR, "dashboard.html")


@app.get("/ui", response_class=HTMLResponse)
def ui():
    with open(PAGE_PATH, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        return f.read()

