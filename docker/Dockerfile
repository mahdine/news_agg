# worker زندهٔ news_agg — کاملاً جدا از tg-media-bot.
#
# فقط خروجی (long-poll getUpdates + Gemini + git push)؛ پورتی باز نمی‌کند،
# webhook ندارد. build از ریشه‌ی checkout:
#   docker compose -f docker/docker-compose.yml up -d --build live

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# git در container با root اجرا می‌شود ولی volume مال کاربر WSL است.
RUN git config --system --add safe.directory /repo

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کد (src/ و config.yaml) در تصویر bake نمی‌شود؛ همیشه از volume /repo
# (تازه‌یترین commit) import می‌شود تا update لازم نباشد.
WORKDIR /repo

# راپور clone (برای push کردن iv/*.html و live/feedback.json) و داده‌های
# محلی (offset، کش ترجمه) — هر دو از volume می‌آیند.
ENV LIVE_REPO_DIR=/repo \
    LIVE_DATA_DIR=/data \
    PAGES_BASE_URL=https://mahdine.github.io/news_agg

CMD ["python", "-m", "src.live"]
