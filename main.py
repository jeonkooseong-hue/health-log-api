from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import json
import os

app = FastAPI(title="마이 헬스 로그 API", version="1.0")

DATA_FILE = "data.json"  # 기록을 저장할 파일 이름


def load_data():
    """서버 시작 시 파일에서 기록을 불러온다. 파일 없으면 빈 목록."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_data():
    """현재 기록(records)을 파일에 저장한다."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


records = load_data()  # 시작할 때 파일에서 불러오기 (재시작해도 유지)


class RecordIn(BaseModel):
    date: str
    weight: float
    height: float
    systolic: int
    diastolic: int
    blood_sugar: int
    steps: int = 0
    sleep_hours: float = 0.0
    memo: str = ""


# ===== 건강 계산 함수들 =====

def calc_bmi(weight, height):
    height_m = height / 100         # cm → m (175 → 1.75)
    bmi = weight / (height_m ** 2)  # 몸무게 ÷ (키 × 키)
    return round(bmi, 1)            # 소수 첫째자리 반올림


def classify_bmi(bmi):
    if bmi < 18.5:
        return "저체중"
    elif bmi < 23:
        return "정상"
    elif bmi < 25:
        return "과체중"
    else:
        return "비만"
    
def classify_bp(systolic, diastolic):
    if systolic >= 140 or diastolic >= 90:
        return "고혈압"
    elif systolic < 120 and diastolic < 80:
        return "정상"
    else:
        return "주의"

def classify_sugar(blood_sugar):
    if blood_sugar < 100:
        return "정상"
    elif blood_sugar < 126:
        return "공복혈당장애"
    else:
        return "당뇨 의심"
    
def make_warnings(bmi_cat, bp_cat, sugar_cat):
    warnings = []                          # 빈 경고 목록
    if bmi_cat == "비만":
        warnings.append("비만 주의")
    if bp_cat == "고혈압":
        warnings.append("고혈압 주의")
    if sugar_cat == "당뇨 의심":
        warnings.append("당뇨 의심 주의")
    return warnings                        # 목록 반환 (없으면 빈 [])


def add_health_info(rec):
    """기록 딕셔너리(rec)에 bmi/분류/경고 칸을 채워 넣는다."""
    bmi = calc_bmi(rec["weight"], rec["height"])
    rec["bmi"] = bmi
    rec["bmi_category"] = classify_bmi(bmi)
    rec["bp_category"] = classify_bp(rec["systolic"], rec["diastolic"])
    rec["sugar_category"] = classify_sugar(rec["blood_sugar"])
    rec["warnings"] = make_warnings(
        rec["bmi_category"],
        rec["bp_category"],
        rec["sugar_category"],
    )
    return rec


@app.get("/")
def read_root():
    return {"message": "마이 헬스 로그 API"}


next_id = max([r["id"] for r in records], default=0) + 1  # 이어서 번호 부여


@app.post("/records")
def add_record(record: RecordIn):
    global next_id
    new_record = record.model_dump()   # 받은 데이터를 딕셔너리로 변환
    new_record["id"] = next_id         # 고유번호 붙이기
    next_id += 1                       # 다음 번호 준비
    add_health_info(new_record)        # BMI/분류/경고 자동 계산해 칸 추가
    records.append(new_record)         # 리스트에 추가(저장)
    save_data()                        # 파일에 저장
    return new_record                  # 저장된 것 돌려주기

@app.get("/search")
def search_records(start: str, end: str):
    result = []
    for r in records:
        if start <= r["date"] <= end:
            result.append(r)
    return {"count": len(result), "records": result}

@app.get("/records")
def get_records():
    return {"count": len(records), "records": records}
# TODO: GET    /records/{record_id} - 단건 조회 (없으면 404)
@app.get("/records/{record_id}")
def get_one(record_id: int):
    for r in records:
        if r["id"] == record_id:
            return r
    raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다")
# TODO: PUT    /records/{record_id} - 수정
@app.put("/records/{record_id}")
def update_record(record_id: int, record: RecordIn):
    for i, r in enumerate(records):
        if r["id"] == record_id:
            updated = record.model_dump()
            updated["id"] = record_id
            add_health_info(updated)       # 수정해도 BMI/분류/경고 재계산
            records[i] = updated
            save_data()                    # 파일에 저장
            return updated
    raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다")
# TODO: DELETE /records/{record_id} - 삭제
@app.delete("/records/{record_id}")
def delete_record(record_id:int):
    for r in records:
        if r["id"] ==record_id:
            records.remove(r)
            save_data()                    # 파일에 저장
            return{'message':'삭제됨', 'id':record_id}
    raise HTTPException(status_code=404, detail='기록을 찾을 수 없습니다')
@app.get("/search")
def search_records(start: str, end: str):
    # ?start=2026-07-01&end=2026-07-31 처럼 날짜 범위를 받는다
    result = []
    for r in records:
        if start <= r["date"] <= end:   # 날짜가 start~end 사이면
            result.append(r)
    return {"count": len(result), "records": result}


@app.get("/stats")
def get_stats():
    if not records:                     # 기록이 하나도 없으면
        return {"count": 0, "message": "기록이 없습니다"}
    weights = [r["weight"] for r in records]   # 몸무게만 모은 목록
    bmis = [r["bmi"] for r in records]         # bmi만 모은 목록
    return {
        "count": len(records),
        "avg_weight": round(sum(weights) / len(weights), 1),
        "avg_bmi": round(sum(bmis) / len(bmis), 1),
        "min_weight": min(weights),
        "max_weight": max(weights),
    }
