#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Claude Code Slack Approval - 제거 스크립트
#
# 사용법: ./code/script/teardown.sh
#
# 로컬 서비스 제거 + AWS 인프라 삭제 (Terraform destroy)
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TERRAFORM_DIR="$PROJECT_ROOT/code/terraform"

# ── Step 1: 로컬 서비스 제거 ─────────────────────────────────────────────────

echo "=== Step 1: 로컬 서비스 제거 ==="
bash "$SCRIPT_DIR/uninstall-service.sh"

# ── Step 2: AWS 인프라 제거 (Terraform destroy) ──────────────────────────────

echo ""
echo "=== Step 2: AWS 인프라 제거 ==="
echo "  DynamoDB, Lambda, API Gateway 리소스를 삭제합니다."
echo ""
read -p "  계속하시겠습니까? [y/N] " answer

if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    echo "  AWS 인프라 제거를 건너뜁니다."
    echo "  수동 제거: cd code/terraform && terraform destroy"
    exit 0
fi

cd "$TERRAFORM_DIR"

if [ ! -f "terraform.tfstate" ]; then
    echo "  WARNING: terraform.tfstate 파일이 없습니다. 이미 제거되었거나 다른 위치에서 관리 중입니다."
    exit 0
fi

terraform destroy -auto-approve

# ── Step 3: 제거 완료 ────────────────────────────────────────────────────────

echo ""
echo "================================================================"
echo "  제거 완료!"
echo "================================================================"
echo ""
echo "  Slack App은 수동으로 삭제하세요:"
echo "  https://api.slack.com/apps"
echo ""
echo "  환경변수 정리가 필요하면 ~/.zshrc에서 관련 항목을 제거하세요."
echo "================================================================"
