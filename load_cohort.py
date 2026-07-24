"""코호트(checkups.parquet) 5,000명을 대시보드 DB에 적재한다.

- 기존 users / records / activity_logs / checkups 를 모두 비우고 새로 채운다.
- 각 사람에게 한글 이름을 부여하고, 분기 검진 14지표를 Checkup 으로 저장한다.
- 메모는 코호트에 없으므로, 그 분기 지표가 개인 평소(과거 중앙값) 대비
  얼마나 나쁜지/좋은지로 생활습관을 역추론해 인과적으로 생성한다.
  (혈당·혈압·체중이 개인 평소보다 크게 오르면 야식·음주·스트레스,
   크게 내리면 운동·숙면 쪽 메모를 붙인다 → 메모–지표 인사이트 검증용)

사용법:
    python load_cohort.py [사람수]
    예) python load_cohort.py 5000
"""
import sys
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd

from database import Base, engine, SessionLocal, User, Record, ActivityLog, Checkup
from auth import hash_password

PARQUET = "data/checkups.parquet"
N_PEOPLE = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
Q0_DATE = date(2021, 1, 15)          # 0분기 검진일 기준
SEED = 42

random.seed(SEED)
rng = np.random.default_rng(SEED)

# ---- 한글 이름 풀 (seed.py 와 동일) --------------------------------------
SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
            "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍"]
GIVEN = ["민준", "서연", "도윤", "하은", "지호", "서준", "하윤", "예준", "지우", "수아",
         "지민", "예은", "준서", "다은", "시우", "유진", "은우", "채원", "지훈", "소율",
         "건우", "지아", "현우", "서윤", "우진", "하린", "정우", "수빈", "지원", "민서",
         "태윤", "예린", "재이", "시윤", "유나", "준우", "서아", "도현", "하율", "지율"]


