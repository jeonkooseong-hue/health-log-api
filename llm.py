"""숫자(위험확률 + 행동 인사이트)를 자연어 소견으로 바꾼다.

원칙:
  - LLM 은 판단하지 않는다. 이미 계산된 숫자를 문장으로 옮기기만 한다.
  - verdict 가 '유의'인 효과만 언급한다. '판단보류'는 언급 금지.
  - OPENAI_API_KEY 가 없으면 규칙 기반 템플릿(mock)으로 문장을 만든다.
    → 키 없이도 전체 파이프라인이 동작하고, 키를 넣으면 자동으로 GPT 사용.
"""
import os
import json

# .env 파일에서 OPENAI_API_KEY 등을 읽어온다 (있으면).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_SYSTEM = (
    "너는 건강검진 데이터를 요약하는 임상 보조 도우미다. "
    "반드시 주어진 JSON 숫자만 사용하고, 없는 내용을 지어내지 마라. "
    "위험 전환 확률과 '유의'로 표시된 행동 효과만 언급하라. "
    "'판단보류' 항목은 언급하지 마라. "
    "3~4문장 한국어. 담당 의료진에게 보고하는 톤. 진단·처방 단정은 피하고 경향만 서술하라."
)


def _has_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def make_narrative(payload: dict) -> dict:
    """payload = {patient, risk, insights}. 반환 {source, text}."""
    if _has_key():
        try:
            return {"source": "gpt", "text": _via_gpt(payload)}
        except Exception as e:
            return {"source": "mock", "text": _via_template(payload),
                    "note": f"GPT 호출 실패 → 템플릿 사용 ({type(e).__name__})"}
    return {"source": "mock", "text": _via_template(payload)}


def _via_gpt(payload: dict) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    r = client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        temperature=0.3, max_tokens=400)
    return r.choices[0].message.content.strip()


def _via_template(payload: dict) -> str:
    """키 없을 때 규칙 기반 문장. GPT와 같은 규율(유의만 언급)을 따른다."""
    p = payload.get("patient", {})
    risk = payload.get("risk", {})
    effects = [e for e in payload.get("insights", {}).get("effects", [])
               if e.get("verdict") == "유의"]

    parts = []
    who = f"{p.get('name','환자')}({p.get('age','?')}세 {'남성' if p.get('sex')=='M' else '여성'})"

    if risk.get("available") and risk.get("eligible") and risk.get("prob") is not None:
        pct = risk["prob"] * 100
        base = (risk.get("baseline") or 0) * 100
        level = "높음" if pct >= base * 2 else "보통" if pct >= base else "낮음"
        parts.append(f"{who}의 다음 분기 위험군 전환 확률은 {pct:.0f}%로 "
                     f"평균({base:.0f}%) 대비 {level} 수준입니다.")
        drv = risk.get("drivers") or []
        if drv:
            parts.append("위험을 끌어올리는 주요 지표는 "
                         + ", ".join(d["feature"] for d in drv[:2]) + " 입니다.")
    elif risk.get("available") and not risk.get("eligible"):
        parts.append(f"{who}는 현재 위험군으로, 전환 예측보다 직접 관리 대상입니다.")

    if effects:
        for e in effects[:2]:
            arrow = "낮추는" if e["direction"] == "감소" else "높이는"
            parts.append(f"'{e['tag']}' 기록이 있던 시점에 {e['metric_ko']}이 평소보다 "
                         f"{abs(e['delta']):.0f} {arrow} 경향이 관찰됩니다 "
                         f"(n={e['n']}, 보정 p={e['p_adj']}).")
    elif risk.get("available"):
        parts.append("통계적으로 유의한 생활습관–지표 연관은 관찰되지 않았습니다.")

    return " ".join(parts) if parts else "분석할 데이터가 부족합니다."
