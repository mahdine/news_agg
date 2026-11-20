"""worker زنده: ترجمه‌ی فوری + جمع‌آوری مداوم بازخورد.

معماری cron (اجرای ساعتی Actions) نمی‌تواند «همان لحظه» جواب بدهد. این
worker یک فرآیند همیشگی است (Docker در WSL، پروژه‌ی جدا از tg-media-bot)
و دو کار دارد:

1. **ترجمه‌ی فوری**: کاربر پست کانال را برای بات forward می‌کند (یا
   /t <message_id|url> می‌زند) → worker مقاله را می‌گیرد، کامل ترجمه
   می‌کند، صفحه‌ی Instant View در iv/ منتشر می‌کند و لینکش را در چت
   خصوصی همان کاربر می‌فرستد.

2. **بازخورد مداوم**: این worker مصرف‌کننده‌ی انحصاری updateهای تلگرام
   است. واکنش/رأی/نظّم در stateٔ حافظه‌ای ثبت و هر گاه تغییر کرد، به‌صورت
   snapshot در live/feedback.json push می‌شود. اجرای ساعتی همان snapshot را
   پیش از رتبه‌بندی و scorecard روی state.json overlay می‌کند
   (State.apply_live_feedback). چون اجرای Actions دیگر getUpdates نمی‌زند،
   update بین دو مصرف‌کننده شکسته نمی‌شود.

offset در فایل محلی می‌ماند (با restart حفظ می‌شود) و repo یک clone محلی
است. worker فقط iv/*.html و live/feedback.json push می‌کند و هرگز
state.json را لمس نمی‌کند — با commit ساعتی state تعارض ندارد.
اگر هاست خاموش باشد، تلگرام updateها را تا ۲۴ ساعت نگه می‌دارد و worker
با روشن‌شدن جبران می‌کند.
"""

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from . import article, ivpage, telegram, translate
from .state import State

log = logging.getLogger("live")

REPO_DIR = Path(os.environ.get("LIVE_REPO_DIR", "/repo"))
DATA_DIR = Path(os.environ.get("LIVE_DATA_DIR", "/data"))
PAGES_BASE = os.environ.get("PAGES_BASE_URL", "https://mahdine.github.io/news_agg")

# long-poll: تلگرام اتصال را نگه می‌دارد و به‌محض رسیدن update پاسخ می‌دهد.
POLL_TIMEOUT = 25
# چقدر زود state.json را تازه کنیم (message_id پست‌های تازه را ببینیم).
REFRESH_STATE_SECONDS = 60
# حداقل فاصله بین دو push snapshot بازخورد (وقتی تغییر وجود داشته باشد).
FLUSH_SECONDS = 60
# چقدر منتظر 200 شدن صفحه روی Pages بمانیم.
PAGE_WAIT_SECONDS = 240
PAGE_WAIT_INTERVAL = 15

STALE_MSG = "این پست قدیمی است و دیگر در حافظه نیست — نمی‌توانم ترجمه‌اش کنم."
NOT_OUR_CHANNEL = "این پست از کانال ما نیست. اگر لینک مقاله‌اش را داری، بفرست: /t <لینک>"
NO_URL_MSG = "این پست لینک مقاله ندارد."
BAD_T_MSG = "بعد از /t یا شماره‌ی پست یا لینک کامل مقاله بفرست."
FETCH_FAIL_MSG = "متن کامل را نمی‌شود گرفت (paywall، چالش جاوااسکریپت، یا صفحه خراب است).\nمقاله‌ی اصلی: {url}"
TRANSLATE_FAIL_MSG = "ترجمه فعلاً نشد (مدل‌ها مشغول‌اند یا سهمیه تمام شده).\nمقاله‌ی اصلی: {url}"
ERROR_MSG = "یه چیزی دراز و کوتاه شد. بعداً دوباره امتحان کن."
DONE_MSG = "📄 ترجمه‌ی کامل:\n{url}"

