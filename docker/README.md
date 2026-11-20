# worker زنده (ترجمهٔ فوری + جمع‌آوری مداوم بازخورد)

پروژهٔ جدا از `tg-media-bot` — فقط روی همان WSL می‌چرخد؛ image،
container، `.env` و داده‌ها مشترک نیستند.

## معماری

- **مصرف‌کنندهٔ انحصاری updateهای تلگرام**: `getUpdates` به‌صورت long-poll.
  اجرای ساعتی Actions دیگر `getUpdates` نمی‌زند، پس update بین دو
  مصرف‌کننده شکسته نمی‌شود.
- **ترجمهٔ فوری**: کاربر پست کانال را برای بات forward می‌کند (یا
  `/t <message_id|url>`) → worker مقاله را می‌گیرد (trafilatura)، کامل
  ترجمه می‌کند (Gemini، همان زنجیرهٔ مدل‌ها)، صفحهٔ HTML در `iv/` push
  می‌کند و workflow `iv-deploy` آن را روی GitHub Pages می‌نهد. لینک قطعی
  است (`https://mahdine.github.io/news_agg/iv/<pid>.html`)؛ worker منتظر
  200 شدن صفحه می‌ماند و بعد در چت خصوصی می‌فرستد. صفحه به‌صورت
  Instant View داخل تلگرام باز می‌شود.
- **بازخورد مداوم**: واکنش/رأی/نظّم در stateٔ حافظه‌ای ثبت و به‌صورت
  snapshot در `live/feedback.json` push می‌شود (هر گاه تغییر کرد، حداکثر
  هر دقیقه). اجرای ساعتی همان snapshot را روی `state.json` overlay
  می‌کند (`State.apply_live_feedback`) — پیش از رتبه‌بندی و scorecard.
- worker هرگز `state.json` را لمس نمی‌کند؛ فقط `iv/*.html` و
  `live/feedback.json` push می‌شوند (فایل‌های جدا، پس با commit ساعتی
  state تعارض نمی‌گیرند).
- offset در `/data/offset`، کش ترجمه در `/data/translated.json` —
  هر دو با restart container می‌مانند. اگر هاست خاموش باشد، تلگرام
  updateها را تا ۲۴ ساعت نگه می‌دارد و worker با روشن‌شدن جبران می‌کند.

## نصب (WSL)

```bash
# بار اول
git clone https://github.com/mahdine/news_agg.git /home/mahdi/news-live
cd /home/mahdi/news-live
# .env با TELEGRAM_BOT_TOKEN، TELEGRAM_CHANNEL، GEMINI_API_KEY، GITHUB_TOKEN
bash docker/bootstrap.sh
docker compose -f docker/docker-compose.yml up -d --build live

# بعدی‌ها
cd /home/mahdi/news-live && git pull
docker compose -f docker/docker-compose.yml up -d --build live
```

`GITHUB_TOKEN`: یک fine-grained PAT روی مخزن `news_agg` با دسترسی
`Contents: read and write`. فقط از worker استفاده می‌شود.

## تست زنده

```bash
docker logs -f newsagg-live
# بعد: یک پست کانال را برای بات forward کن؛ ترجمه باید در چت خصوصی بیاید
```
