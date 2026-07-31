#!/usr/bin/env bash
#
# Loyihani GitHub'ga to'g'ri strukturada joylash.
#
# Foydalanish:
#   bash setup_git.sh https://github.com/FOYDALANUVCHI/REPO.git
#
set -euo pipefail

REPO_URL="${1:-}"

if [[ -z "$REPO_URL" ]]; then
  echo "Xato: repo havolasini bering."
  echo "Masalan: bash setup_git.sh https://github.com/user/otc.git"
  exit 1
fi

# --- Xavfsizlik tekshiruvi -------------------------------------------
# .env commit'ga tushishi eng qimmat xato. Oldindan to'xtatamiz.
if [[ ! -f .gitignore ]]; then
  echo "XATO: .gitignore topilmadi. Push to'xtatildi."
  exit 1
fi

if ! grep -qx "\.env" .gitignore; then
  echo "XATO: .gitignore ichida '.env' qatori yo'q. Push to'xtatildi."
  exit 1
fi

git init -q 2>/dev/null || true

# .gitignore ni birinchi bo'lib qat'iylashtiramiz
git add .gitignore
git commit -qm "chore: add gitignore" 2>/dev/null || true

git add .

# Oxirgi tekshiruv: sirlar staging'ga tushmaganini aniqlaymiz
if git diff --cached --name-only | grep -qE '(^|/)\.env$|mnemonic|secret'; then
  echo ""
  echo "  TO'XTA: quyidagi fayllar commit'ga tushmoqda va sir bo'lishi mumkin:"
  git diff --cached --name-only | grep -E '(^|/)\.env$|mnemonic|secret'
  echo ""
  echo "  'git reset' qiling va .gitignore ni tekshiring."
  exit 1
fi

git commit -qm "feat: escrow bot skeleton with admin panel" || true
git branch -M main
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

echo ""
echo "Push qilinmoqda: $REPO_URL"
git push -u origin main --force

echo ""
echo "Tayyor. Endi repoda struktura shunday bo'lishi kerak:"
echo "  bot/config.py, bot/services/..., bot/handlers/..."
echo ""
echo "Tekshiring: .env repoda KO'RINMASLIGI shart."
