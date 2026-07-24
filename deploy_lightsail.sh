#!/usr/bin/env bash
# 라이트세일 Container Service 배포 스크립트
#
# 사전 준비:
#   1) aws CLI 설치 + aws configure (region: ap-northeast-2 권장)
#   2) docker build -t health-log-api .  (이미 완료)
#   3) 아래 SECRET_KEY / OPENAI_API_KEY 를 환경변수로 넣고 실행:
#        SECRET_KEY=아무긴문자열 OPENAI_API_KEY=sk-... bash deploy_lightsail.sh
#      (키를 이 파일에 직접 쓰지 말 것 — 커밋 시 유출)
set -euo pipefail

# aws / lightsailctl 경로 (winget·수동 설치 위치)
export PATH="$PATH:/c/Program Files/Amazon/AWSCLIV2:$LOCALAPPDATA/lightsailctl"

SERVICE=health-log
REGION=${AWS_REGION:-ap-northeast-2}
POWER=micro          # 1GB RAM (pandas/sklearn 로드에 nano 512MB 는 부족)
SCALE=1
IMAGE=health-log-api:latest

: "${SECRET_KEY:?SECRET_KEY 환경변수를 지정하세요}"
: "${OPENAI_API_KEY:=}"   # 없으면 규칙 템플릿으로 동작

echo "== 1. Container Service 생성 (이미 있으면 건너뜀) =="
aws lightsail get-container-services --service-name "$SERVICE" --region "$REGION" >/dev/null 2>&1 \
  || aws lightsail create-container-service \
       --service-name "$SERVICE" --power "$POWER" --scale "$SCALE" --region "$REGION"

echo "== 2. 서비스 ACTIVE 대기 =="
until [ "$(aws lightsail get-container-services --service-name "$SERVICE" --region "$REGION" \
        --query 'containerServices[0].state' --output text)" = "ACTIVE" ]; do
  echo "  ...서비스 준비 중"; sleep 15
done

echo "== 3. 이미지 push (787MB, 수 분 소요) =="
PUSH_OUT=$(aws lightsail push-container-image \
  --service-name "$SERVICE" --label app --image "$IMAGE" --region "$REGION" 2>&1)
echo "$PUSH_OUT"
# push 출력에서 이미지 참조 추출 (예: :health-log.app.7)
IMAGE_REF=$(echo "$PUSH_OUT" | grep -oE ':'"$SERVICE"'\.app\.[0-9]+' | tail -1)
echo "이미지 참조: $IMAGE_REF"

echo "== 4. 배포 정의 작성 =="
DEPLOY_JSON=$(mktemp)
cat > "$DEPLOY_JSON" <<JSON
{
  "serviceName": "$SERVICE",
  "containers": {
    "app": {
      "image": "$IMAGE_REF",
      "ports": { "8000": "HTTP" },
      "environment": {
        "SECRET_KEY": "$SECRET_KEY",
        "OPENAI_API_KEY": "$OPENAI_API_KEY",
        "TOKEN_EXPIRE_HOURS": "12"
      }
    }
  },
  "publicEndpoint": {
    "containerName": "app",
    "containerPort": 8000,
    "healthCheck": { "path": "/", "successCodes": "200", "intervalSeconds": 15 }
  }
}
JSON

echo "== 5. 배포 실행 =="
aws lightsail create-container-service-deployment \
  --region "$REGION" --cli-input-json "file://$DEPLOY_JSON"
rm -f "$DEPLOY_JSON"

echo "== 6. 배포 완료 대기 + URL =="
until [ "$(aws lightsail get-container-services --service-name "$SERVICE" --region "$REGION" \
        --query 'containerServices[0].currentDeployment.state' --output text)" = "ACTIVE" ]; do
  echo "  ...배포 진행 중"; sleep 20
done
URL=$(aws lightsail get-container-services --service-name "$SERVICE" --region "$REGION" \
        --query 'containerServices[0].url' --output text)
echo ""
echo "========================================"
echo " 배포 완료:  $URL"
echo "  대시보드:  ${URL}dashboard   (admin / admin1234)"
echo "  사용자:    ${URL}ui"
echo "  API 문서:  ${URL}docs"
echo "========================================"
