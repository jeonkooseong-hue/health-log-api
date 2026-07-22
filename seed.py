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
import math
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

# 메모(이벤트)가 그날 지표에 실제로 영향을 주도록 설계 (memo, d수축기, d이완기, d혈당, d체중)
# 핵심 스토리: 운동 → 혈압 크게↓·혈당 조금↓ / 야식·과식 → 혈당 크게↑ / 음주 → 혈압·혈당↑ / 스트레스 → 혈압↑
EVENTS = [
    ("운동 완료", -11, -6, -4, -0.06),
    ("숙면함", -4, -2, -2, 0.0),
    ("물 많이 마심", -2, -1, -3, 0.0),
    ("컨디션 좋음", -1, 0, -1, 0.0),
    ("", 0, 0, 0, 0.0),
    ("", 0, 0, 0, 0.0),
    ("조금 피곤함", 2, 1, 1, 0.0),
    ("스트레스 많음", 10, 6, 4, 0.0),
    ("야식 먹음", 2, 1, 17, 0.18),
    ("과식", 3, 2, 14, 0.22),
    ("음주", 12, 7, 11, 0.06),
]

SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
            "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍"]
GIVEN = ["민준", "서연", "도윤", "하은", "지호", "서준", "하윤", "예준", "지우", "수아",
         "지민", "예은", "준서", "다은", "시우", "유진", "은우", "채원", "지훈", "소율",
         "건우", "지아", "현우", "서윤", "우진", "하린", "정우", "수빈", "지원", "민서",
         "태윤", "예린", "재이", "시윤", "유나", "준우", "서아", "도현", "하율", "지율"]


def korean_names(n):
    """서로 다른 한국 이름 n개를 만든다."""
    combos = [s + g for s in SURNAMES for g in GIVEN]  # 20 x 40 = 800개
    random.shuffle(combos)
    return combos[:n]


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
    base = datetime.combine(start, datetime.min.time())

    def rand_join():
        return base + timedelta(days=random.randint(0, 20), hours=random.randint(6, 22),
                                minutes=random.randint(0, 59), seconds=random.randint(0, 59))

    # 사용자 생성: 1명 슈퍼관리자(admin) + 3명 관리자 + 나머지 일반 (한국 이름)
    names = korean_names(NUM_USERS - 1)
    users = [User(username="admin", hashed_password=pw_admin, role="superadmin",
                  created_at=rand_join(), status="active")]
    for i in range(NUM_USERS - 1):
        role = "admin" if i < 3 else "user"
        if role == "user":
            roll = random.random()
            status = "withdrawn" if roll < 0.04 else ("dormant" if roll < 0.10 else "active")
        else:
            status = "active"
        users.append(User(username=names[i], hashed_password=pw_user, role=role,
                          created_at=rand_join(), status=status))
    db.add_all(users)
    db.commit()  # id 확보

    # 가입 로그
    db.add_all([ActivityLog(user_id=u.id, username=u.username, action="signup",
                            detail=f"role={u.role}", created_at=u.created_at) for u in users])
    db.commit()

    # 일일 건강 기록 (메모 이벤트가 지표에 인과적으로 영향)
    records = []
    for u in users:
        height = float(random.randint(150, 188))
        target_bmi = random.uniform(18.0, 32.0)
        base = round(target_bmi * (height / 100) ** 2, 1)   # 시작 체중
        slope = random.uniform(-0.08, 0.03)
        amp = random.uniform(1.5, 3.5)
        sys_base = random.randint(105, 150)
        dia_base = random.randint(68, 95)
        sugar_base = random.randint(85, 135)
        # 개인 성향(페르소나): 운동파/야식파 등 → 이벤트 확률에 반영
        w_ex = random.uniform(0.6, 2.6)
        w_eat = random.uniform(0.6, 2.6)
        weights = []
        for (memo, ds, dd, dg, dw) in EVENTS:
            if memo == "운동 완료": weights.append(2.0 * w_ex)
            elif memo in ("야식 먹음", "과식"): weights.append(1.2 * w_eat)
            elif memo == "음주": weights.append(0.8)
            elif memo == "스트레스 많음": weights.append(0.9)
            elif memo == "": weights.append(2.4)
            else: weights.append(1.0)
        for k in range(DAYS):
            d = start + timedelta(days=k)
            memo, ds, dd, dg, dw = random.choices(EVENTS, weights=weights, k=1)[0]
            seasonal = amp * math.sin(math.pi * k / DAYS)
            weight = round(base + slope * k + seasonal + dw + random.uniform(-0.3, 0.3), 1)
            systolic = max(90, min(180, sys_base + ds + random.randint(-5, 5)))
            diastolic = max(55, min(120, dia_base + dd + random.randint(-4, 4)))
            blood_sugar = max(70, min(220, sugar_base + dg + random.randint(-8, 8)))
            bmi = calc_bmi(weight, height)
            bmi_cat = classify_bmi(bmi)
            bp_cat = classify_bp(systolic, diastolic)
            sugar_cat = classify_sugar(blood_sugar)
            warns = make_warnings(bmi_cat, bp_cat, sugar_cat)
            records.append(Record(
                user_id=u.id, date=d.isoformat(), weight=weight, height=height,
                systolic=systolic, diastolic=diastolic, blood_sugar=blood_sugar,
                steps=random.randint(2000, 15000), sleep_hours=round(random.uniform(4.5, 9.0), 1),
                memo=memo,
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