def make_names(n):
    combos = [s + g for s in SURNAMES for g in GIVEN]     # 20 × 40 = 800
    random.shuffle(combos)
    out = []
    i = 0
    while len(out) < n:
        base = combos[i % len(combos)]
        suffix = "" if i < len(combos) else str(i // len(combos) + 1)
        out.append(base + suffix)
        i += 1
    return out[:n]


# ---- 메모: 개인 평소 대비 지표 편차로 생활습관 역추론 -----------------------
# (지표가 나쁜 쪽으로 튀면 나쁜 습관, 좋은 쪽으로 튀면 좋은 습관 메모)
PHRASES = {
    "운동": ["아침에 30분 조깅했다", "헬스장에서 웨이트 1시간", "점심 후 산책 많이 함",
             "자전거로 출퇴근", "홈트 완료", "수영 다녀옴", "등산했더니 개운함",
             "저녁에 5km 러닝", "요가 클래스 참석", "계단 오르기 실천"],
    "숙면": ["요즘 7시간 푹 잔다", "오랜만에 숙면", "일찍 자서 컨디션 좋음", "잘 자고 일어남"],
    "수분": ["물 2리터씩 마심", "수분 충분히 섭취", "물 자주 마시는 습관"],
    "양호": ["컨디션 좋음", "몸이 가볍다", "특이사항 없이 좋음", "건강한 요즘"],
    "무": ["", "", "", "평범한 요즘", "특별한 일 없음"],
    "피곤": ["요즘 피곤하다", "야근으로 피곤", "수면이 부족했다", "몸이 무거움", "피로가 쌓임"],
    "스트레스": ["업무 스트레스 심함", "스트레스를 많이 받음", "마감 때문에 예민",
               "기분이 안 좋음", "긴장되는 나날", "짜증나는 일 많았음"],
    "야식": ["밤에 치킨 자주 먹음", "야식으로 라면", "자기 전 과자", "늦은 밤 간식",
             "야식을 참지 못함", "밤에 빵 먹음"],
    "과식": ["요즘 과식했다", "저녁 많이 먹음", "뷔페에서 과식", "배부르게 먹는 날들",
             "식사량이 과했음", "폭식했다"],
    "음주": ["회식에서 음주", "맥주 여러 잔", "저녁에 소주", "술자리 잦음",
             "와인 자주", "과음했다"],
}


def pick_memo(dfbs, dsys, dbmi):
    """개인 평소 대비 편차(z 유사값)로 생활습관 메모 선택.

    dfbs/dsys/dbmi = 이번 분기 값 - 개인 과거 중앙값 (표준화 전 원단위).
    """
    # 나쁜 신호: 혈당↑ 혈압↑ 체중↑ / 좋은 신호: 반대
    bad = dfbs / 12 + dsys / 10 + dbmi / 1.2       # 대략 표준화
    r = random.random()
    if bad > 1.2:                                   # 뚜렷하게 나빠짐
        if dfbs >= dsys and dfbs > 6:               # 혈당 주도 → 식습관
            key = "야식" if r < 0.55 else "과식"
        elif dsys > 6 and dfbs > 4:                 # 둘 다 → 음주
            key = "음주"
        else:                                       # 혈압 주도 → 스트레스
            key = "스트레스"
    elif bad > 0.5:
        key = random.choice(["피곤", "스트레스", "과식", "무"])
    elif bad < -1.0:                                # 뚜렷하게 좋아짐
        key = "운동" if r < 0.6 else random.choice(["숙면", "수분"])
    elif bad < -0.4:
        key = random.choice(["운동", "숙면", "양호", "무"])
    else:
        key = random.choice(["무", "무", "양호", "피곤"])
    return random.choice(PHRASES[key])


def main():
    print(f"코호트 로드: {PARQUET}")
    df = pd.read_parquet(PARQUET)
    ids = rng.choice(df.person_id.unique(), N_PEOPLE, replace=False)
    sub = df[df.person_id.isin(ids)].sort_values(["person_id", "quarter"]).copy()
    print(f"{N_PEOPLE:,}명 · {len(sub):,}행 선택")

    names = make_names(N_PEOPLE)

    # 스키마 재생성 (테이블 전부 비움)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # 관리자 계정 하나 (대시보드 로그인용)
    db.add(User(username="admin", hashed_password=hash_password("admin1234"),
                role="superadmin", status="active", name="관리자"))
    db.commit()

    INT_COLS = ["systolic", "diastolic", "fbs", "total_chol",
                "triglyceride", "hdl", "ldl", "ast", "alt", "ggt"]

    for idx, (pid, g) in enumerate(sub.groupby("person_id")):
        g = g.sort_values("quarter")
        first = g.iloc[0]
        user = User(
            username=f"patient{idx+1:04d}",
            hashed_password=hash_password("patient1234"),
            role="user", status="active",
            name=names[idx], person_id=int(pid),
            age=int(first["age"]), sex=str(first["sex"]),
            smoker=bool(first["smoker"]),
        )
        db.add(user)
        db.flush()               # user.id 확보

        # 개인 과거 누적 중앙값으로 편차 계산 (미래 정보 안 씀)
        fbs_hist, sys_hist, bmi_hist = [], [], []
        checkups = []
        for _, row in g.iterrows():
            fbs_v, sys_v, bmi_v = row["fbs"], row["systolic"], row["bmi"]
            dfbs = fbs_v - (np.median(fbs_hist) if fbs_hist else fbs_v)
            dsys = sys_v - (np.median(sys_hist) if sys_hist else sys_v)
            dbmi = bmi_v - (np.median(bmi_hist) if bmi_hist else bmi_v)
            memo = pick_memo(dfbs, dsys, dbmi)
            fbs_hist.append(fbs_v); sys_hist.append(sys_v); bmi_hist.append(bmi_v)

            d = Q0_DATE + timedelta(days=int(row["quarter"]) * 91)
            c = Checkup(
                user_id=user.id, quarter=int(row["quarter"]), date=d.isoformat(),
                bmi=round(float(row["bmi"]), 1),
                waist=round(float(row["waist"]), 1),
                grade=int(row["grade"]), memo=memo,
                hemoglobin=round(float(row["hemoglobin"]), 1),
                creatinine=round(float(row["creatinine"]), 2),
            )
            for col in INT_COLS:
                setattr(c, col, int(round(float(row[col]))))
            checkups.append(c)

        db.bulk_save_objects(checkups)
        if (idx + 1) % 500 == 0:
            db.commit()
            print(f"  {idx+1:,}/{N_PEOPLE:,} 적재")

    db.commit()

    # 검증
    nu = db.query(User).filter(User.person_id.isnot(None)).count()
    nc = db.query(Checkup).count()
    from sqlalchemy import func
    gd = dict(db.query(Checkup.grade, func.count()).group_by(Checkup.grade).all())
    memo_empty = db.query(Checkup).filter(Checkup.memo == "").count()
    print(f"\n적재 완료: 환자 {nu:,}명 · 검진 {nc:,}건")
    print(f"판정 분포: 정상 {gd.get(0,0):,} / 주의 {gd.get(1,0):,} / 위험 {gd.get(2,0):,}")
    print(f"메모 있는 검진 {nc-memo_empty:,}건 ({(nc-memo_empty)/nc*100:.0f}%)")
    sample = db.query(User).filter(User.person_id.isnot(None)).first()
    sc = db.query(Checkup).filter(Checkup.user_id == sample.id).order_by(Checkup.quarter).first()
    print(f"\n예시: {sample.name} ({sample.sex}, {sample.age}세, 흡연 {sample.smoker})")
    print(f"  0분기 [{sc.date}] BMI {sc.bmi} 혈압 {sc.systolic}/{sc.diastolic} 혈당 {sc.fbs} "
          f"판정 {sc.grade} 메모 '{sc.memo}'")
    db.close()


if __name__ == "__main__":
    main()
