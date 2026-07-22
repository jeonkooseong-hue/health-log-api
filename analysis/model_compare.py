"""임베딩 모델 5종 비교: 유사도 분리도 + 다운스트림 성능 + 에포크 체크.

배포 이미지 밖(analysis/) 전용. 필요: scikit-learn, gensim
과제:
  (1) 유사도 검사 - 같은 이벤트 메모끼리 얼마나 가깝게 임베딩되나 (intra - inter cosine)
  (2) 다운스트림 - 메모 임베딩 → '개인 평소 대비 혈당 상승/평소/하강' 분류 정확도
  (3) 에포크 체크 - 학습형 모델(W2V/FastText/Doc2Vec)의 epoch별 성능 곡선
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import normalize
from gensim.models import Word2Vec, FastText, Doc2Vec
from gensim.models.doc2vec import TaggedDocument

from database import SessionLocal, Record
from seed import PHRASES

DIM = 50
EPOCH_GRID = [1, 3, 5, 10, 20, 40]
EMPTY = "없음"

# 메모 → 이벤트 라벨 역매핑
MEMO2EVENT = {}
for ev, plist in PHRASES.items():
    for p in plist:
        MEMO2EVENT[p if p else EMPTY] = ev


def tok(s):
    return (s or EMPTY).split() or [EMPTY]


def load():
    db = SessionLocal()
    by_user = defaultdict(list)
    for r in db.query(Record).all():
        by_user[r.user_id].append(r)
    db.close()
    memos, labels = [], []
    for recs in by_user.values():
        vals = np.array([r.blood_sugar for r in recs], float)
        med, sd = np.median(vals), (vals.std() or 1)
        for r in recs:
            m = r.memo if r.memo else EMPTY
            v = r.blood_sugar
            lab = "상승" if v > med + 0.5 * sd else ("하강" if v < med - 0.5 * sd else "평소")
            memos.append(m)
            labels.append(lab)
    return memos, labels


def separation(vecs, events):
    """같은 이벤트끼리 코사인 유사도 - 다른 이벤트끼리 (높을수록 의미 분리 잘됨)."""
    V = normalize(np.asarray(vecs, dtype=float))
    S = V @ V.T
    ev = np.array(events)
    same = ev[:, None] == ev[None, :]
    iu = np.triu_indices(len(ev), k=1)
    s, d = S[iu], same[iu]
    return float(s[d].mean() - s[~d].mean())


def downstream(vec_of, memos, labels):
    X = np.asarray([vec_of[m] for m in memos], dtype=float)
    Xtr, Xte, ytr, yte = train_test_split(X, labels, test_size=0.25, random_state=0, stratify=labels)
    clf = LogisticRegression(max_iter=2000).fit(Xtr, ytr)
    return accuracy_score(yte, clf.predict(Xte))


def main():
    memos, labels = load()
    uniq = sorted(set(memos))
    events = [MEMO2EVENT.get(m, "기타") for m in uniq]
    corpus = [tok(m) for m in memos]           # 학습용 (18k 문서)
    print(f"기록 {len(memos)} · 고유 메모 {len(uniq)} · 이벤트 {len(set(events))}종")
    base = max(labels.count(l) for l in set(labels)) / len(labels)
    print(f"다수클래스 baseline acc = {base:.3f}\n")

    results = []

    # 1) TF-IDF
    tf = TfidfVectorizer(token_pattern=r"(?u)\b\w+\b")
    M = tf.fit_transform(uniq).toarray()
    vec = dict(zip(uniq, M))
    results.append(("TF-IDF", separation(M, events), downstream(vec, memos, labels), "-"))

    # 2) LSA (TF-IDF → SVD)
    k = min(DIM, M.shape[1] - 1)
    L = TruncatedSVD(n_components=k, random_state=0).fit_transform(M)
    vec = dict(zip(uniq, L))
    results.append((f"LSA(SVD-{k})", separation(L, events), downstream(vec, memos, labels), "-"))

    # 3~5) 학습형: epoch별
    for name in ("Word2Vec", "FastText", "Doc2Vec"):
        print(f"== {name} 에포크 체크 ==")
        best = (None, -1, -1)
        for ep in EPOCH_GRID:
            if name == "Doc2Vec":
                docs = [TaggedDocument(t, [i]) for i, t in enumerate(corpus)]
                m = Doc2Vec(docs, vector_size=DIM, min_count=1, epochs=ep, workers=2, seed=0)
                V = np.array([m.infer_vector(tok(u)) for u in uniq])
            else:
                Model = Word2Vec if name == "Word2Vec" else FastText
                m = Model(corpus, vector_size=DIM, min_count=1, epochs=ep, workers=2, seed=0, sg=1)
                V = np.array([np.mean([m.wv[w] for w in tok(u) if w in m.wv] or [np.zeros(DIM)], axis=0) for u in uniq])
            vec = dict(zip(uniq, V))
            sep = separation(V, events)
            acc = downstream(vec, memos, labels)
            print(f"   epoch={ep:>3}  분리도={sep:+.3f}  acc={acc:.3f}")
            if acc > best[2]:
                best = (ep, sep, acc)
        results.append((name, best[1], best[2], f"epoch={best[0]}"))
        print()

    print("=" * 62)
    print(f"{'모델':16}{'유사도 분리도':>14}{'다운스트림 acc':>15}{'최적':>10}")
    for n, s, a, e in results:
        print(f"{n:16}{s:>+14.3f}{a:>15.3f}{e:>10}")


if __name__ == "__main__":
    main()
