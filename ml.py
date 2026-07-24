"""학습된 위험 전환 모델을 불러와 예측한다.

DB의 Checkup 시계열을 받아 train_model.py 와 동일한 특성을 만들고,
최신 분기 시점의 '다음 분기 위험 전환 확률'을 반환한다.

모델 파일(models/risk_model.joblib)이 없으면 predict_transition() 은
{"available": False} 를 돌려주므로 API 는 모델 없이도 동작한다.
"""
import os
import numpy as np
import pandas as pd

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "risk_model.joblib")
_PACK = None


def _load():
    global _PACK
    if _PACK is None and os.path.exists(_MODEL_PATH):
        import joblib
        _PACK = joblib.load(_MODEL_PATH)
    return _PACK


def is_available() -> bool:
    return _load() is not None


def _build_one(checkups) -> pd.DataFrame:
    """한 사람의 Checkup 목록 → 특성 행렬 (분기 오름차순).

    checkups: DB Checkup ORM 객체들의 리스트 (또는 dict 리스트).
    """
    pack = _load()
    rows = []
    for c in checkups:
        get = (lambda k: c.get(k)) if isinstance(c, dict) else (lambda k: getattr(c, k))
        rows.append({
            "person_id": 0, "quarter": get("quarter"),
            "bmi": get("bmi"), "waist": get("waist"), "systolic": get("systolic"),
            "diastolic": get("diastolic"), "fbs": get("fbs"), "total_chol": get("total_chol"),
            "triglyceride": get("triglyceride"), "hdl": get("hdl"), "ldl": get("ldl"),
            "hemoglobin": get("hemoglobin"), "ast": get("ast"), "alt": get("alt"),
            "ggt": get("ggt"), "creatinine": get("creatinine"), "grade": get("grade"),
            "age": get("_age"), "sex": get("_sex"), "smoker": get("_smoker"),
        })
    df = pd.DataFrame(rows).sort_values("quarter")

    clip, logcols, key = pack["clip"], pack["logcols"], pack["key"]
    # 숫자 컬럼 강제 변환 (None → NaN). 이후 imputer가 median으로 채움
    numcols = set(clip) | set(logcols) | set(key) | {"grade", "age", "is_male", "smoker_i"}
    for col in numcols:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col, (lo, hi) in clip.items():
        if col in df:
            df[col] = df[col].clip(lo, hi)
    for col in logcols:
        if col in df:
            df[col] = np.log1p(df[col])

    df["gap"] = df.quarter.diff()
    for c in key:
        df[f"{c}_lag1"] = df[c].shift(1)
        df[f"{c}_d1"] = (df[c] - df[f"{c}_lag1"]) / df["gap"]
        df[f"{c}_ma4"] = df[c].rolling(4, min_periods=1).mean()
        df[f"{c}_std4"] = df[c].rolling(4, min_periods=2).std()
        df[f"{c}_dev"] = df[c] - df[f"{c}_ma4"]
    df["grade_lag1"] = df.grade.shift(1)
    df["is_male"] = (df.sex == "M").astype(int)
    df["smoker_i"] = df.smoker.astype(int)
    return df


def predict_transition(checkups, age, sex, smoker) -> dict:
    """최신 분기 기준 다음 분기 위험 전환 확률.

    반환: {available, prob, grade, eligible, baseline, drivers[]}
      - eligible=False 이면 최신 판정이 이미 위험이라 전환 예측 대상이 아님.
    """
    pack = _load()
    if pack is None:
        return {"available": False}
    if not checkups:
        return {"available": True, "prob": None, "eligible": False, "reason": "기록 없음"}

    # 인적정보를 각 행에 실어 특성 생성기로 전달
    enriched = []
    for c in checkups:
        d = {k: getattr(c, k) for k in (
            "quarter", "bmi", "waist", "systolic", "diastolic", "fbs", "total_chol",
            "triglyceride", "hdl", "ldl", "hemoglobin", "ast", "alt", "ggt",
            "creatinine", "grade")}
        d["_age"], d["_sex"], d["_smoker"] = age, sex, smoker
        enriched.append(d)

    df = _build_one(enriched)
    last = df.iloc[[-1]]
    grade = int(last["grade"].iloc[0])
    baseline = pack.get("baseline")

    if grade >= 2:      # 이미 위험 → 전환 예측 대상 아님
        return {"available": True, "prob": None, "grade": grade,
                "eligible": False, "baseline": baseline,
                "reason": "이미 위험군 (전환 예측 대상 아님)"}

    X = last[pack["features"]]
    prob = float(pack["model"].predict_proba(X)[:, 1][0])

    return {"available": True, "prob": round(prob, 3), "grade": grade,
            "eligible": True, "baseline": baseline,
            "drivers": _drivers(pack, X)}


def _drivers(pack, X) -> list:
    """로지스틱 계수 × 표준화 기여로 위험을 끌어올린 상위 특성."""
    model = pack["model"]
    try:
        clf = model.named_steps["logisticregression"]
        scaler = model.named_steps["standardscaler"]
        imp = model.named_steps["simpleimputer"]
        xi = imp.transform(X)
        xs = scaler.transform(xi)
        contrib = (clf.coef_[0] * xs[0])          # 로그오즈 기여
    except Exception:
        return []

    KOR = {"bmi": "체질량지수", "waist": "허리둘레", "systolic": "수축기혈압",
           "diastolic": "이완기혈압", "fbs": "공복혈당", "total_chol": "총콜레스테롤",
           "triglyceride": "중성지방", "hdl": "HDL", "ldl": "LDL", "ast": "간수치AST",
           "alt": "간수치ALT", "ggt": "감마지티피", "age": "나이", "grade": "현재판정",
           "is_male": "성별(남)", "smoker_i": "흡연"}
    def label(f):
        for sfx, t in [("_lag1", "·직전"), ("_d1", "·변화량"), ("_ma4", "·1년평균"),
                       ("_std4", "·변동성"), ("_dev", "·평균대비")]:
            if f.endswith(sfx):
                return KOR.get(f[:-len(sfx)], f[:-len(sfx)]) + t
        return KOR.get(f, f)

    order = np.argsort(contrib)[::-1]
    out = []
    for i in order[:3]:
        if contrib[i] > 0:
            out.append({"feature": label(pack["features"][i]),
                        "effect": round(float(contrib[i]), 3)})
    return out
