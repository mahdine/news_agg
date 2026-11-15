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

# پست‌های ثبت‌شده برای بازخورد. هر رکورد کوچک است (موضوعات + رأی‌ها)؛
# ۴۰۰ پست یعنی حدود دو ماه با پست ساعتی.
MAX_POSTS = 400

# چقدر یک ماجرا در حافظه بماند. پوشش چندرسانه‌ای یک خبر بزرگ تا دو روز کش
# می‌آید — رسانه‌های روسی و آسیایی معمولا‌ یک روز دیرتر می‌نویسند — پس پنجره
# باید از خودِ موج پوشش بلندتر باشد وگرنه دنباله‌اش رد می‌شود.
STORY_MEMORY_HOURS = 72

# ارزش خالص هر رأی در بازخورد. 🔥/👏 از واکنش‌های تلگرام می‌آیند و قوی‌تر/نرم‌تر
# از 👍 معمولی‌اند؛ 👎 کامل منفی است، تکراری بیشتر خطای خوشه‌بندی است تا
# بی‌کیفیتی منبع، و قدیمی‌بودن نرم‌ترین سیگنال.
VOTE_WEIGHT = {
    "good": 1.0,
    "excellent": 1.5,
    "useful": 0.75,
    "bad": -1.0,
    "repeat": -0.5,
    "stale": -0.25,
}

