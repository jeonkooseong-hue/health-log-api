"""국내(아시아인) 기준 건강 판정 로직.

기준 출처: 국민건강보험공단 건강검진 기준 / 대사증후군(NCEP ATP III 아시아 기준).
※ 학습·데모용 단순화 구현이며 의료기기·진단 도구가 아님.
"""

HEALTHY, CAUTION, RISK = "정상", "경계", "위험"


# ---------- 항목별 판정 ----------

def judge_bp(sys_v, dia_v):
    if sys_v is None or dia_v is None:
        return None
    if sys_v >= 140 or dia_v >= 90:
        return RISK          # 고혈압
    if sys_v >= 120 or dia_v >= 80:
        return CAUTION       # 고혈압 전단계
    return HEALTHY


def judge_fbs(v):
    """공복혈당."""
    if v is None:
        return None
    if v >= 126:
        return RISK          # 당뇨병 의심
    if v >= 100:
        return CAUTION       # 공복혈당장애
    return HEALTHY


def judge_bmi(v):
    """아시아인 기준."""
    if v is None:
        return None
    if v >= 25.0:
        return RISK          # 비만
    if v >= 23.0:
        return CAUTION       # 과체중
    if v < 18.5:
        return CAUTION       # 저체중도 주의
    return HEALTHY


def judge_waist(v, sex):
    """복부비만: 남 ≥90, 여 ≥85."""
    if v is None or sex not in ("M", "F"):
        return None
    cut = 90 if sex == "M" else 85
    return RISK if v >= cut else HEALTHY


def judge_ldl(v):
    if v is None:
        return None
    if v >= 160:
        return RISK          # 이상지질혈증
    if v >= 130:
        return CAUTION
    return HEALTHY


def judge_hdl(v):
    if v is None:
        return None
    if v < 40:
        return RISK          # 남녀 공통 위험 수치
    if v < 60:
        return CAUTION
    return HEALTHY


def judge_tg(v):
    """중성지방."""
    if v is None:
        return None
    if v >= 200:
        return RISK
    if v >= 150:
        return CAUTION
    return HEALTHY


# ---------- 대사증후군 (5개 중 3개 이상) ----------

def metabolic_syndrome(waist=None, sex=None, tg=None, hdl=None,
                       sys_v=None, dia_v=None, fbs=None):
    """대사증후군 진단: 아래 5개 중 3개 이상 해당."""
    items = []

    # 1. 복부비만
    if waist is not None and sex in ("M", "F"):
        cut = 90 if sex == "M" else 85
        items.append({"name": "복부비만", "hit": waist >= cut,
                      "detail": f"허리둘레 {waist}cm (기준 {cut}cm)"})
    # 2. 높은 중성지방
    if tg is not None:
        items.append({"name": "높은 중성지방", "hit": tg >= 150,
                      "detail": f"중성지방 {tg} mg/dL (기준 150)"})
    # 3. 낮은 HDL (남<40, 여<50)
    if hdl is not None and sex in ("M", "F"):
        cut = 40 if sex == "M" else 50
        items.append({"name": "낮은 HDL", "hit": hdl < cut,
                      "detail": f"HDL {hdl} mg/dL (기준 {cut})"})
    # 4. 높은 혈압
    if sys_v is not None and dia_v is not None:
        items.append({"name": "높은 혈압", "hit": sys_v >= 130 or dia_v >= 85,
                      "detail": f"{sys_v}/{dia_v} mmHg (기준 130/85)"})
    # 5. 높은 혈당
    if fbs is not None:
        items.append({"name": "높은 혈당", "hit": fbs >= 100,
                      "detail": f"공복혈당 {fbs} mg/dL (기준 100)"})

    hits = [i for i in items if i["hit"]]
    return {
        "criteria": items,
        "hit_count": len(hits),
        "evaluable": len(items),
        "diagnosed": len(hits) >= 3,
        "hit_names": [i["name"] for i in hits],
    }


# ---------- 원인 추론 (IF-THEN) ----------

def infer_cause(bmi=None, smoking=None, family_history=None,
                fbs=None, ldl=None, sys_v=None, dia_v=None):
    """질병 '원인 유형' 추론: 유전 우세 / 행태 우세 / 복합 / 판단보류.

    smoking: 0(안 피움) 1(끊음) 2(현재 흡연)
    family_history: 0/1
    """
    bmi_ok = bmi is not None and 18.5 <= bmi < 23.0
    smoker = smoking == 2
    fam = family_history == 1
    metab_bad = (judge_fbs(fbs) == RISK) or (judge_ldl(ldl) == RISK)
    bp_bad = judge_bp(sys_v, dia_v) in (CAUTION, RISK)
    obese = bmi is not None and bmi >= 25.0

    # 유전 우세: 생활습관 양호(정상 BMI·비흡연)한데도 대사 지표 위험 + 가족력 있음
    if bmi_ok and not smoker and metab_bad and fam:
        return {
            "type": "유전적 요인 우세",
            "reason": "BMI 정상·비흡연인데도 혈당/LDL이 위험 수준이고 가족력이 있습니다.",
            "action": "생활습관 교정만으로 한계가 있을 수 있어 약물·전문 상담 검토가 필요합니다.",
        }
    # 행태 우세: 가족력 없이 흡연 또는 비만 + 혈압·LDL 동시 악화
    if (not fam) and (smoker or obese) and bp_bad and (judge_ldl(ldl) in (CAUTION, RISK)):
        return {
            "type": "행태적 요인 우세",
            "reason": "가족력은 없으나 " + ("흡연" if smoker else "비만") + " 상태에서 혈압과 LDL이 동시에 악화됐습니다.",
            "action": "금연·체중 감량·식이 조절 등 생활습관 개입 효과가 클 것으로 보입니다.",
        }
    # 복합
    if fam and (smoker or obese) and (metab_bad or bp_bad):
        return {
            "type": "복합 요인",
            "reason": "가족력과 생활습관 위험요인(흡연/비만)이 함께 있습니다.",
            "action": "생활습관 교정과 정기 추적을 병행해야 합니다.",
        }
    return {
        "type": "판단 보류",
        "reason": "뚜렷한 원인 패턴이 관찰되지 않거나 필요한 정보(가족력·흡연·지질)가 부족합니다.",
        "action": "추가 검진 정보 확보 후 재평가가 필요합니다.",
    }


def overall_grade(judgements):
    """항목별 판정 리스트 → 종합 등급."""
    vals = [v for v in judgements if v]
    if not vals:
        return None
    if RISK in vals:
        return RISK
    if CAUTION in vals:
        return CAUTION
    return HEALTHY
