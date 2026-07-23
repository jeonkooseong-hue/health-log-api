"""시계열 건강검진 코호트 생성기.

설계:
  · 10만 명 × 5년 × 분기(3개월)마다 검진 = 20시점 → 최대 200만 행
  · 앵커: 연령대×성별 분포 고정 (건강검진통계연보 기준을 1/10 축소)
  · 개인별 잠재위험 z → 분기마다 랜덤워크 → 지표 생성 → criteria.py로 판정 도출
    (판정을 따로 배정하지 않으므로 수치와 판정이 항상 정합)
  · 흡연: 성별 조건부 목표 비율, 기간 중 일부 금연 전이
  · 결측(미수검) / 이상치(측정오류) 포함
출력: data/checkups.parquet (없으면 CSV)

실행: python analysis/gen_cohort.py [--n 100000] [--years 5]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "data")

# [100만 기준] 연령대별 (계, 남, 여) — 앵커
AGE_BANDS = [
    # label, (min_age, max_age), total, male, female
    ("20대이하", (20, 29), 154_000, 84_700, 69_300),
    ("30대",     (30, 39), 157_000, 86_350, 70_650),
    ("40대",     (40, 49), 212_000, 108_120, 103_880),
    ("50대",     (50, 59), 226_000, 113_000, 113_000),
    ("60대",     (60, 69), 185_000, 90_650, 94_350),
    ("70대이상", (70, 84), 66_000, 28_380, 37_620),
]
BASE_TOTAL = 1_000_000

# 흡연 목표 (100만 기준): 현재흡연 남 153,500 / 여 23,500
SMOKE_RATE = {"M": 153_500 / 512_500, "F": 23_500 / 487_500}   # 성별 내 현재흡연 비율
QUIT_RATE_PER_Q = 0.008     # 분기당 금연 확률
MISS_RATE = 0.10            # 미수검(결측 행) 비율
OUTLIER_RATE = 0.005        # 이상치 비율


def build_people(n, rng):
    """연령×성별 앵커에 맞춰 개인 생성."""
    sexes, ages = [], []
    for _, (lo, hi), total, male, female in AGE_BANDS:
        nm = int(round(male / BASE_TOTAL * n))
        nf = int(round(female / BASE_TOTAL * n))
        sexes += ["M"] * nm + ["F"] * nf
        ages += list(rng.integers(lo, hi + 1, nm + nf))
    sex = np.array(sexes)
    age = np.array(ages, dtype=np.float32)
    idx = rng.permutation(len(sex))
    sex, age = sex[idx], age[idx]

    is_male = sex == "M"
    smoker = np.where(is_male,
                      rng.random(len(sex)) < SMOKE_RATE["M"],
                      rng.random(len(sex)) < SMOKE_RATE["F"])
    # 잠재위험 z: 나이↑, 남성, 흡연 → 위험↑
    z = (0.030 * (age - 45)
         + 0.35 * is_male
         + 0.45 * smoker
         + rng.normal(0, 1.0, len(sex)))
    return pd.DataFrame({"person_id": np.arange(1, len(sex) + 1),
                         "sex": sex, "age0": age,
                         "smoker0": smoker, "z0": z.astype(np.float32)})


def metrics_from_z(z, age, is_male, rng, offset=0.0):
    """잠재위험 z → 검진 수치 (벡터화)."""
    n = len(z)
    zz = z + offset
    bmi = np.clip(21.0 + 1.9 * zz + rng.normal(0, 1.6, n), 14, 45)
    waist = np.clip(1.9 * bmi + np.where(is_male, 40, 35) + rng.normal(0, 3.5, n), 55, 140)
    sys_bp = np.clip(112 + 8.5 * zz + 0.15 * (age - 45) + rng.normal(0, 8, n), 85, 210)
    dia_bp = np.clip(70 + 5.0 * zz + rng.normal(0, 6, n), 50, 130)
    fbs = np.clip(90 + 11.0 * zz + 0.20 * (age - 45) + rng.normal(0, 9, n), 60, 320)
    tchol = np.clip(180 + 16 * zz + rng.normal(0, 28, n), 90, 400)
    tg = np.clip(95 * np.exp(0.30 * zz + rng.normal(0, 0.35, n)), 25, 900)
    hdl = np.clip(62 - 7.5 * zz - np.where(is_male, 5, 0) + rng.normal(0, 8, n), 15, 110)
    ldl = np.clip(105 + 15 * zz + rng.normal(0, 24, n), 30, 280)
    hgb = np.clip(np.where(is_male, 15.2, 13.3) + rng.normal(0, 1.1, n), 8, 19)
    ast = np.clip(22 * np.exp(0.16 * zz + rng.normal(0, 0.30, n)), 5, 400)
    alt = np.clip(21 * np.exp(0.22 * zz + rng.normal(0, 0.34, n)), 3, 400)
    ggt = np.clip(26 * np.exp(0.33 * zz + rng.normal(0, 0.45, n)), 5, 600)
    creat = np.clip(np.where(is_male, 0.95, 0.75) + rng.normal(0, 0.14, n), 0.3, 3.0)
    return dict(bmi=bmi, waist=waist, systolic=sys_bp, diastolic=dia_bp, fbs=fbs,
                total_chol=tchol, triglyceride=tg, hdl=hdl, ldl=ldl, hemoglobin=hgb,
                ast=ast, alt=alt, ggt=ggt, creatinine=creat)


def severity_score(m, is_male):
    """이상 항목 가중 심각도 점수 (위험항목 2점, 경계항목 1점 + 연속 보정).

    ※ '모든 항목 정상'을 정상으로 보면 13%밖에 안 나와 통계연보(40.2%)와 안 맞음.
       통계연보 종합판정(정상A/B·질환의심·유질환자)에 맞추기 위해 심각도 점수의
       분위수로 3등급을 나눈다. (수치가 나쁠수록 등급이 나쁨 → 수치·판정 정합 유지)
    """
    waist_cut = np.where(is_male, 90, 85)
    risk = (
        (m["systolic"] >= 140).astype(np.float32) + (m["diastolic"] >= 90)
        + (m["fbs"] >= 126) + (m["bmi"] >= 25.0) + (m["ldl"] >= 160)
        + (m["hdl"] < 40) + (m["triglyceride"] >= 200) + (m["waist"] >= waist_cut)
    )
    caution = (
        ((m["systolic"] >= 120) & (m["systolic"] < 140)).astype(np.float32)
        + ((m["diastolic"] >= 80) & (m["diastolic"] < 90))
        + ((m["fbs"] >= 100) & (m["fbs"] < 126))
        + ((m["bmi"] >= 23.0) & (m["bmi"] < 25.0)) + (m["bmi"] < 18.5)
        + ((m["ldl"] >= 130) & (m["ldl"] < 160))
        + ((m["hdl"] >= 40) & (m["hdl"] < 60))
        + ((m["triglyceride"] >= 150) & (m["triglyceride"] < 200))
    )
    # 동점 해소용 연속 성분 (지표가 기준을 얼마나 초과했는지)
    cont = (m["systolic"] / 120 + m["fbs"] / 100 + m["bmi"] / 23
            + m["ldl"] / 130 + m["triglyceride"] / 150 + (60 / np.maximum(m["hdl"], 10))) / 6
    return 2.0 * risk + 1.0 * caution + 0.30 * cont


def grade_from_score(score, cuts):
    """심각도 점수 → 0정상/1주의/2위험."""
    return np.where(score >= cuts[1], 2, np.where(score >= cuts[0], 1, 0)).astype(np.int8)


def calibrate_cuts(people, rng, offset, target=(0.402, 0.322, 0.276)):
    """전체 판정 비율이 목표가 되도록 심각도 점수 분위수 절단점 산출."""
    is_male = (people["sex"] == "M").values
    age = people["age0"].values
    z = people["z0"].values
    m = metrics_from_z(z, age, is_male, np.random.default_rng(0), offset)
    s = severity_score(m, is_male)
    q1 = np.quantile(s, target[0])                    # 정상/주의 경계
    q2 = np.quantile(s, target[0] + target[1])        # 주의/위험 경계
    return (float(q1), float(q2))


def generate(n=100_000, years=5, seed=42):
    rng = np.random.default_rng(seed)
    t0 = time.time()
    people = build_people(n, rng)
    print(f"개인 {len(people):,}명 생성 · 남 {(people.sex=='M').sum():,} / 여 {(people.sex=='F').sum():,}")

    offset = 0.0
    cuts = calibrate_cuts(people, rng, offset)
    print(f"심각도 절단점 캘리브레이션: 정상<{cuts[0]:.2f} ≤주의<{cuts[1]:.2f} ≤위험")

    is_male = (people["sex"] == "M").values
    z = people["z0"].values.astype(np.float64)
    smoker = people["smoker0"].values.copy()
    n_q = years * 4
    frames = []
    for t in range(n_q):
        age = people["age0"].values + t * 0.25
        # 개인 상태 랜덤 변동 (랜덤워크) + 완만한 노화 추세
        z = z + rng.normal(0.010, 0.16, len(z))
        # 일부 금연
        quit_mask = smoker & (rng.random(len(z)) < QUIT_RATE_PER_Q)
        smoker = smoker & ~quit_mask
        z = z - 0.25 * quit_mask       # 금연 시 위험 소폭 감소

        m = metrics_from_z(z, age, is_male, rng, offset)
        df = pd.DataFrame({
            "person_id": people["person_id"].values,
            "quarter": t,
            "date": pd.Timestamp("2021-03-31") + pd.DateOffset(months=3 * t),
            "sex": people["sex"].values,
            "age": age.astype(np.float32),
            "smoker": smoker,
            "severity": severity_score(m, is_male).astype(np.float32),
        })
        for k, v in m.items():
            df[k] = v.astype(np.float32)
        frames.append(df)
    data = pd.concat(frames, ignore_index=True)

    # 전체 시점 풀링 후 분위수 절단 → 목표 비율 정확히 달성
    tgt = (0.402, 0.322, 0.276)
    q1 = float(np.quantile(data["severity"].to_numpy(), tgt[0]))
    q2 = float(np.quantile(data["severity"].to_numpy(), tgt[0] + tgt[1]))
    data["grade"] = grade_from_score(data["severity"].to_numpy(), (q1, q2))
    print(f"심각도 절단점(전체 풀링): 정상<{q1:.2f} ≤주의<{q2:.2f} ≤위험")

    # 이상치 (측정 오류)
    n_out = int(len(data) * OUTLIER_RATE)
    oi = rng.choice(len(data), n_out, replace=False)
    col = rng.choice(["systolic", "fbs", "triglyceride", "ggt"], n_out)
    for c in np.unique(col):
        sel = oi[col == c]
        vals = data.loc[sel, c].to_numpy() * rng.uniform(2.5, 4.0, len(sel))
        data.loc[sel, c] = vals.astype(np.float32)

    # 미수검(결측 행 제거)
    keep = rng.random(len(data)) >= MISS_RATE
    data = data[keep].reset_index(drop=True)

    print(f"행 {len(data):,} · {time.time()-t0:.1f}s")
    dist = data["grade"].value_counts(normalize=True).sort_index()
    print(f"판정 비율  정상 {dist.get(0,0)*100:.1f}% / 주의 {dist.get(1,0)*100:.1f}% / 위험 {dist.get(2,0)*100:.1f}%  (목표 40.2/32.2/27.6)")
    print(f"현재흡연(최종시점) {data[data.quarter==n_q-1]['smoker'].mean()*100:.1f}%")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--years", type=int, default=5)
    a = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)
    data = generate(a.n, a.years)
    try:
        path = os.path.join(OUTDIR, "checkups.parquet")
        data.to_parquet(path, index=False)
    except Exception as e:
        path = os.path.join(OUTDIR, "checkups.csv")
        print(f"parquet 실패({e}) → CSV로 저장")
        data.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"저장: {path}  ({os.path.getsize(path)/1e6:.0f}MB)")


if __name__ == "__main__":
    main()