# واکنش تلگرام روی پست → نوع بازخورد. واکنش‌های بیرون از این جدول
# (😂، ✅، ایموجی‌های سفارشی و…) رأی محسوب نمی‌شوند.
REACTION_VOTES = {
    "👍": "good",
    "🔥": "excellent",
    "❤️": "excellent",
    "👏": "useful",
    "👎": "bad",
}


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
        # ── بازخورد مخاطب ──
        # pid → {label, pool, topics, title, url, at, votes: {user_id: vote}}
        self.posts: dict[str, dict] = {}
        self.next_post_id: int = 1
        # آخرین update_id تلگرامی که پردازش شده؛ getUpdates از بعدش شروع می‌کند.
        self.last_update_id: int = 0

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
            self.posts = data.get("posts", {})
            self.next_post_id = data.get("next_post_id", 1)
            self.last_update_id = data.get("last_update_id", 0)

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
                "topics": list(getattr(item, "topics", [])),
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
            # رکوردهای جدید label (منبعِ config) را مستقیم دارند؛ دامنه‌ی
            # ناشر برای داستان‌های HN چیزی مثل github.com می‌شود و برای
            # آمار به درد نمی‌خورد. حدس زدن از روی دامنه فقط برای رکوردهای
            # قدیمی بدون label می‌ماند.
            label = entry.get("label")
            if label is None:
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

    # ── بازخورد مخاطب ──────────────────────────────────────────────

    def register_post(self, item, title: str, topics: list[str], now: datetime) -> str:
        """پست تازه‌ی ارسال‌شده را برای جمع‌آوری رأی ثبت می‌کند و pid برمی‌گرداند.

        pid یک عدد کوچک است چون دکمه‌های تلگرام callback_data را به ۶۴ بایت
        محدود می‌کنند؛ mapping کامل همین‌جا نگهداری می‌شود.
        """
        pid = str(self.next_post_id)
        self.next_post_id += 1
        self.posts[pid] = {
            "label": item.label,
            "pool": "viral" if item.ranked else "editorial",
            "topics": list(topics or []),
            "title": (title or "")[:120],
            "url": item.url,
            "at": now.isoformat(),
            "votes": {},
        }
        return pid

    def record_vote(self, pid: str, user_id: int | str, vote: str) -> bool:
        """رأی ثبت می‌کند. هر کاربر روی هر پست یک رأی دارد؛ رأی جدید جای قبلی را می‌گیرد.

        False یعنی pid یا vote نامعتبر (مثلاً پست از state پُریده شده).
        """
        if vote not in VOTE_WEIGHT:
            return False
        post = self.posts.get(str(pid))
        if post is None:
            return False
        post["votes"][str(user_id)] = vote
        return True

    def record_reaction(self, pid: str, user_id: int | str, emojis: list[str]) -> bool:
        """واکنش‌های فعلی کاربر روی پست را ذخیره می‌کند.

        new_reaction در update‌ی message_reaction فهرست کامل واکنش‌های آن
        کاربر است (نه تفاوت)، پس همین‌جا جایگزین کامل می‌شود. False یعنی
        pid نامعتبر یا فهرست با قبلی یکی بود.
        """
        emojis = [e for e in emojis if e]
        post = self.posts.get(str(pid))
        if post is None:
            return False
        if post["votes"].get(str(user_id)) == emojis:
            return False
        post["votes"][str(user_id)] = emojis
        return True

    def note_message_id(self, pid: str, message_id: int | None, chat_id: int | None = None) -> None:
        """message_id تلگرام پست را بعد از ارسال موفق به رکورد وصل می‌کند.

        با همین mapping update‌های message_reaction (که فقط message_id دارند)
        به pid تبدیل می‌شوند.
        """
        post = self.posts.get(str(pid))
        if post is None:
            return
        if message_id:
            post["message_id"] = message_id
        if chat_id:
            post["chat_id"] = chat_id

    def find_pid_by_message(self, message_id: int | None, chat_id: int | None = None) -> str | None:
        """pid پستی که message_id تلگرامش با این است (برای update‌های واکنش).

        اگر chat_id داده شود فقط پست‌های همان چت حساب می‌شوند (پست‌های قدیمی
        بدون chat_id همچنان با message_id تنها هم می‌خورند).
        """
        if not message_id:
            return None
        for pid, post in self.posts.items():
            if post.get("message_id") != message_id:
                continue
            if chat_id is not None and post.get("chat_id") not in (None, chat_id):
                continue
            return pid
        return None

    def note_poll_id(self, pid: str, poll_id: str | None) -> None:
        """poll_id نظّم بازخورد را بعد از ارسال موفق به رکورد پست وصل می‌کند.

        update‌های poll_answer و poll فقط poll_id دارند و با همین mapping به
        pid می‌رسند.
        """
        post = self.posts.get(str(pid))
        if post is not None and poll_id:
            post["poll_id"] = poll_id

    def record_poll_counts(self, pid: str, counts: dict[str, int]) -> bool:
        """شمارش نهایی نظّم (وقتی بسته شد) را به رکورد پست وصل می‌کند.

        نظّم‌های کانال ناشناس‌اند، پس رأی‌به‌رأی نداریم؛ شمارش نهایی خودش
        «هر کاربر یک رأی» تلگرام را دارد. False یعنی pid نامعتبر یا نظّمی
        با رأی نداشت.
        """
        counts = {k: v for k, v in (counts or {}).items() if v}
        if not counts:
            return False
        post = self.posts.get(str(pid))
        if post is None:
            return False
        if post.get("poll_counts") == counts:
            return False
        post["poll_counts"] = counts
        return True

    def find_pid_by_poll(self, poll_id: str | None) -> str | None:
        """pid پستی که نظّمش با این poll_id است."""
        if not poll_id:
            return None
        for pid, post in self.posts.items():
            if post.get("poll_id") == poll_id:
                return pid
        return None

    @staticmethod
    def effective_vote(value) -> str | None:
        """رأی ذخیره‌شده‌ی یک کاربر را به یک نوع بازخورد ختم می‌کند.

        رأی دکمه یک string است؛ رأی واکنش فهرست ایموجی — قوی‌ترین‌شان
        (بیشترین وزن) رأی کاربر شمرده می‌شود.
        """
        if isinstance(value, str):
            return value if value in VOTE_WEIGHT else None
        if isinstance(value, list):
            mapped = [REACTION_VOTES[e] for e in value if e in REACTION_VOTES]
            if mapped:
                return max(mapped, key=lambda v: VOTE_WEIGHT[v])
        return None

    def feedback_counts(self, per: str = "label") -> dict[str, dict[str, int]]:
        """شمارش هر نوع رأی، به تفکیک منبع (`per="label"`) یا موضوع (`per="topic").

        موضوع هر پست چندتا است و رأی‌هایش روی همه‌ی موضوعاتش می‌نشینند.
        """
        from collections import defaultdict

        acc: dict[str, dict[str, int]] = defaultdict(
            lambda: dict.fromkeys(VOTE_WEIGHT, 0)
        )
        for post in self.posts.values():
            votes = list(post.get("votes", {}).values())
            poll_counts = post.get("poll_counts") or {}
            if not votes and not poll_counts:
                continue
            keys = [post["label"]] if per == "label" else post.get("topics", [])
            for key in keys:
                for value in votes:
                    vote = self.effective_vote(value)
                    if vote:
                        acc[key][vote] += 1
                for vote, count in poll_counts.items():
                    if vote in acc[key]:
                        acc[key][vote] += count
        return dict(acc)

    @staticmethod
    def stats_from_counts(counts: dict[str, dict[str, int]]) -> dict[str, dict]:
        """n رأی، امتیاز خالص (−۱ تا ۱)، درصد تأیید، و ضریب پیشنهادی برای هر کلید."""
        out: dict[str, dict] = {}
        for key, c in counts.items():
            n = sum(c.values())
            if not n:
                continue
            net = sum(VOTE_WEIGHT[v] * c.get(v, 0) for v in VOTE_WEIGHT)
            score = net / n
            pos = sum(c.get(v, 0) for v, w in VOTE_WEIGHT.items() if w > 0)
            neg = sum(c.get(v, 0) for v, w in VOTE_WEIGHT.items() if w < 0)
            out[key] = {
                "n": n,
                "score": score,
                "approval": pos / (pos + neg) if (pos + neg) else None,
                **{v: c.get(v, 0) for v in VOTE_WEIGHT},
            }
        return out

    @staticmethod
    def _multiplier(score: float, n: int, strength: float, lo: float, hi: float,
                    min_votes: int = 3) -> float:
        """ضریب نرم با shrinkage به سمت ۱.

        با رأی کم ضریب تقریباً ۱ می‌ماند (n/(n+6)) تا نویز رأی‌های اول
        رتبه‌بندی را نچرخاند؛ با رأی زیاد به سقف strength می‌رسد.
        """
        if n < min_votes:
            return 1.0
        value = 1.0 + strength * score * (n / (n + 6.0))
        return max(lo, min(hi, value))

    def feedback_multipliers(self) -> dict[str, float]:
        """ضریب رتبه‌بندی برای هر منبع از روی بازخورد (باند ۰.۷۵ تا ۱.۲۵)."""
        stats = self.stats_from_counts(self.feedback_counts("label"))
        return {
            label: self._multiplier(s["score"], s["n"], 0.25, 0.75, 1.25)
            for label, s in stats.items()
        }

    def topic_multipliers(self) -> dict[str, float]:
        """ضریب رتبه‌بندی برای هر موضوع (باند باریک‌تر: ۰.۸۵ تا ۱.۱۵).

        موضوع در زمان رتبه‌بندی با کلیدواژه تخمین زده می‌شود (نه برچسب دقیق
        مدل)، پس اثرش باید کوچک بماند.
        """
        stats = self.stats_from_counts(self.feedback_counts("topic"))
        return {
            topic: self._multiplier(s["score"], s["n"], 0.15, 0.85, 1.15)
            for topic, s in stats.items()
        }

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

        # پست‌های قدیمی‌تر از پنجره‌ی بازخورد را می‌پرانیم؛ رأی‌هایشان در
        # آمار ماندن لازم نیست ولی حجم state بی‌دلیل زیاد نمی‌شود.
        if len(self.posts) > MAX_POSTS:
            ordered = sorted(self.posts.items(), key=lambda kv: kv[1].get("at", ""))
            self.posts = dict(ordered[-MAX_POSTS:])

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
                    "posts": self.posts,
                    "next_post_id": self.next_post_id,
                    "last_update_id": self.last_update_id,
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
