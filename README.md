# 마이 헬스 로그 API

> 매일의 건강 수치를 기록하면 BMI를 자동 계산하고 혈압·혈당을 분류해 경고를 알려주는, 로그인 기반 개인 건강 관리 REST API.

회원가입·로그인 후 건강 수치(몸무게·키·혈압·혈당 등)를 저장하면 서버가 BMI 계산, 혈압/혈당 분류, 경고 생성을 자동으로 처리하고, 쌓인 기록으로 검색·통계·주간 리포트를 제공합니다. 데이터는 SQLite 데이터베이스에 저장되며, 기록은 로그인한 사용자별로 분리됩니다.

> ⚠️ 학습용 프로젝트입니다. 건강 분류 기준은 학습을 위해 단순화한 값으로, 실제 의학적 진단이 아닙니다.

## 데이터 모델

데이터베이스 구조(ERD)와 DB↔API 스키마 매핑은 [`docs/ERD.md`](docs/ERD.md) 참고. ErdCloud import용 DDL은 [`docs/schema.sql`](docs/schema.sql).

- **USER** 1 : N **RECORD** (사용자 한 명이 여러 기록을 가짐)
- **USER** 1 : N **ACTIVITY_LOG** (로그인·기록 변경 활동 로그)

## 기능 목록 (엔드포인트)

### 인증

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/signup` | 회원가입 (비밀번호는 bcrypt 해시로 저장, 첫 가입자는 관리자) |
| POST | `/login` | 로그인, JWT 토큰 발급 |
| GET | `/me` | 내 정보 조회 (아이디·권한) |

### 건강 기록 (로그인 필요, 본인 기록만)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/records` | 건강 기록 추가 (BMI·분류·경고 자동 계산) |
| GET | `/records` | 내 기록 전체 조회 (개수 포함) |
| GET | `/records/{id}` | 내 기록 하나 조회 (없으면 404) |
| PUT | `/records/{id}` | 기록 수정 |
| DELETE | `/records/{id}` | 기록 삭제 |
| GET | `/search?start=&end=` | 날짜 범위로 검색 |
| GET | `/stats` | 평균 체중·BMI 등 통계 |
| GET | `/report/weekly` | 최근 7일 vs 지난주 평균 체중 변화 |
| GET | `/ui` | 로그인·기록 입력·조회·통계 HTML 화면 |

### 관리자 (admin · superadmin)

| 메서드 | 경로 | 권한 | 설명 |
|--------|------|------|------|
| GET | `/admin/users` | admin+ | 전체 사용자 목록 (기록 수 포함) |
| GET | `/admin/logs` | admin+ | 활동 로그 조회 |
| GET | `/admin/stats` | admin+ | 전체 사용자·기록·로그 수 |
| PUT | `/admin/users/{id}/role` | superadmin | 사용자 권한 변경 |
| GET | `/dashboard` | — | 관리자 대시보드 화면 |

> 인증 창구는 `Authorization: Bearer <토큰>` 헤더가 필요합니다. `/docs` 의 **Authorize** 버튼으로도 테스트할 수 있습니다.

### 권한 체계

`user` < `admin` < `superadmin`. **첫 가입자는 자동으로 superadmin**, 이후 가입은 모두 user.

- **user**: 본인 기록만 CRUD
- **admin**: 관리자 대시보드 조회 (사용자·로그·통계)
- **superadmin**: 조회 + 사용자 권한 변경(승격/강등). 마지막 superadmin은 강등 불가

### 주요 기능

- **로그인/회원가입**: OAuth2 password flow + JWT 토큰, bcrypt 비밀번호 해시.
- **사용자별 기록 분리**: 로그인한 사용자의 기록만 조회·수정·삭제 가능.
- **날짜 사이드바**: 기록 날짜를 클릭해 그날의 상세(수치·분류·메모·경고) 조회.
- **관리자 대시보드** (`/dashboard`): 자체 로그인, KPI·막대차트·사용자표·활동 피드. superadmin은 권한 변경.
- **활동 로그**: 회원가입·로그인·기록 변경·권한 변경을 자동 기록.
- **주간 리포트**: 최근 7일과 지난주 평균 체중을 비교해 변화량 반환.

## 분류 기준

- **BMI**: 저체중(<18.5) / 정상(18.5~22.9) / 과체중(23~24.9) / 비만(≥25)
- **혈압**: 정상(수축기<120 그리고 이완기<80) / 주의 / 고혈압(수축기≥140 또는 이완기≥90)
- **공복혈당**: 정상(<100) / 공복혈당장애(100~125) / 당뇨 의심(≥126)

## 실행 방법

### 로컬 실행

```bash
# 가상환경 생성 & 활성화 (Windows)
python -m venv venv
venv\Scripts\activate

# 패키지 설치
pip install -r requirements.txt

# 서버 실행
uvicorn main:app --reload
```

접속: http://127.0.0.1:8000/docs · 화면: http://127.0.0.1:8000/ui

### Docker 실행

```bash
docker build -t health-log-api .
docker run -d -p 8000:8000 health-log-api
```

접속: http://127.0.0.1:8000/docs

## 기술 스택

- Python 3.13
- FastAPI · Uvicorn
- Pydantic (요청 검증)
- SQLAlchemy · SQLite (데이터베이스)
- PyJWT (토큰) · bcrypt (비밀번호 해시)
- Docker

## 프로젝트 구조

```
main.py        API 엔드포인트 + Pydantic 스키마 + 건강 계산 로직
database.py    SQLite 연결 + 테이블(User, Record) 정의
auth.py        비밀번호 해시, JWT 토큰, 로그인 확인
page.html      /ui 화면
docs/ERD.md    데이터 모델(ERD) 문서
Dockerfile     이미지 빌드 설계도
```

## 배포 URL

<!-- 배포했다면 여기에 접속 주소 적기 -->
(배포 후 작성)
