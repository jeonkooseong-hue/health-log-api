"""더미 데이터 생성기.

사용법:
    python seed.py [사용자수] [일수]
    예) python seed.py 100 180   # 100명, 최근 180일(약 6개월) 매일 기록

기존 데이터를 모두 지우고 새로 생성한다.
로그인 계정:
    슈퍼관리자  admin / admin1234
    일반/관리자  user001 ~ / user1234
"""
import sys
import json
import random
from datetime import date, timedelta, datetime

from database import Base, engine, SessionLocal, User, Record, ActivityLog
from auth import hash_password
from main import calc_bmi, classify_bmi, classify_bp, classify_sugar, make_warnings

NUM_USERS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 180
TODAY = date(2026, 7, 21)
random.seed(42)

MEMOS = ["", "", "", "", "컨디션 좋음", "조금 피곤함", "야식 먹음",
         "운동 완료", "숙면함", "스트레스 많음", "물 많이 마심", "과식"]


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # 기존 데이터 초기화
    db.query(Record).delete()
    db.query(ActivityLog).delete()
    db.query(User).delete()
    db.commit()

    # 비밀번호 해시는 한 번만 (더미라 공용 비번)
    pw_admin = hash_password("admin1234")
    pw_user = hash_password("user1234")

    start = TODAY - timedelta(days=DAYS - 1)  # 첫 기록일
    joined = datetime.combine(start, datetime.min.time())

    # 사용자 생성: 1명 슈퍼관리자 + 3명 관리자 + 나머지 일반
    users = [User(username="admin", hashed_password=pw_admin, role="superadmin", created_at=joined)]
    for i in range(1, NUM_USERS):
        role = "admin" if i <= 3 else "user"
        users.append(User(username=f"user{i:03d}", hashed_password=pw_user, role=role, created_at=joined))
    db.add_all(users)
    db.commit()  # id 확보

    # 가입 로그
    db.add_all([ActivityLog(user_id=u.id, username=u.username, action="signup",
                            detail=f"role={u.role}", created_at=u.created_at) for u in users])
    db.commit()

    # 일일 건강 기록
    records = []
    for u in users:
        height = float(random.randint(150, 188))
        target_bmi = random.uniform(18.0, 32.0)
        weight = round(target_bmi * (height / 100) ** 2, 1)   # BMI 기준 현실적 체중
        sys_base = random.randint(105, 150)
        dia_base = random.randint(68, 95)
        sugar_base = random.randint(85, 135)
        for k in range(DAYS):
            d = start + timedelta(days=k)
            weight = round(weight + random.uniform(-0.3, 0.3), 1)   # 완만한 변동
            systolic = max(90, min(180, sys_base + random.randint(-8, 8)))
            diastolic = max(55, min(120, dia_base + random.randint(-6, 6)))
            blood_sugar = max(70, min(220, sugar_base + random.randint(-10, 12)))
            bmi = calc_bmi(weight, height)
            bmi_cat = classify_bmi(bmi)
            bp_cat = classify_bp(systolic, diastolic)
            sugar_cat = classify_sugar(blood_sugar)
            warns = make_warnings(bmi_cat, bp_cat, sugar_cat)
            records.append(Record(
                user_id=u.id, date=d.isoformat(), weight=weight, height=height,
                systolic=systolic, diastolic=diastolic, blood_sugar=blood_sugar,
                steps=random.randint(2000, 15000), sleep_hours=round(random.uniform(4.5, 9.0), 1),
                memo=random.choice(MEMOS),
                bmi=bmi, bmi_category=bmi_cat, bp_category=bp_cat, sugar_category=sugar_cat,
                warnings=json.dumps(warns, ensure_ascii=False),
            ))
    db.bulk_save_objects(records)
    db.commit()
    db.close()

    print(f"완료: 사용자 {len(users)}명 · 기록 {len(records)}건 · 기간 {start} ~ {TODAY}")
    print("로그인: admin/admin1234 (슈퍼) · user001~/user1234")


if __name__ == "__main__":
    main()
