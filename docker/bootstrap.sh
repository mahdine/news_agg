#!/usr/bin/env bash
# راه‌اندازی بار اول (داخل WSL، از ریشه‌ی checkout):
#   1) .env را می‌خواهد: TELEGRAM_BOT_TOKEN، TELEGRAM_CHANNEL، GEMINI_API_KEY
#      و GITHUB_TOKEN (یک fine-grained PAT روی news_agg با دسترسی
#      Contents: read+write — فقط برای push کردن صفحات ایستا و snapshot بازخورد).
#   2) clone می‌کند و git user را برای commit‌های worker تنظیم می‌کند.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "اول .env بساز (TELEGRAM_BOT_TOKEN، TELEGRAM_CHANNEL، GEMINI_API_KEY، GITHUB_TOKEN)"
  exit 1
fi
set -a; source .env; set +a
: "${GITHUB_TOKEN:?GITHUB_TOKEN در .env نیست}"

if [ ! -d repo/.git ]; then
  git clone --depth 50 https://github.com/mahdine/news_agg.git repo
fi
git -C repo fetch --depth 50 origin main
git -C repo checkout -B main origin/main
git -C repo config user.name "newsagg-live"
git -C repo config user.email "newsagg-live@users.noreply.github.com"
mkdir -p data
echo "OK — حالا: docker compose -f docker/docker-compose.yml up -d --build live"