GREETING = (
    "سلام! پستی که می‌خواهی متن کاملش را بخوانی را برایم forward کن، "
    "یا /t <لینک> بفرست — ترجمه‌ی کاملش همین‌جا می‌آید."
)


class LiveWorker:
    def __init__(self, token: str, gemini_key: str, models: list[str], channel: str, github_token: str):
        self.token = token
        self.gemini_key = gemini_key
        self.models = models
        self.channel = channel
        self.github_token = github_token
        self.client = httpx.Client(timeout=30, follow_redirects=True)
        self.channel_id: int | None = None
        self.state: State | None = None
        self.offset = self._load_offset()
        self.last_refresh = 0.0
        self.last_flush = 0.0
        self.feedback_dirty = False
        self._cache = self._load_cache()

    # ── ذخیره‌ی محلی (volume) ────────────────────────────────────────
    def _load_offset(self) -> int:
        p = DATA_DIR / "offset"
        try:
            return int(p.read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _save_offset(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "offset").write_text(str(self.offset))

    def _load_cache(self) -> dict:
        try:
            return json.loads((DATA_DIR / "translated.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "translated.json").write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ── git ─────────────────────────────────────────────────────────
    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(
            ["git", "-C", str(REPO_DIR), *args],
            capture_output=True, text=True, timeout=120,
        )
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:4])} شکست: {(r.stderr or r.stdout)[:200]}")
        return r

    def _git_push(self, *args: str) -> subprocess.CompletedProcess:
        return self._git("-c", f"http.extraHeader=Authorization: Bearer {self.github_token}", *args)

    def _publish_file(self, rel_path: str, content: str, message: str) -> None:
        """فایل را روی main منتشر می‌کند: fetch → reset → بازنویسی → commit → push.

        reset --hard قبل از هر push باعث می‌شود روی آخرین main بنشینیم و
        اگر push از روی سر بازگشت (تصادف با commit ساعتی state)، دوباره از
        آخر امتحان می‌کنیم. فایل‌هایی که push می‌کنیم جدا از state.json
        هستند، پس هیچ چیزی از کار نمی‌افتد.
        """
        for attempt in range(1, 4):
            try:
                self._git("fetch", "origin", "main")
                self._git("reset", "--hard", "origin/main")
                target = REPO_DIR / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                self._git("add", rel_path)
                if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
                    return  # بدون تغییر (مثلاً درخواست تکراری) — push لازم نیست
                self._git(
                    "-c", "user.name=newsagg-live",
                    "-c", "user.email=newsagg-live@users.noreply.github.com",
                    "commit", "-m", message,
                )
                self._git_push("push", "origin", "HEAD:main")
                return
            except Exception as exc:
                log.warning("push %s نشد (تلاش %d): %s", rel_path, attempt, str(exc)[:150])
                time.sleep(5)
        raise RuntimeError(f"push {rel_path} بعد از سه تلاش شکست خورد")

    # ── state ───────────────────────────────────────────────────────
    def refresh_state(self, force: bool = False) -> State | None:
        """state.json تازه از origin/main می‌آورد؛ بازخورد ثبت‌شده را حفظ می‌کند.

        فقط پست‌های *تازه* (pidی که هنوز ندیده‌ایم) اضافه می‌شوند. برای
        pidهای شناخته‌شدهٔ خودِ ما، رکورد ما منبعِ حقیقت است: ما تنها
        کسی‌ایم که رأی/واکنش می‌نویسیم، پس نسخه‌ی حافظه‌ای‌مان تازه‌تر از
        چیزی است که در repo خوابیده.
        """
        now = time.time()
        if self.state is not None and not force and now - self.last_refresh < REFRESH_STATE_SECONDS:
            return self.state
        try:
            self._git("fetch", "origin", "main")
            self._git("reset", "--hard", "origin/main")
            fresh = State(REPO_DIR / "state.json")
        except Exception as exc:
            log.warning("همگام‌سازی repo نشد: %s", str(exc)[:150])
            self.last_refresh = now
            return self.state
        if self.state is None:
            self.state = fresh
        else:
            for pid, post in fresh.posts.items():
                if pid not in self.state.posts:
                    self.state.posts[pid] = post
        self.last_refresh = now
        return self.state

    # ── تلگرام ─────────────────────────────────────────────────────
    def _send(self, chat_id, text: str, client: httpx.Client | None = None) -> None:
        if not chat_id:
            return
        try:
            telegram._call(
                "sendMessage", {"chat_id": chat_id, "text": text},
                self.token, client or self.client,
            )
        except telegram.TelegramError as exc:
            log.warning("پیام نرفت: %s", exc)

    def poll_updates(self) -> list:
        result = telegram._call(
            "getUpdates",
            {"offset": self.offset, "limit": 100, "timeout": POLL_TIMEOUT},
            self.token, self.client,
        )
        return result if isinstance(result, list) else []

    def handle_update(self, update: dict) -> None:
        self.offset = max(self.offset, update.get("update_id", 0))
        self._save_offset()
        if m := update.get("message"):
            self._handle_message(m)
        elif ur := update.get("message_reaction"):
            self._handle_reaction(ur)
        elif rc := update.get("message_reaction_count"):
            self._handle_reaction_count(rc)
        elif pa := update.get("poll_answer"):
            self._handle_poll_answer(pa)
        elif p := update.get("poll"):
            self._handle_poll(p)
        elif cb := update.get("callback_query"):
            self._handle_callback(cb)

    def _handle_message(self, msg: dict) -> None:
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()

        if text.startswith("/start"):
            self._handle_start(text[len("/start"):].strip(), (msg.get("from") or {}).get("id"), chat_id)
            return

        if text.startswith("/t") and len(text) > 2:
            arg = text[2:].strip()
            if arg:
                self._spawn_job(chat_id, arg)
            return

        fwd_id = msg.get("forward_from_message_id")
        if fwd_id:
            fchat = msg.get("forward_from_chat") or {}
            if self.channel_id and fchat.get("id") and fchat.get("id") != self.channel_id:
                self._send(chat_id, NOT_OUR_CHANNEL)
            else:
                self._spawn_job(chat_id, str(fwd_id))
            return
        # متن عادی که نه دستور است نه forward: بی‌صدا رد می‌شود (همان
        # رفتار اجرای ساعتی — فقط /start جواب می‌گرفت).

    def _handle_start(self, payload: str, user_id, chat_id) -> None:
        parsed = telegram.parse_vote(payload)
        if not parsed:
            if chat_id:
                self._send(chat_id, GREETING)
            return
        pid, vote = parsed
        state = self.refresh_state(force=True)
        if state and state.record_vote(pid, user_id or 0, vote):
            self.feedback_dirty = True
            if chat_id:
                self._send(chat_id, telegram.CONFIRM[vote])
        elif chat_id:
            self._send(chat_id, telegram.STALE)

    def _handle_reaction(self, ur: dict) -> None:
        user = ur.get("user") or {}
        if not user:
            return  # کانال واکنش ناشناس دارد؛ از message_reaction_count می‌آید
        chat = ur.get("chat") or {}
        new = ur.get("new_reaction") or []
        emojis = [r.get("emoji", "") for r in new if r.get("type") == "emoji"]
        state = self.refresh_state()
        if not state:
            return
        pid = state.find_pid_by_message(ur.get("message_id"), chat.get("id"))
        if pid and state.record_reaction(pid, user.get("id", 0), emojis):
            self.feedback_dirty = True

    def _handle_reaction_count(self, rc: dict) -> None:
        chat = rc.get("chat") or {}
        counts = {}
        for rt in rc.get("reactions") or []:
            r = rt.get("type") or {}
            if r.get("type") == "emoji" and r.get("emoji"):
                counts[r["emoji"]] = rt.get("total_count", 0)
        state = self.refresh_state()
        if not state:
            return
        pid = state.find_pid_by_message(rc.get("message_id"), chat.get("id"))
        if pid and state.record_reaction_counts(pid, counts):
            self.feedback_dirty = True

    def _handle_poll_answer(self, pa: dict) -> None:
        user = pa.get("user") or {}
        option_ids = pa.get("option_ids") or []
        if not user or not option_ids:
            return
        idx = option_ids[0]
        vote = telegram.POLL_OPTIONS[idx][1] if 0 <= idx < len(telegram.POLL_OPTIONS) else None
        if not vote:
            return
        state = self.refresh_state()
        if not state:
            return
        pid = state.find_pid_by_poll(pa.get("poll_id"))
        if pid and state.record_vote(pid, user.get("id", 0), vote):
            self.feedback_dirty = True

    def _handle_poll(self, p: dict) -> None:
        if not p.get("is_closed"):
            return
        state = self.refresh_state()
        if not state:
            return
        pid = state.find_pid_by_poll(p.get("id"))
        if not pid:
            return
        counts = {}
        for i, opt in enumerate(p.get("options") or []):
            vote = telegram.POLL_OPTIONS[i][1] if i < len(telegram.POLL_OPTIONS) else None
            if vote:
                counts[vote] = opt.get("voter_count", 0)
        if state.record_poll_counts(pid, counts):
            self.feedback_dirty = True

    def _handle_callback(self, cb: dict) -> None:
        parsed = telegram.parse_vote(cb.get("data", "") or "")
        if not parsed:
            return
        pid, vote = parsed
        state = self.refresh_state()
        if state and state.record_vote(pid, (cb.get("from") or {}).get("id", 0), vote):
            self.feedback_dirty = True

    # ── ترجمه‌ی فوری ─────────────────────────────────────────────────
    def _spawn_job(self, chat_id, key: str) -> None:
        # job در thread جدا با httpx client خودش: هم صف getUpdates بسته
        # نمی‌ماند، هم client بین threadها شریک نمی‌شود (httpx thread-safe نیست).
        threading.Thread(target=self._job_safe, args=(chat_id, key), daemon=True).start()

    def _job_safe(self, chat_id, key: str) -> None:
        client = httpx.Client(timeout=30, follow_redirects=True)
        try:
            self.translate_request(chat_id, key, client)
        except Exception:
            log.exception("job ترجمه شکست خورد")
            self._send(chat_id, ERROR_MSG, client)
        finally:
            client.close()

    def translate_request(self, chat_id, key: str, client: httpx.Client | None = None) -> None:
        client = client or self.client
        state = self.refresh_state(force=True)

        url = ""
        title = ""
        label = ""
        date_str = ""

        if key.startswith("http"):
            url = key
        else:
            if not key.isdigit():
                self._send(chat_id, BAD_T_MSG, client)
                return
            if not state:
                self._send(chat_id, ERROR_MSG, client)
                return
            pid = state.find_pid_by_message(int(key))
            if not pid:
                self._send(chat_id, STALE_MSG, client)
                return
            post = state.posts[pid]
            url = post.get("url") or ""
            title = post.get("title") or ""
            label = post.get("label") or ""
            date_str = (post.get("at") or "")[:10]
            key = pid  # کلید صفحه = pid
        if not url:
            self._send(chat_id, NO_URL_MSG, client)
            return

        # اگر قبلا‌ ترجمه شده، فقط لینکِ موجود می‌رود — سهمیه نمی‌سوزد.
        cached = self._cache.get(key)
        if cached and cached.get("url") == url:
            self._send(chat_id, DONE_MSG.format(url=ivpage.page_url(PAGES_BASE, key)), client)
            return

        text = article.fetch(url, client)
        if not text:
            self._send(chat_id, FETCH_FAIL_MSG.format(url=url), client)
            return

        try:
            translated = translate.translate_full(text, self.gemini_key, self.models, client)
        except translate.TranslationError as exc:
            log.warning("ترجمه نشد: %s", exc)
            self._send(chat_id, TRANSLATE_FAIL_MSG.format(url=url), client)
            return
        if not translated:
            self._send(chat_id, TRANSLATE_FAIL_MSG.format(url=url), client)
            return

        rel = f"iv/{ivpage.page_filename(key)}"
        page = ivpage.build_page(title, label, date_str, url, translated)
        self._publish_file(rel, page, f"iv: ترجمه‌ی کامل {key}")

        self._cache[key] = {
            "url": url,
            "at": datetime.now(timezone.utc).isoformat(),
            "file": rel,
        }
        self._save_cache()

        page_url = ivpage.page_url(PAGES_BASE, key)
        # لینک قطعی است ولی deploy چند دقیقه طول می‌کشد؛ منتظر 200 می‌مانیم.
        if self._wait_ready(page_url, client):
            self._send(chat_id, DONE_MSG.format(url=page_url), client)
        else:
            self._send(chat_id, f"صفحه در حال انتشار است؛ اگر باز نشد یک دقیقه صبر کن:\n{page_url}", client)

    def _wait_ready(self, page_url: str, client: httpx.Client) -> bool:
        deadline = time.time() + PAGE_WAIT_SECONDS
        while time.time() < deadline:
            try:
                if client.head(page_url, timeout=15, follow_redirects=True).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(PAGE_WAIT_INTERVAL)
        return False

    # ── snapshot بازخورد ───────────────────────────────────────────
    def flush_feedback(self) -> None:
        if not self.feedback_dirty or self.state is None:
            return
        if time.time() - self.last_flush < FLUSH_SECONDS:
            return
        posts = {}
        for pid, post in self.state.posts.items():
            fb = {}
            if post.get("votes"):
                fb["votes"] = post["votes"]
            if post.get("reaction_counts"):
                fb["reaction_counts"] = post["reaction_counts"]
            if post.get("poll_counts"):
                fb["poll_counts"] = post["poll_counts"]
            if fb:
                posts[pid] = fb
        snapshot = {
            "at": datetime.now(timezone.utc).isoformat(),
            "last_update_id": self.offset,
            "posts": posts,
        }
        try:
            self._publish_file(
                "live/feedback.json",
                json.dumps(snapshot, ensure_ascii=False, indent=1),
                "live: snapshot بازخورد",
            )
            self.feedback_dirty = False
            self.last_flush = time.time()
        except Exception as exc:
            log.warning("push snapshot بازخورد نشد: %s", str(exc)[:150])

    # ── حلقه‌ی اصلی ─────────────────────────────────────────────────
    def run(self) -> None:
        log.info(
            "worker زنده شروع شد (کانال %s، %d مدل، offset=%d)",
            self.channel, len(self.models), self.offset,
        )
        self.refresh_state(force=True)
        if self.offset == 0 and self.state:
            # اولین اجرا: از جایی که اجرای ساعتی رسیده ادامه بده.
            self.offset = self.state.last_update_id
        self._save_offset()

        while True:
            try:
                updates = self.poll_updates()
                for update in updates:
                    self.handle_update(update)
                self.refresh_state()
                self.flush_feedback()
                if not updates:
                    time.sleep(1)
            except Exception:
                log.exception("خطا در حلقه‌ی worker")
                time.sleep(5)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from .main import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel = os.environ.get("TELEGRAM_CHANNEL", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    missing = [
        name
        for name, value in [
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHANNEL", channel),
            ("GEMINI_API_KEY", gemini_key),
            ("GITHUB_TOKEN", github_token),
        ]
        if not value
    ]
    if missing:
        log.error("متغیرهای محیطی تنظیم نشده: %s", ", ".join(missing))
        return 1

    root = Path(__file__).resolve().parent.parent
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    models = (config.get("settings") or {}).get("gemini_models") or ["gemini-2.5-flash"]

    worker = LiveWorker(token, gemini_key, models, channel, github_token)
    try:
        worker.channel_id = telegram.get_chat_id(channel, token, worker.client)
    except Exception as exc:
        log.warning("id کانال گرفته نشد (چک forward بر مبنای number غیرفعال می‌ماند): %s", exc)
    # getUpdates فقط وقتی جواب می‌دهد که webhook تنظیم نباشد.
    telegram.ensure_polling(token, worker.client)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
