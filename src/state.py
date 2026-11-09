"""حافظه‌ی بین اجراها: چه چیزی پست شده، سهم هر استخر، و رشد تعامل آیتم‌ها."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("state")

# فقط این تعداد uid آخر را نگه می‌داریم تا فایل بی‌نهایت رشد نکند. حالا که هر
# پست کل خوشه‌اش را بایگانی می‌کند (نه فقط خودش را)، این عدد چند برابر سریع‌تر
# پر می‌شود، پس سقف بالاتر رفت تا پنجره‌ی seen از پنجره‌ی حافظه‌ی ماجرا کوتاه‌تر
# نشود.
MAX_SEEN = 3000

# عکس‌های تعامل. هر اجرا حدود صد آیتم رتبه‌دار می‌بیند و هر آیتم بعد از خروج از
# پنجره‌ی سنی بی‌فایده می‌شود، پس این سقف چند اجرا تاریخچه می‌دهد.
MAX_SNAPSHOTS = 600

# برای جلوگیری از پشت‌سرهم آمدن یک منبع، فقط همین تعداد پست آخر مهم است.
RECENT_LABELS = 5

# امضای ماجراهای پست‌شده. سقفش سخاوتمند است چون هر ردیف فقط چند ده توکن است.
MAX_STORIES = 300

# چقدر یک ماجرا در حافظه بماند. پوشش چندرسانه‌ای یک خبر بزرگ تا دو روز کش
# می‌آید — رسانه‌های روسی و آسیایی معمولاً یک روز دیرتر می‌نویسند — پس پنجره
# باید از خودِ موج پوشش بلندتر باشد وگرنه دنباله‌اش رد می‌شود.
STORY_MEMORY_HOURS = 72


class State:
    def __init__(self, path: Path):
        self.path = path
        self.is_first_run = not path.exists()
        self.seen: list[str] = []
        self._seen_set: set[str] = set()
        # سهم ۵۰/۵۰ بین دو استخر وقتی هر اجرا فقط یک پست می‌فرستد داخل خودِ اجرا
        # قابل اعمال نیست، پس در طول زمان نگه داشته می‌شود.
        self.posted: dict[str, int] = {"viral": 0, "editorial": 0}
        # uid → [engagement, timestamp]. برای سنجش رشد بین دو اجرا.
        self.snapshots: dict[str, list] = {}
        # برچسب منابع آخرین پست‌ها، از قدیم به جدید.
        self.recent_labels: list[str] = []
        # پست‌های هفته، برای ساختن خلاصه‌ی هفتگی.
        self.history: list[dict] = []
        # تاریخ آخرین خلاصه‌ی هفتگی، تا دو بار در یک روز نرود.
        self.last_digest: str = ""
        # امضای ماجراهایی که پست شده‌اند، جدا از uid مقاله‌ها.
        self.stories: list[dict] = []
        # label منبع → چند خبرش توسط مدل SKIP شده.
        self.skips: dict[str, int] = {}
        # label منبع → کل تعداد پست‌ها و زمان آخرین پست. بلندمدت، جدا از
        # history که بعد از هر digest به ۷ روز می‌رسد.
        self.posts_by_label: dict[str, int] = {}
        self.last_post: dict[str, str] = {}

        if not self.is_first_run:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.seen = data.get("seen", [])
            self._seen_set = set(self.seen)
            self.posted.update(data.get("posted", {}))
            self.snapshots = data.get("snapshots", {})
            self.recent_labels = data.get("recent_labels", [])
            self.history = data.get("history", [])
            self.last_digest = data.get("last_digest", "")
            self.stories = data.get("stories", [])
            self.skips = data.get("skips", {})
            self.posts_by_label = data.get("posts_by_label", {})
            self.last_post = data.get("last_post", {})

    def has(self, uid: str) -> bool:
        return uid in self._seen_set

    def mark(self, uid: str) -> None:
        if uid in self._seen_set:
            return
        self._seen_set.add(uid)
        self.seen.append(uid)

    def count_post(self, pool: str, label: str) -> None:
        self.posted[pool] = self.posted.get(pool, 0) + 1
        self.recent_labels.append(label)
        self.recent_labels = self.recent_labels[-RECENT_LABELS:]
        # شمارنده‌ی بلندمدت per-label. `history` بعد از هر digest به ۷ روز
        # محدود می‌شود، ولی scorecard به کل تعداد پست‌های هر منبع نیاز دارد.
        self.posts_by_label[label] = self.posts_by_label.get(label, 0) + 1
        self.last_post[label] = now_utc().isoformat()
        if len(self.posts_by_label) > 300:
            for k in list(self.posts_by_label):
                if k not in self.recent_labels and self.posts_by_label[k] < 3:
                    self.posts_by_label.pop(k, None)
                    self.last_post.pop(k, None)
            if len(self.posts_by_label) > 300:
                self.posts_by_label = dict(sorted(
                    self.posts_by_label.items(), key=lambda kv: -kv[1])[:200])

    def remember_story(self, signature: set[str], now: datetime) -> None:
        """امضای ماجرایی که پست شد.

        `seen` فقط URL مقاله را می‌گیرد، و این برای خبری که ده رسانه پوشش
        می‌دهند کافی نیست: نسخه‌ی رسانه‌ی بعدی URL دیگری دارد و از فیلتر رد
        می‌شود. امضای ماجرا همان چیزی است که همه‌ی آن نسخه‌ها در آن مشترک‌اند.
        """
        if not signature:
            return
        self.stories.append({"sig": sorted(signature), "at": now.isoformat()})

    def recent_stories(self, now: datetime, hours: int = STORY_MEMORY_HOURS) -> list[set[str]]:
        """ماجراهایی که در پنجره‌ی حافظه پست شده‌اند."""
        cutoff = now - timedelta(hours=hours)
        live = []
        for row in self.stories:
            at = _parse(row.get("at"))
            if at and at >= cutoff:
                live.append(set(row.get("sig", [])))
        return live

    def remember(self, item, persian: str, now: datetime) -> None:
        """پست را برای خلاصه‌ی هفتگی و scorecard نگه می‌دارد.

        `source` دامنه‌ی ناشر است (برای سرتیتر) و `label` منبعِ config —
        برای scorecard دوم لازم است: چند فید می‌توانند یک دامنه را پست کنند،
        ولی آستانه و authority مال label است.
        """
        self.history.append(
            {
                "title": persian.split(". ")[0].strip().rstrip("."),
                "url": item.url,
                "source": item.publisher or item.label,
                "label": item.label,
                "pool": "viral" if item.ranked else "editorial",
                "score": round(item.score, 5),
                "at": now.isoformat(),
            }
        )

    def backfill(self, config: dict) -> None:
        """اولین اجرای بعد از اضافه‌شدن فیلدهای scorecard.

        `posts_by_label` خالی است ولی history پر. mapping domain→label از
        config می‌سازیم و آمار قدیمی را از روی `source` (دامنه‌ی ناشر) بازسازی
        می‌کنیم. تقریبی است — پستی که از یک فید برای دامنه‌ی دیگری بوده روی
        label همان فید می‌نشیند — ولی برای scorecard کافی است. یک‌باره اجرا
        می‌شود؛ بعد از آن فیلدها همیشه پرند.
        """
        if self.posts_by_label or not self.history:
            return
        domain_to_label: dict[str, str] = {}
        for src in config.get("sources", []):
            label = src.get("label") or src.get("handle") or src.get("url")
            raw = src.get("url") or src.get("handle") or ""
            host = raw.split("//")[-1].split("/")[0].lower().removeprefix("www.")
            if host:
                domain_to_label[host] = label

        for entry in self.history:
            source = (entry.get("source") or "").lower()
            label = domain_to_label.get(source)
            if label is None:
                # شاید خودِ source یک label config باشد (مثلا‌ اکانت HN یا
                # فیدی که publisher ندارد). مقایسه بدون حساسیت به حروف بزرگ.
                label = next(
                    (
                        (s.get("label") or s.get("handle") or s.get("url"))
                        for s in config.get("sources", [])
                        if ((s.get("label") or s.get("handle") or s.get("url")) or "").lower() == source
                    ),
                    None,
                )
            if label is None:
                continue
            self.posts_by_label[label] = self.posts_by_label.get(label, 0) + 1
            at = entry.get("at")
            if at and (label not in self.last_post or at > self.last_post[label]):
                self.last_post[label] = at

        log.info(
            "scorecard backfill: %d منبع با %d پست قدیمی",
            len(self.posts_by_label), len(self.history),
        )

    def count_skip(self, label: str) -> None:
        """مدل خلاصه‌سازی خبر را SKIP کرده. برای scorecard: منبعی که همیشه
        SKIP می‌خورد نویز است، حتی اگر پست بدهد.»"""
        self.skips[label] = self.skips.get(label, 0) + 1
        # سقف: scorecard فقط منابع فعال را می‌بیند و config محدود است.
        if len(self.skips) > 200:
            self.skips = dict(sorted(self.skips.items(), key=lambda kv: -kv[1])[:100])

    def week(self, now: datetime, days: int = 7) -> list[dict]:
        """پست‌های این هفته، و هرچه قدیمی‌تر بود را دور می‌ریزد."""
        cutoff = now - timedelta(days=days)
        self.history = [
            row
            for row in self.history
            if _parse(row.get("at")) and _parse(row["at"]) >= cutoff
        ]
        return self.history

    def previous(self, uid: str) -> tuple[int, datetime] | None:
        """تعامل و زمانِ آخرین باری که این آیتم را دیدیم."""
        row = self.snapshots.get(uid)
        if not row:
            return None
        try:
            return int(row[0]), datetime.fromisoformat(row[1])
        except (ValueError, IndexError, TypeError):
            return None

    def record(self, uid: str, engagement: int, now: datetime) -> None:
        self.snapshots[uid] = [engagement, now.isoformat()]

    def save(self) -> None:
        self.seen = self.seen[-MAX_SEEN:]
        self._seen_set = set(self.seen)
        self.stories = self.stories[-MAX_STORIES:]

        # قدیمی‌ترین عکس‌ها اول حذف می‌شوند؛ آن‌ها همان‌هایی هستند که از پنجره‌ی
        # سنی خارج شده‌اند و دیگر امتیاز نمی‌گیرند.
        if len(self.snapshots) > MAX_SNAPSHOTS:
            ordered = sorted(self.snapshots.items(), key=lambda kv: kv[1][1])
            self.snapshots = dict(ordered[-MAX_SNAPSHOTS:])

        self.path.write_text(
            json.dumps(
                {
                    "seen": self.seen,
                    "posted": self.posted,
                    "recent_labels": self.recent_labels,
                    "last_digest": self.last_digest,
                    "history": self.history,
                    "stories": self.stories,
                    "snapshots": self.snapshots,
                    "skips": self.skips,
                    "posts_by_label": self.posts_by_label,
                    "last_post": self.last_post,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
