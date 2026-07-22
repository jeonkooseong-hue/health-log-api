"""국민건강보험공단 건강검진정보(100만) 기반 횡단 위험 스크리닝 모델 6종 비교.

목표: 혈액검사(혈당) 없이 기본 정보만으로 '당뇨 의심(공복혈당>=126)' 선별.
  → 혈당 및 혈당 파생 변수는 특성에서 제외 (라벨 누출 방지)

실행:
  python analysis/nhis_train.py            # 층화 샘플 10만으로 6모델 비교
  python analysis/nhis_train.py --curve    # 학습곡선(샘플 크기별 성능 수렴 확인)
  python analysis/nhis_train.py --full     # LightGBM만 전체 100만
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import (RandomForestClassifier, GradientBoostingClassifier,
                              HistGradientBoostingClassifier)
from sklearn.metrics import roc_auc_score, average_precision_score, accuracy_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
import lightgbm as lgb
import xgboost as xgb

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "국민건강보험공단_건강검진정보_2023.CSV")

# 33개 중 필요한 것만 (메모리 1/3)
USECOLS = [
    "성별코드", "연령대코드(5세단위)", "신장(5cm단위)", "체중(5kg단위)", "허리둘레",
    "수축기혈압", "이완기혈압", "식전혈당(공복혈당)",
    "총콜레스테롤", "트리글리세라이드", "HDL콜레스테롤", "LDL콜레스테롤",
    "혈색소", "혈청크레아티닌", "혈청지오티(AST)", "혈청지피티(ALT)", "감마지티피",
    "흡연상태", "음주여부",
]
TARGET_SRC = "식전혈당(공복혈당)"
# 라벨 누출 방지: 혈당 자체는 특성에서 제외
DROP_FROM_X = [TARGET_SRC]

DTYPES = {
    "성별코드": "int8", "연령대코드(5세단위)": "int8",
    "신장(5cm단위)": "float32", "체중(5kg단위)": "float32", "허리둘레": "float32",
    "수축기혈압": "float32", "이완기혈압": "float32", "식전혈당(공복혈당)": "float32",
    "총콜레스테롤": "float32", "트리글리세라이드": "float32",
    "HDL콜레스테롤": "float32", "LDL콜레스테롤": "float32",
    "혈색소": "float32", "혈청크레아티닌": "float32",
    "혈청지오티(AST)": "float32", "혈청지피티(ALT)": "float32", "감마지티피": "float32",
    "흡연상태": "float32", "음주여부": "float32",
}


def load(nrows=None):
    t0 = time.time()
    df = pd.read_csv(CSV, encoding="cp949", usecols=USECOLS, dtype=DTYPES, nrows=nrows)
    # BMI 파생 (5cm/5kg 반올림이라 근사)
    h = df["신장(5cm단위)"] / 100.0
    df["BMI"] = (df["체중(5kg단위)"] / (h * h)).astype("float32")
    df = df[df[TARGET_SRC].notna()]
    df["target"] = (df[TARGET_SRC] >= 126).astype("int8")   # 당뇨 의심
    print(f"로드 {len(df):,}행 · {time.time()-t0:.1f}s · 메모리 {df.memory_usage(deep=True).sum()/1e6:.0f}MB")
    print(f"양성(당뇨 의심) 비율 {df['target'].mean()*100:.2f}%")
    return df


def stratified_sample(df, n):
    """성별×연령대 비율 유지 층화 샘플."""
    if n <= 0 or n >= len(df):
        return df
    frac = n / len(df)
    return (df.groupby(["성별코드", "연령대코드(5세단위)"], observed=True, group_keys=False)
              .apply(lambda g: g.sample(max(1, int(round(len(g) * frac))), random_state=0)))


def split(df):
    X = df.drop(columns=DROP_FROM_X + ["target"])
    y = df["target"].values
    return train_test_split(X, y, test_size=0.25, random_state=0, stratify=y)


def evaluate(name, model, Xtr, Xte, ytr, yte, needs_impute=True):
    t0 = time.time()
    if needs_impute:
        model = make_pipeline(SimpleImputer(strategy="median"), model)
    model.fit(Xtr, ytr)
    fit_s = time.time() - t0
    prob = model.predict_proba(Xte)[:, 1]
    pred = (prob >= 0.5).astype(int)
    return {
        "model": name,
        "AUC": roc_auc_score(yte, prob),
        "PR-AUC": average_precision_score(yte, prob),
        "acc": accuracy_score(yte, pred),
        "fit_s": fit_s,
    }


def models_for(n):
    """6종. 대용량(20만+)에서는 sklearn GBM 대신 HistGradientBoosting 사용(동일 계열, 히스토그램 기반)."""
    big = n > 200_000
    gbm = (("HistGradientBoosting", HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0), False)
           if big else
           ("GradientBoosting", GradientBoostingClassifier(random_state=0), True))
    return [
        ("LogisticRegression", make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)), True),
        ("DecisionTree(CART)", DecisionTreeClassifier(max_depth=8, random_state=0), True),
        ("RandomForest", RandomForestClassifier(n_estimators=200, max_depth=12, n_jobs=-1, random_state=0), True),
        gbm,
        ("LightGBM", lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=63,
                                        n_jobs=-1, random_state=0, verbose=-1), False),
        ("XGBoost", xgb.XGBClassifier(n_estimators=300, learning_rate=0.05, max_depth=6,
                                      n_jobs=-1, random_state=0, tree_method="hist",
                                      eval_metric="logloss"), False),
    ]


def run_compare(n_sample):
    df = load()
    s = stratified_sample(df, n_sample)
    print(f"\n층화 샘플 {len(s):,}행으로 6모델 비교")
    Xtr, Xte, ytr, yte = split(s)
    rows = []
    for name, m, imp in models_for(len(s)):
        r = evaluate(name, m, Xtr, Xte, ytr, yte, needs_impute=imp)
        rows.append(r)
        print(f"  {name:20} AUC={r['AUC']:.4f}  PR-AUC={r['PR-AUC']:.4f}  acc={r['acc']:.4f}  {r['fit_s']:.1f}s")
    print("\n" + "=" * 70)
    best = max(rows, key=lambda r: r["AUC"])
    print(f"최고 AUC: {best['model']} ({best['AUC']:.4f})")
    return rows


def run_curve():
    df = load()
    print("\n학습곡선 — 샘플 크기별 LightGBM 성능 (수렴 지점 확인)")
    for n in (10_000, 30_000, 100_000, 300_000, len(df)):
        s = stratified_sample(df, n)
        Xtr, Xte, ytr, yte = split(s)
        m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=63,
                               n_jobs=-1, random_state=0, verbose=-1)
        r = evaluate(f"n={n}", m, Xtr, Xte, ytr, yte, needs_impute=False)
        print(f"  n={len(s):>9,}  AUC={r['AUC']:.4f}  PR-AUC={r['PR-AUC']:.4f}  {r['fit_s']:.1f}s")


def run_full():
    df = load()
    print(f"\n전체 {len(df):,}행 · LightGBM")
    Xtr, Xte, ytr, yte = split(df)
    m = lgb.LGBMClassifier(n_estimators=500, learning_rate=0.05, num_leaves=127,
                           n_jobs=-1, random_state=0, verbose=-1)
    r = evaluate("LightGBM(full)", m, Xtr, Xte, ytr, yte, needs_impute=False)
    print(f"  AUC={r['AUC']:.4f}  PR-AUC={r['PR-AUC']:.4f}  acc={r['acc']:.4f}  {r['fit_s']:.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--curve", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--n", type=int, default=100_000)
    a = ap.parse_args()
    if a.curve:
        run_curve()
    elif a.full:
        run_full()
    else:
        run_compare(a.n)
