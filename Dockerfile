# 1. 베이스 이미지: 파이썬 3.13 (가벼운 slim 버전)
FROM python:3.13-slim

# 2. 컨테이너 안 작업 폴더
WORKDIR /app

# 3. 패키지 목록 먼저 복사 후 설치 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 나머지 소스코드 복사
COPY . .

# 5. 8000번 포트 사용 알림
EXPOSE 8000

# 6. 서버 실행 (외부 접속 허용하려면 host 0.0.0.0)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
