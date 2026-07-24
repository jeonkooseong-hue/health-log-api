"""메모–지표 행동 인사이트 (통계 검정).

한 사람의 검진 시계열에서, 특정 생활습관 메모가 있던 시점의 지표가
그 사람 평소(기준선)와 통계적으로 유의하게 다른지 검정한다.

원칙(이전에 지적받은 '판단 누가 하냐'에 대한 답):
  - 여기서는 판단만 한다. 서술(문장 생성)은 llm.py 가 한다.
  - 각 효과에 표본수 n, 95% 신뢰구간, p값을 붙인다.
  - 여러 조합을 동시에 검정하므로 Benjamini–Hochberg 로 다중비교 보정한다.
  - 역인과 방지: 메모는 t분기, 지표 효과는 '그 메모 시점의 지표'로 본다.
    (코호트 메모가 그 분기 지표 편차로 생성되었으므로 동시점 비교가 타당)

의존성: numpy 만 사용 (scipy 없이 t분포 근사).
"""
import numpy as np

# 메모 → 행동 태그 (dashboard 인사이트 엔진과 동일 어휘)
EVENT_KEYWORDS = [
    ("운동", ["조깅", "웨이트", "산책", "자전거", "홈트", "수영", "등산", "러닝", "요가", "계단", "운동"]),
    ("야식", ["야식", "치킨", "라면", "과자", "간식", "빵", "떡볶이", "새벽"]),
    ("과식", ["과식", "많이 먹", "뷔페", "폭식", "배부르", "식사량"]),
    ("음주", ["음주", "맥주", "소주", "술자리", "와인", "과음", "회식"]),
    ("스트레스", ["스트레스", "예민", "짜증", "긴장", "기분이 안"]),
    ("숙면", ["숙면", "푹 잔", "일찍 자", "잘 자"]),
    ("피곤", ["피곤", "피로", "수면이 부족", "몸이 무거"]),
    ("수분", ["물 2리터", "수분", "물 자주", "물 많이"]),
]

# 태그별로 볼 지표 (임상적으로 관련된 것만)
TAG_METRICS = {
    "운동": ["systolic", "fbs", "bmi"],
    "야식": ["fbs", "triglyceride"],
    "과식": ["fbs", "bmi"],
    "음주": ["systolic", "ggt", "fbs"],
    "스트레스": ["systolic", "diastolic"],
    "숙면": ["systolic", "fbs"],
    "피곤": ["systolic"],
    "수분": ["systolic", "fbs"],
}

METRIC_KO = {"systolic": "수축기혈압", "diastolic": "이완기혈압", "fbs": "공복혈당",
             "bmi": "체질량지수", "triglyceride": "중성지방", "ggt": "감마지티피"}


def _tag(memo: str):
    if not memo:
        return None
    for tag, kws in EVENT_KEYWORDS:
        if any(k in memo for k in kws):
            return tag
    return None


def _t_sf(t, dfree):
    """t 분포 생존함수(단측) 근사. scipy 없이.

    dfree 가 충분히 크면 정규근사, 작으면 보정.
    """
    t = abs(t)
    # 정규분포 생존함수 (Abramowitz-Stegun 근사)
    z = t * (1 - 1 / (4 * dfree)) / np.sqrt(1 + t * t / (2 * dfree)) if dfree > 0 else t
    # Φ(-z)
    return 0.5 * _erfc(z / np.sqrt(2))


def _erfc(x):
    # Abramowitz & Stegun 7.1.26
    t = 1.0 / (1.0 + 0.3275911 * abs(x))
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return 1.0 - y if x >= 0 else 1.0 + y


def _bh(pvals):
    """Benjamini–Hochberg 보정 q값."""
    n = len(pvals)
    order = np.argsort(pvals)
    q = np.empty(n)
    prev = 1.0
    for rank in range(n - 1, -1, -1):
        i = order[rank]
        val = pvals[i] * n / (rank + 1)
        prev = min(prev, val)
        q[i] = prev
    return q


def behavior_insights(checkups, min_n=4):
    """checkups: Checkup ORM 리스트. 반환: 유의성 판정이 붙은 효과 목록."""
    recs = sorted(checkups, key=lambda c: c.quarter)
    if len(recs) < min_n + 2:
        return {"available": False, "reason": "기록이 적어 검정 불가"}

    # 각 (검진, 지표)에 태그를 붙여, 지표별로 '태그 시점'과 '비태그(그 태그가 아닌) 시점'을 나눈다.
    #   비교는 Welch 2표본 t-test (태그 그룹 vs 나머지). 전체 평균을 base로 쓰면
    #   태그 시점이 base에 섞여 대비가 죽으므로, 반드시 나머지와 비교한다.
    per_metric = {}     # m -> list of (tag_or_None, value)
    for c in recs:
        tag = _tag(c.memo or "")
        for m in set(sum(TAG_METRICS.values(), [])):
            v = getattr(c, m, None)
            if v is not None:
                per_metric.setdefault(m, []).append((tag, float(v)))

    raw = []
    for tag, metrics in TAG_METRICS.items():
        for m in metrics:
            seq = per_metric.get(m)
            if not seq:
                continue
            grp = np.array([v for t, v in seq if t == tag])
            rest = np.array([v for t, v in seq if t != tag])
            if len(grp) < min_n or len(rest) < 2:
                continue
            n1, n2 = len(grp), len(rest)
            m1, m2 = grp.mean(), rest.mean()
            v1 = grp.var(ddof=1) if n1 > 1 else 0.0
            v2 = rest.var(ddof=1) if n2 > 1 else 0.0
            se = np.sqrt(v1 / n1 + v2 / n2)
            if se == 0:
                continue
            delta = m1 - m2
            t = delta / se
            # Welch–Satterthwaite 자유도
            dfree = (v1 / n1 + v2 / n2) ** 2 / (
                (v1 / n1) ** 2 / max(n1 - 1, 1) + (v2 / n2) ** 2 / max(n2 - 1, 1))
            p = 2 * _t_sf(t, dfree)
            ci = 1.96 * se
            raw.append({"tag": tag, "metric": m, "metric_ko": METRIC_KO.get(m, m),
                        "n": int(n1), "delta": round(float(delta), 1),
                        "ci_low": round(float(delta - ci), 1),
                        "ci_high": round(float(delta + ci), 1),
                        "direction": "증가" if delta > 0 else "감소",
                        "p": float(p)})

    if not raw:
        return {"available": True, "effects": [], "reason": "검정 가능한 반복 행동 없음"}

    qs = _bh(np.array([r["p"] for r in raw]))
    for r, q in zip(raw, qs):
        r["p_adj"] = round(float(q), 4)
        r["p"] = round(r["p"], 4)
        # 유의 + 신뢰구간이 0을 넘지 않음
        sig = q < 0.05 and (r["ci_low"] > 0 or r["ci_high"] < 0)
        r["verdict"] = "유의" if sig else "판단보류"

    raw.sort(key=lambda r: (r["verdict"] != "유의", r["p_adj"]))
    return {"available": True, "effects": raw,
            "n_significant": sum(1 for r in raw if r["verdict"] == "유의")}
