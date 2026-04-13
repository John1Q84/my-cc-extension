#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Claude Code Slack Approval - 배포 스크립트
#
# 사용법: ./code/script/deploy.sh
#
# 실행 전 환경변수 설정 필요:
#   SLACK_APPROVAL_BOT_TOKEN, SLACK_APPROVAL_CHANNEL_ID, TF_VAR_slack_signing_secret
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TERRAFORM_DIR="$PROJECT_ROOT/code/terraform"
AWS_REGION="${SLACK_APPROVAL_AWS_REGION:-ap-northeast-2}"

# ── Step 1: 필수 도구 확인 ──────────────────────────────────────────────────

echo "=== Step 1: 필수 도구 확인 ==="

check_command() {
    local cmd=$1
    local install_hint=$2
    if ! command -v "$cmd" &> /dev/null; then
        echo "  ERROR: $cmd 이 설치되어 있지 않습니다."
        echo "         설치: $install_hint"
        return 1
    fi
    echo "  $cmd: OK"
}

MISSING=0
check_command python3 "brew install python" || MISSING=1
check_command terraform "brew install terraform" || MISSING=1
check_command aws "brew install awscli" || MISSING=1
check_command jq "brew install jq" || MISSING=1
check_command curl "(macOS 기본 포함)" || MISSING=1

if [ $MISSING -ne 0 ]; then
    echo ""
    echo "누락된 도구를 설치한 후 다시 실행하세요."
    exit 1
fi

# ── Step 2: 환경변수 확인 ────────────────────────────────────────────────────

echo ""
echo "=== Step 2: 환경변수 확인 ==="

MISSING=0
for var in SLACK_APPROVAL_BOT_TOKEN SLACK_APPROVAL_CHANNEL_ID TF_VAR_slack_signing_secret; do
    if [ -z "${!var:-}" ]; then
        echo "  ERROR: $var 이 설정되지 않았습니다."
        MISSING=1
    else
        echo "  $var: OK"
    fi
done

if [ $MISSING -ne 0 ]; then
    echo ""
    echo "환경변수를 설정하세요. 가이드: docs/slack-app-setup-guide.md"
    exit 1
fi

# ── Step 3: AWS 자격증명 확인 ────────────────────────────────────────────────

echo ""
echo "=== Step 3: AWS 자격증명 확인 ==="

if aws sts get-caller-identity --region "$AWS_REGION" > /dev/null 2>&1; then
    IDENTITY=$(aws sts get-caller-identity --region "$AWS_REGION" --output text --query 'Arn')
    echo "  AWS Identity: $IDENTITY"
else
    echo "  WARNING: AWS 자격증명을 확인할 수 없습니다."
    echo "  aws configure 또는 ~/.aws/credentials를 확인하세요."
    read -p "  계속하시겠습니까? [y/N] " answer
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ── Step 4: Terraform 배포 ───────────────────────────────────────────────────

echo ""
echo "=== Step 4: Terraform 배포 (AWS 인프라) ==="

cd "$TERRAFORM_DIR"
terraform init -input=false
terraform apply -auto-approve

API_GATEWAY_URL=$(terraform output -raw api_gateway_url)
echo ""
echo "  API Gateway URL: $API_GATEWAY_URL"

# ── Step 5: 로컬 서비스 설치 ─────────────────────────────────────────────────

echo ""
echo "=== Step 5: 로컬 서비스 설치 ==="

cd "$PROJECT_ROOT"
bash "$SCRIPT_DIR/install-service.sh"

# ── Step 6: 배포 완료 요약 ───────────────────────────────────────────────────

INTERACTIVITY_URL="${API_GATEWAY_URL}slack/interact"

echo ""
echo "================================================================"
echo "  배포 완료!"
echo "================================================================"
echo ""
echo "  Slack Interactivity Request URL:"
echo "  $INTERACTIVITY_URL"
echo ""
echo "  위 URL을 Slack App > Interactivity & Shortcuts > Request URL에"
echo "  등록하세요."
echo ""
echo "  가이드: docs/slack-app-setup-guide.md (Step 6)"
echo ""
echo "  서비스 상태:  curl http://localhost:8080/health"
echo "  로그 확인:    tail -f ~/Library/Logs/oh-my-cc-agent/stderr.log"
echo "================================================================"
