"""위험 전환 예측 모델을 로컬에서 학습해 저장한다.

타겟: 현재 정상·주의인 사람이 다음 분기에 위험(grade==2)으로 전환되는가.
서빙 일관성을 위해 DB(Checkup)에서 실제로 얻을 수 있는 특성만 쓴다.
  - 14지표 전부 + 나이·성별·흡연  (코호트를 DB에 그대로 적재했으므로 전부 사용 가능)
  - 파생: lag1 / d1(gap 보정) / ma4 / std4 / dev
  - 제외: severity(생성기 내부값), gap(예측력 없음)

모델: LogisticRegression (트리와 성능 동률이면서 빠르고 확률이 정직해서 채택).

사용법:
    python train_model.py [사람수]
    예) python train_model.py 30000
"""
import sys
import time
import json
import joblib

import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupShuffleSplit
from sklearn.metrics import roc_auc_score, average_precision_score

PARQUET = "data/checkups.parquet"
OUT_MODEL = "models/risk_model.joblib"
OUT_META = "models/risk_model_meta.json"
N_PEOPLE = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
SEED = 42

# ---- EDA에서 확정한 사양 --------------------------------------------------
METRICS_ALL = ["bmi", "waist", "systolic", "diastolic", "fbs", "total_chol",
               "triglyceride", "hdl", "ldl", "hemoglobin", "ast", "alt", "ggt", "creatinine"]
DROP_METRICS = ["hemoglobin", "creatinine"]              # 전환상관 0.05 미만
USE_METRICS = [m for m in METRICS_ALL if m not in DROP_METRICS]

CLIP = {"systolic": (70, 250), "diastolic": (40, 150), "fbs": (50, 400),
        "triglyceride": (20, 1000), "ggt": (5, 500), "ast": (5, 300),
        "alt": (3, 300), "total_chol": (80, 400), "bmi": (12, 50)}
LOGCOLS = ["triglyceride", "ggt", "ast", "alt"]
KEY = ["bmi", "systolic", "diastolic", "fbs", "ldl", "hdl", "triglyceride", "waist", "ggt"]
SUFFIX = ["_lag1", "_d1", "_ma4", "_std4", "_dev"]


def build_features(df, with_target=True):
    """전처리 → 파생특성. with_target 이면 다음 분기 전환 타겟도 만든다.

    파생은 반드시 행 필터링 전에 계산한다(rolling 창 보존).
    """
    s = df.sort_values(["person_id", "quarter"]).copy()

    for c, (lo, hi) in CLIP.items():
        if c in s:
            s[c] = s[c].clip(lo, hi)
    for c in LOGCOLS:
        if c in s:
            s[c] = np.log1p(s[c])

    g = s.groupby("person_id")
    s["gap"] = s.quarter - g["quarter"].shift(1)
    for c in KEY:
        s[f"{c}_lag1"] = g[c].shift(1)
        s[f"{c}_d1"] = (s[c] - s[f"{c}_lag1"]) / s["gap"]
        s[f"{c}_ma4"] = g[c].transform(lambda x: x.rolling(4, min_periods=1).mean())
        s[f"{c}_std4"] = g[c].transform(lambda x: x.rolling(4, min_periods=2).std())
        s[f"{c}_dev"] = s[c] - s[f"{c}_ma4"]
    s["grade_lag1"] = g["grade"].shift(1)
    s["is_male"] = (s.sex == "M").astype(int)
    s["smoker_i"] = s.smoker.astype(int)

    if with_target:
        nx = df[["person_id", "quarter", "grade"]].rename(columns={"grade": "next_grade"}).copy()
        nx["quarter"] -= 1
        s = s.merge(nx, on=["person_id", "quarter"], how="inner")
        s = s[s.grade < 2].copy()
        s["target"] = (s.next_grade == 2).astype(int)
    return s


DERIVED = [f"{c}{sfx}" for c in KEY for sfx in SUFFIX]
FEATS = USE_METRICS + DERIVED + ["age", "is_male", "smoker_i", "grade", "grade_lag1"]


def main():
    import os
    os.makedirs("models", exist_ok=True)

    t0 = time.time()
    df = pd.read_parquet(PARQUET)
    rng = np.random.default_rng(SEED)
    ids = rng.choice(df.person_id.unique(), min(N_PEOPLE, df.person_id.nunique()), replace=False)
    sub = df[df.person_id.isin(ids)].copy()
    print(f"로드 {len(sub):,}행 · {len(ids):,}명 · {time.time()-t0:.1f}s")

    s = build_features(sub)
    X, y, groups = s[FEATS], s["target"].values, s["person_id"].values
    print(f"학습 대상 {len(s):,}행 · 특성 {len(FEATS)}개 · 양성률 {y.mean()*100:.2f}%")

    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=0).split(X, y, groups))
    Xtr, Xte, ytr, yte = X.iloc[tr], X.iloc[te], y[tr], y[te]
    gte = s.grade.values[te]
    assert len(set(groups[tr]) & set(groups[te])) == 0, "사람 누출!"

    model = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(max_iter=2000, C=1.0, class_weight=None),
    )
    t1 = time.time()
    model.fit(Xtr, ytr)
    prob = model.predict_proba(Xte)[:, 1]
    base = yte.mean()
    pr_auc = average_precision_score(yte, prob)
    auc = roc_auc_score(yte, prob)
    print(f"\nLogisticRegression  학습 {time.time()-t1:.1f}s")
    print(f"  PR-AUC {pr_auc:.4f} (기준선 {base:.4f} · {pr_auc/base:.1f}배)  ·  AUC {auc:.4f}")

    # 하위집단
    sub_perf = []
    for gv, nm in [(0, "정상 출발"), (1, "주의 출발")]:
        m = gte == gv
        p = average_precision_score(yte[m], prob[m])
        sub_perf.append({"group": nm, "n": int(m.sum()), "pos_rate": round(float(yte[m].mean()), 4),
                         "pr_auc": round(float(p), 4), "lift": round(float(p / yte[m].mean()), 2)})
        print(f"  {nm}: 양성률 {yte[m].mean()*100:.2f}% · PR-AUC {p:.4f} · {p/yte[m].mean():.1f}배")

    # 전체 데이터로 재학습(저장용)
    model.fit(X, y)
    joblib.dump({"model": model, "features": FEATS, "clip": CLIP, "logcols": LOGCOLS,
                 "key": KEY, "suffix": SUFFIX, "use_metrics": USE_METRICS,
                 "baseline": float(base), "model_name": "LogisticRegression"}, OUT_MODEL)

    meta = {"model": "LogisticRegression",
            "target": "현재 정상·주의 → 다음 분기 위험 전환",
            "n_people": len(ids), "n_rows": int(len(s)), "n_features": len(FEATS),
            "pos_rate": round(float(y.mean()), 4),
            "pr_auc": round(float(pr_auc), 4), "auc": round(float(auc), 4),
            "baseline": round(float(base), 4), "subgroups": sub_perf}
    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n저장: {OUT_MODEL}")
    print(f"총 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
