"""메모 → '개인 평소 대비' 지표 변화 예측 (신호 검증 + 해석).

배포 이미지 밖(analysis/)에서만 실행. sklearn 필요.
핵심: 개인 기저치를 통제(각자 중앙값 대비)하면 메모의 인과 신호가 드러난다.
"""
import os
import sys
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score

from database import SessionLocal, Record

TOK = r"(?u)\b\w+\b"


def load():
    db = SessionLocal()
    by_user = defaultdict(list)
    for r in db.query(Record).all():
        by_user[r.user_id].append(r)
    db.close()
    return by_user


def build(by_user, field):
    """feature = 오늘 메모, target = 개인 중앙값 대비 상승/평소/하강."""
    X, y = [], []
    for recs in by_user.values():
        vals = np.array([getattr(r, field) for r in recs], float)
        med = np.median(vals)
        sd = vals.std() or 1
        for r in recs:
            v = getattr(r, field)
            if v > med + 0.5 * sd:
                lab = "상승"
            elif v < med - 0.5 * sd:
                lab = "하강"
            else:
                lab = "평소"
            X.append(r.memo or "없음")
            y.append(lab)
    return X, y


def run(title, X, y, vec):
    cnt = Counter(y)
    maj = cnt.most_common(1)[0][1] / len(y)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=0, stratify=y)
    Vtr = vec.fit_transform(Xtr)
    clf = LogisticRegression(max_iter=1000).fit(Vtr, ytr)
    pred = clf.predict(vec.transform(Xte))
    acc = accuracy_score(yte, pred)
    f1 = f1_score(yte, pred, average="macro")
    print(f"\n== {title} ==")
    print(f"  다수클래스 baseline acc={maj:.3f}  →  모델 acc={acc:.3f}  macroF1={f1:.3f}")
    # 해석: '상승' 클래스에 기여하는 메모 상위
    feats = np.array(vec.get_feature_names_out())
    if "상승" in list(clf.classes_):
        idx = list(clf.classes_).index("상승")
        coef = clf.coef_[idx]
        top = feats[np.argsort(coef)[::-1][:4]]
        bot = feats[np.argsort(coef)[:4]]
        print(f"  '상승' ↑ 견인 메모: {list(top)}")
        print(f"  '상승' ↓ 억제 메모: {list(bot)}")


def main():
    by_user = load()
    run("혈당(blood_sugar) — 메모→개인 대비 변화", *build(by_user, "blood_sugar"), CountVectorizer(token_pattern=TOK))
    run("수축기 혈압(systolic) — 메모→개인 대비 변화", *build(by_user, "systolic"), CountVectorizer(token_pattern=TOK))


if __name__ == "__main__":
    main()
