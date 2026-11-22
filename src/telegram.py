"""ارسال پست به کانال از طریق Bot API."""

import html
import logging
import os

import httpx
from urllib.parse import quote

from .models import Item

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


# سقف پیام متنی ۴۰۹۶ است. خلاصه‌ی ۲۵۰ کلمه‌ای فارسی حدود ۱۵۰۰ کاراکتر می‌شود،
# پس جا هست. caption عکس فقط ۱۰۲۴ می‌گیرد و همین باعث شد از sendPhoto به
# sendMessage با link preview بزرگ برویم: هم عکس بالای متن می‌آید هم متن کامل جا
# می‌شود.
MAX_TEXT = 3500

# عکس پست از طریق link preview می‌آید. وقتی تصویر مناسبی پیدا نشد preview
# غیرفعال می‌شود — تصویر پیش‌فرض یا لوگوی سایت برای هر خبر یکی است و خواننده را
# گمراه می‌کند، پس پست بی‌عکس تمیزتر است.

# خلاصه هرچقدر هم کامل باشد جای مقاله را نمی‌گیرد؛ این خط خواننده را به دکمه‌ی
# زیر پست وصل می‌کند.
READ_MORE = "برای ادامه‌ی خواندن روی <b>منبع</b> بزنید"

# سرعت امتیازگیری که ماجرا «داغ» حساب شود: امتیاز در ساعت. با آستانه‌ی
# min_points=50 یکسان‌اند: خبری که تازه از آستانه رد شده و در دو ساعت ۵۰ امتیاز
# گرفته دقیقاً ۲۵ در ساعت است — همان لحظه‌ای که داغ می‌شود بج را می‌بیند.
# داستان‌های عادی فرانت‌پیجِ کند حدود ۱۰–۱۵ امتیاز در ساعت می‌گیرند و بج
# نمی‌گیرند؛ خبری که دارد منفجر می‌شود معمولاً بالای ۵۰ است.
HOT_POINTS_PER_HOUR = 25.0


def _fa(value) -> str:
    """عدد فارسی: 156 → ۱۵۶. بج‌ها در متن فارسی پست باید فارسی‌رقم باشند."""
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _badges(item: Item) -> str:
    """خط بج زیر تیتر: سرعت امتیاز (🔥) و پوشش چند منبع مستقل.

    هر دو عدد از داده‌ای می‌آیند که اجرا همین لحظه جمع کرده — بدون درخواست
    اضافه: points از Algolia در همان fetch، و sources از خوشه‌بندی که برای
    امتیاز دادن همین آیتم‌ها ساخته شده بود (breakdown["sources"]).
    """
    parts = []
    if item.points:
        age = item.age_hours()
        if item.points / age >= HOT_POINTS_PER_HOUR:
            when = (
                "کمتر از یک ساعت"
                if age < 1
                else f"{_fa(max(1, round(age)))} ساعت"
            )
            parts.append(f"🔥 {_fa(item.points)} امتیاز در {when}")
    sources = item.breakdown.get("sources") or 0
    if sources >= 2:
        parts.append(f"{_fa(sources)} منبع مستقل")
    return "  ·  ".join(parts)


def build_message(
    item: Item,
    title: str,
    body: str,
    tag: str = "",
    channel: str = "",
    translate_url: str = "",
    translate_hint: bool = False,
) -> str:
    """تیتر، متن، خط منبع، و ردیف هشتگ و امضا.

    `translate_url`: لینک Instant Viewِ ترجمه‌ی کامل (صفحه‌ی پیش‌ساخته). در
    متنِ پست می‌افتد نه دکمه — لینک داخل متن است که تلگرام را مجبور به باز
    شدن IV درون اپ می‌کند؛ همان لینک در دکمه‌ی URL در مرورگر بیرون باز
    می‌شود (مشاهده‌شده 2026-11-22).

    `translate_hint`: وقتی IV در پیام دوم می‌افتد (پست‌های عکس‌دار — کارت
    preview لینک‌های متن را از IV درون‌اپ بیرون می‌فرستد؛ تست کانال
    2026-11-22: E ✅ بدون preview / L ❌ با preview)، یک خط راهنمای ساده
    به‌جای لینک می‌افتد تا خواننده بداند ترجمه در پیام بعدی است.
    """
    source = html.escape(item.publisher or item.label)
    if item.publisher and item.publisher != item.label:
        source += f"  ·  via {html.escape(item.label)}"

    footer = "  ".join(part for part in (tag, _handle(channel)) if part)
    badges = _badges(item)
    translate_line = f'📄 <a href="{translate_url}">ترجمه‌ی کامل</a>' if translate_url else ""
    hint_line = "📄 ترجمه‌ی کامل: پیام بعدی" if (translate_hint and not translate_url) else ""
    fixed = len(source) + len(footer) + len(title) + len(badges) + len(translate_line) + len(hint_line) + 12
    body = body.strip()
    if len(body) > MAX_TEXT - fixed:
        body = body[: MAX_TEXT - fixed].rsplit(" ", 1)[0] + "…"

    parts = []
    if title:
        parts.append(f"<b>{html.escape(title)}</b>")
    if badges:
        # متن داخلی است (فارسی + ایموجی)، نه ورودی کاربر — escape ندارد.
        parts.append(badges)
    parts.append(html.escape(body))
    if translate_line:
        parts.append(translate_line)
    parts.append(f"<i>{source}</i>")
    if footer:
        parts.append(html.escape(footer))
    if hint_line:
        parts.append(html.escape(hint_line))
    return "\n\n".join(parts)


def _handle(channel: str) -> str:
    channel = (channel or "").strip()
    return channel if channel.startswith("@") else ""


# ── بازخورد مخاطب ─────────────────────────────────────────────────
#
# دو مسیر، یک مخزن رأی (هر کاربر روی هر پست یک رأی؛ جدید جای قدیمی می‌گیرد):
#
# ۱. واکنش تلگرام (مسیر اصلی): همه‌ی مخاطب‌ها — حتی کسانی که قبلا با بات
#    چت کرده‌اند — می‌توانند به پست کانال react کنند. بات به‌عنوان ادمین
#    update‌ی message_reaction با user و فهرست کامل واکنش‌های آن کاربر
#    (new_reaction) می‌گیرد. بدون deep link، بدون چت خصوصی، با cron معمولی.
#
# ۲. دکمه‌های url با deep-link /start (مسیر فرعی): مخاطب می‌زند → چت خصوصی
#    با بات باز می‌شود و /start با payload «fb:pid:vote» می‌رسد؛ هر اجرا
#    (حداکثر یک ساعت بعد) با getUpdates جمع می‌شود و تأیید در چت خصوصی می‌رود.
#    نکته: اگر کاربر قبلا با بات شروع کرده باشد، تلگرام payload را نمی‌فرستد
#    و فقط چت را باز می‌کند — برای همین واکنش مسیر اصلی است.
#
# pid کوچک است چون payload باید کوتاه باشد؛ mapping کامل در state.json است.
# callback_query با همان شکل data هم پردازش می‌شود — اگر پستی جایی برسد که
# بات callback بگیرد (مثلا‌ گروهی که در آن عضو است) همان مسیر ادامه می‌یابد.

VOTES = ("good", "excellent", "useful", "bad", "repeat", "stale")

FEEDBACK_BUTTONS = [
    ("👍 بود", "good"),
    ("👎 نبود", "bad"),
    ("🔁 تکراری", "repeat"),
    ("🕒 قدیمی", "stale"),
]

CONFIRM = {
    "good": "✅ رأی «بود» ثبت شد. ممنون!",
    "excellent": "✅ رأی «خیلی بود» ثبت شد. ممنون!",
    "useful": "✅ رأی «مفید بود» ثبت شد. ممنون!",
    "bad": "✅ رأی «نبود» ثبت شد — در نظر می‌گیرم.",
    "repeat": "✅ «تکراری بود» ثبت شد. ممنون که گفتی.",
    "stale": "✅ «قدیمی بود» ثبت شد.",
}

# اگر /start بدون payload fb: آمد (مثلا‌ اولین بار که مخاطب با بات حرف می‌زند).
GREETING = "سلام! من بات خبرهای تکنولوژی‌ام. رأی‌ات ثبت شد و کمک می‌کند کانال را بهتر کنم."

# دکمه‌ی پستی ضربه خورده که پستش از حافظه‌ی state پُریده شده (قدیمی است).
STALE = "این پست قدیمی است و رأی‌اش دیگر ذخیره نمی‌شود — ولی ممنون که وقت گذاشتی."

# ── نظّم بازخورد (مسیر اصلی) ───────────────────────────────────────
#
# نظّم تلگرام مکانیزم بومیِ جمع‌آوری نظر در کانال‌هاست: مخاطب با یک ضربه رأی
# می‌دهد، تلگرام «هر کاربر یک رأی» را خودش اعمال می‌کند و نتیجه‌ی عمومی روی
# نظّم نمایش می‌یابد. نظّم‌های کانال همیشه ناشناس‌اند (API اجازه‌ی نظّم
# غیروابسته در چت کانال نمی‌دهد)؛ پس به‌جای رأی‌به‌رأی، وقتی نظّم بسته می‌شود
# (۲۴ ساعت، یا بسته‌شدن دستی) یک update «poll» با شمارش نهایی هر گزینه به
# بات می‌رسد و همین‌جا ثبت می‌شود. بدون backend و بدون deep link — همان
# معماری‌ی cron، و برای همه‌ی اعضای کانال کار می‌کند.
POLL_QUESTION = "این خبر چطور بود؟ یک گزینه را انتخاب کن."
POLL_OPTIONS = [
    ("👍 بود", "good"),
    ("👎 نبود", "bad"),
    ("🔁 تکراری", "repeat"),
    ("🕒 قدیمی", "stale"),
]
POLL_OPTION_VOTES = {text: vote for text, vote in POLL_OPTIONS}
POLL_DURATION = 24 * 3600  # حداکثر مجاز در API


# هشِ نسخهٔ قالب Instant View در instantview.telegram.org (دامنهٔ
# mahdine.github.io). با هر ویرایش قالب در ادیتور این هش عوض می‌شود —
# لینک «View in Telegram» ادیتور rhash جدید را می‌دهد؛ آنجا را به‌روز کن.
IV_RHASH = os.environ.get("IV_RHASH", "3f8f57ce2ac84f")


def iv_link(page_url: str) -> str:
    """لینک t.me/iv: صفحهٔ ترجمه‌شده به‌صورت Instant View با قالب خودمان
    باز می‌شود — بدون نیاز به تأیید تیم تلگرام (audience خودمان).

    دکمه‌ی «ترجمه کامل» پست‌ها هم همین را می‌سازد: یک tap، ترجمه‌ی کامل،
    داخل تلگرام — بدون چت و بدون بات.
    """
    return f"https://t.me/iv?url={quote(page_url, safe='')}&rhash={IV_RHASH}"


def iv_delivery_mode() -> str:
    """حالت تحویل لینک «ترجمه کامل».

    `text` (طراحی نهایی): لینک IV در متن پست — Instant View درون اپ.
    `button` (حالت موقت): دکمه‌ی «ترجمه کامل» مستقیم روی URL صفحه — webview
    درون اپ.

    حالت button فالبک وقتی است که تلگرام Instant View را روی پیام‌های بات
    سرکوب کند (مشاهده‌شده 2026-11-22: لینک t.me/iv به صفحه‌ی بینابینی
    "OPEN LINK" بدل می‌شود و فقط پیام‌های انسانیِ خودِ owner کانال IV
    می‌گیرند). چون button به IV وابسته نیست، هم‌زمانی که IV خاموش است هم
    کار می‌کند و خواننده ترجمه‌ی کامل را درون اپ می‌بیند. با env
    `IV_DELIVERY_MODE` تعیین می‌شود؛ پیش‌فرض text است.
    """
    return (os.environ.get("IV_DELIVERY_MODE", "text").strip().lower()) or "text"


def delivery_link(page_url: str) -> str:
    """لینک مقصدِ «ترجمه کامل» بر اساس حالت تحویل.

    `text` → t.me/iv (Instant View درون اپ). `button` → خودِ URL صفحه
    (webview درون اپ). همه‌جا که لینک «خواندن ترجمه کامل» می‌سازیم این را
    صدا می‌زنیم تا سوییچ حالت یک‌جا و منسجم باشد.
    """
    if iv_delivery_mode() == "button":
        return page_url
    return iv_link(page_url)


def vote_url(bot_username: str, pid: str, vote: str) -> str:
    return f"https://t.me/{bot_username}?start=fb:{pid}:{vote}"


def parse_vote(payload: str) -> tuple[str, str] | None:
    """«fb:123:good» → («123», «good»). هر چیز دیگر None."""
    parts = payload.strip().split(":", 2)
    if len(parts) != 3 or parts[0] != "fb":
        return None
    pid, vote = parts[1], parts[2]
    if not pid.isdigit() or vote not in VOTES:
        return None
    return pid, vote


def translate_url(bot_username: str, pid: str = "") -> str:
    """لینک دکمه‌ی «ترجمه کامل»: چت خصوصی بات با payload tr:<pid>.

    payload فقط در اولین /start همان کاربر می‌رسد (قلم تلگرام) — worker
    همان پست را مستقیم ترجمه می‌کند. کاربر بازگشتی بدون payload فقط چت را
    باز می‌کند و پست را forward می‌کند (مسیر اصلی که همیشه کار می‌کند).
    """
    payload = f"tr:{pid}" if pid else "tr"
    return f"https://t.me/{bot_username}?start={payload}"


# پیشوند ثابتِ پیام دکمه‌ی «ترجمه کامل» (share dialog). worker همین پیشوند
# را می‌بیند، لینک را از متن می‌کَنَد و ترجمه را خودکار شروع می‌کند. برای همه‌ی
# کاربران — اول‌بار و بازگشتی — کار می‌کند، برخلاف ?start که payload‌اش فقط
# در اولین /start می‌رسد و کلیکِ کاربر بازگشتی هیچ event تولید نمی‌کند.
TRANSLATE_SHARE_PREFIX = "ترجمه کامل این پست"


def share_url(url: str, text: str) -> str:
    """لینک share dialog تلگرام با لینک مقاله و متن درخواست."""
    return f"https://t.me/share/url?url={quote(url, safe='')}&text={quote(text, safe='')}"


def parse_translate(payload: str) -> str | None:
    """«tr:123» → «123»، «tr» → «». هر چیز دیگر None."""
    parts = payload.strip().split(":", 1)
    if parts[0] != "tr":
        return None
    pid = parts[1].strip() if len(parts) > 1 else ""
    if pid and not pid.isdigit():
        return None
    return pid


def build_keyboard(
    item: Item,
    pid: str = "",
    bot_username: str = "",
    feedback: bool = True,
    translate_target: str = "",
) -> dict:
    """دکمه‌های زیر پست.

    ردیف اول (وقتی فعال باشد) چهار دکمه‌ی بازخورد است و ردیف دوم «منبع»،
    «ترجمه کامل» و «دیدگاه‌ها» وقتی بحث جدا از مقاله دارد (HN، Lobsters).
    در Ask HN مقاله و بحث یکی‌اند و دکمه‌ی دیدگاه‌ها تکرار، پس جا نمی‌افتد.
    همه url هستند — backend نمی‌خواهند.

    «ترجمه کامل»: اگر صفحه‌ی ترجمه‌شده deploy شده (translate_target)، لینک
    IV به متن خودِ پست می‌افتد (build_message) و دکمه‌ای نمی‌افتد — لینک
    داخل متن است که IV درون اپ باز می‌کند. وگرنه فالبک = دکمه‌ی share dialog
    که پیام را به چت بات می‌رساند و worker ترجمه را خودکار شروع می‌کند.
    """
    rows = []
    if feedback and pid and bot_username:
        rows.append(
            [{"text": text, "url": vote_url(bot_username, pid, vote)} for text, vote in FEEDBACK_BUTTONS]
        )
    source_row = [{"text": "منبع", "url": item.url}]
    mode = iv_delivery_mode()
    if translate_target and mode == "button":
        # حالت موقت: دکمه مستقیم روی URL صفحه — webview درون اپ، همیشه
        # کار می‌کند (IV روی پیام‌های بات سرکوب‌شده است؛ 2026-11-22).
        source_row.append({"text": "ترجمه کامل", "url": translate_target})
    elif bot_username and not translate_target:
        # فالبک: share dialog با لینک مقاله؛ کاربر بات را انتخاب می‌کند و
        # worker لینک را می‌کَنَد و ترجمه را خودکار شروع می‌کند.
        share_text = f"{TRANSLATE_SHARE_PREFIX} را برای {bot_username} بفرست"
        source_row.append({"text": "ترجمه کامل", "url": share_url(item.url, share_text)})
    if item.discussion_url and item.discussion_url != item.url:
        source_row.append({"text": "دیدگاه‌ها", "url": item.discussion_url})
    rows.append(source_row)
    return {"inline_keyboard": rows}


def get_username(token: str, client: httpx.Client) -> str:
    """username بات، برای ساختن لینک t.me/<bot>."""
    try:
        result = _call("getMe", {}, token, client)
        return (result or {}).get("username", "") if isinstance(result, dict) else ""
    except TelegramError as exc:
        log.warning("getMe نشد: %s", exc)
        return ""


def ensure_polling(token: str, client: httpx.Client) -> None:
    """getUpdates فقط وقتی جواب می‌دهد که webhook تنظیم نباشد.

    یک‌بار در هر اجرا حذف می‌کنیم (idempotent) تا هیچ webhook فراموش‌شده‌ای
    جلوی جمع‌آوری رأی را نگیرد.
    """
    try:
        _call("deleteWebhook", {"drop_pending_updates": False}, token, client)
    except TelegramError as exc:
        log.warning("deleteWebhook نشد: %s", exc)


def drain_feedback(token: str, client: httpx.Client, state) -> int:
    """رأی‌های رسیده از اجرای قبل را جمع می‌کند و تأییدشان را می‌فرستد.

    تعداد رأی ثبت‌شده برمی‌گرداند. state را تغییر می‌دهد (posts، last_update_id)؛
    ذخیره‌ی state با caller است.
    """
    ensure_polling(token, client)
    recorded = 0
    confirmed = 0

    while True:
        updates = _call(
            "getUpdates",
            {
                "offset": state.last_update_id + 1,
                "limit": 100,
                "timeout": 0,
                "allowed_updates": [
                    "message",
                    "callback_query",
                    "message_reaction",
                    "message_reaction_count",
                    "poll_answer",
                    "poll",
                ],
            },
            token,
            client,
        )
        if not isinstance(updates, list) or not updates:
            break
        for update in updates:
            state.last_update_id = max(state.last_update_id, update.get("update_id", 0))
            callback = update.get("callback_query")
            if callback:
                parsed = parse_vote(callback.get("data", "") or "")
                if parsed:
                    pid, vote = parsed
                    if state.record_vote(pid, callback.get("from", {}).get("id", 0), vote):
                        recorded += 1
                    try:
                        _call(
                            "answerCallbackQuery",
                            {"callback_query_id": callback.get("id"), "text": "ثبت شد ✓"},
                            token,
                            client,
                        )
                    except TelegramError:
                        pass
                continue

            reaction = update.get("message_reaction")
            if reaction:
                # مسیر اصلی: react به پست کانال. user فقط وقتی هست که واکنش
                # ناشناس نباشد؛ new_reaction فهرست کامل واکنش‌های آن کاربر است.
                user = reaction.get("user")
                if user:
                    emojis = [
                        rt.get("emoji")
                        for rt in reaction.get("new_reaction") or []
                        if rt.get("type") == "emoji" and rt.get("emoji")
                    ]
                    pid = state.find_pid_by_message(
                        reaction.get("message_id"),
                        reaction.get("chat", {}).get("id"),
                    )
                    if pid and state.record_reaction(pid, user.get("id", 0), emojis):
                        recorded += 1
                continue

            rc = update.get("message_reaction_count")
            if rc:
                # مسیر اصلی برای کانال: واکنش‌های کانال ناشناس‌اند و تلگرام
                # به‌جای user id، شمارش مطلق هر ایموجی روی پیام را می‌فرستد
                # (grouped؛ تا چند دقیقه تأخیر ممکن است).
                pid = state.find_pid_by_message(
                    rc.get("message_id"),
                    rc.get("chat", {}).get("id"),
                )
                if pid:
                    # هر عنصر: {type: {type: "emoji", emoji: ...}, total_count: n}
                    counts = {}
                    for rt in rc.get("reactions") or []:
                        r = rt.get("type") or {}
                        if r.get("type") == "emoji" and r.get("emoji"):
                            counts[r["emoji"]] = rt.get("total_count", 0)
                    if state.record_reaction_counts(pid, counts):
                        recorded += 1
                continue

            poll_answer = update.get("poll_answer")
            if poll_answer:
                # نظّم‌های غیروابسته (فقط در گروه‌ها ممکن‌اند؛ کانال همیشه
                # ناشناس است): هر رأی به‌صورت جداگانه با شناسه‌ی کاربر می‌آید.
                user = poll_answer.get("user")
                option_ids = poll_answer.get("option_ids") or []
                if user and option_ids:
                    idx = option_ids[0]
                    vote = POLL_OPTIONS[idx][1] if 0 <= idx < len(POLL_OPTIONS) else None
                    if vote:
                        pid = state.find_pid_by_poll(poll_answer.get("poll_id"))
                        if pid and state.record_vote(pid, user.get("id", 0), vote):
                            recorded += 1
                continue

            poll_update = update.get("poll")
            if poll_update:
                # مسیر اصلی برای کانال: وقتی نظّم بسته می‌شود (۲۴ ساعت یا
                # بسته‌شدن دستی) شمارش نهایی هر گزینه می‌آید. تلگرام خودش
                # هر کاربر یک رأی را اعمال کرده است.
                if poll_update.get("is_closed"):
                    pid = state.find_pid_by_poll(poll_update.get("id"))
                    if pid:
                        counts = {}
                        for i, opt in enumerate(poll_update.get("options") or []):
                            vote = POLL_OPTIONS[i][1] if i < len(POLL_OPTIONS) else None
                            if vote:
                                counts[vote] = opt.get("voter_count", 0)
                        if state.record_poll_counts(pid, counts):
                            recorded += sum(counts.values())
                continue

            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            if not text.startswith("/start"):
                continue
            payload = text[len("/start"):].strip()
            user_id = message.get("from", {}).get("id", 0)
            chat_id = message.get("chat", {}).get("id")
            parsed = parse_vote(payload)
            if not parsed:
                if chat_id:
                    try:
                        _call("sendMessage", {"chat_id": chat_id, "text": GREETING}, token, client)
                    except TelegramError as exc:
                        log.warning("سلام به کاربر نرفت: %s", exc)
                continue
            pid, vote = parsed
            if state.record_vote(pid, user_id, vote):
                recorded += 1
            if chat_id:
                confirmed += 1
                try:
                    _call(
                        "sendMessage",
                        {"chat_id": chat_id, "text": CONFIRM[vote] if state.posts.get(str(pid)) else STALE},
                        token,
                        client,
                    )
                except TelegramError as exc:
                    log.warning("تأیید رأی نرفت: %s", exc)
        if len(updates) < 100:
            break

    if recorded or confirmed:
        log.info("بازخورد: %d رأی ثبت شد (%d تأیید فرست شد)", recorded, confirmed)
    return recorded


def publish(
    item: Item,
    title: str,
    body: str,
    token: str,
    channel: str,
    client: httpx.Client,
    tag: str = "",
    pid: str = "",
    bot_username: str = "",
    feedback: bool = True,
    translate_target: str = "",
    translate_in_text: bool = True,
) -> dict:
    """پست را با عکس بزرگ بالای متن می‌فرستد.

    از sendPhoto استفاده نمی‌کنیم چون caption اش به ۱۰۲۴ کاراکتر محدود است و
    خلاصه‌های ما بلندترند. link preview همان عکس را بزرگ نشان می‌دهد بدون آن
    محدودیت. result تلگرام برمی‌گردد (message_id برای نگاشت واکنش‌ها).

    `translate_in_text`: لینک IV در خودِ متن بیفتد یا نه. پست‌های عکس‌دار
    False می‌گیرند — کارت preview (و هر media) لینک‌های متن را از IV درون‌اپ
    بیرون می‌فرستد (تست کانال 2026-11-22: L ❌ با preview / E ✅ بدون) — و
    لینک IV به پیام دوم خالص‌متنی می‌رود (publish_iv_message). در آن حالت یک
    خط راهنما «ترجمه‌ی کامل: پیام بعدی» به‌جای لینک می‌افتد.
    """
    mode = iv_delivery_mode()
    # حالت button: لینکی به متن نمی‌افتد (دکمه‌ی «ترجمه کامل» می‌افتد) و
    # پیام دوم وجود ندارد، پس خط راهنما هم نمی‌افتد.
    if translate_target and translate_in_text and mode == "text":
        t_url = iv_link(translate_target)
    else:
        t_url = ""
    text = build_message(
        item, title, body, tag, channel,
        translate_url=t_url,
        translate_hint=bool(translate_target and not translate_in_text and mode == "text"),
    )
    keyboard = build_keyboard(item, pid, bot_username, feedback, translate_target)
    has_image = bool(item.image_url)
    if has_image:
        preview = {
            "url": item.image_url,
            "prefer_large_media": True,
            "show_above_text": True,
        }
    else:
        preview = {"is_disabled": True}

    try:
        return _call(
            "sendMessage",
            {
                "chat_id": channel,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": preview,
                "reply_markup": keyboard,
            },
            token,
            client,
        )
    except TelegramError as exc:
        if not has_image:
            raise
        log.warning("ارسال با عکس نشد، بدون عکس تلاش می‌کنم: %s", exc)

    return _call(
        "sendMessage",
        {
            "chat_id": channel,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "reply_markup": keyboard,
        },
        token,
        client,
    )


def publish_iv_message(
    token: str,
    channel: str,
    client: httpx.Client,
    translate_url: str,
) -> dict:
    """پیام دومِ پست‌های عکس‌دار: لینک «ترجمه‌ی کامل» به‌صورت خالص‌متنی.

    نه preview، نه media: هر دو لینک‌های متن را از IV درون‌اپ بیرون می‌فرستند
    (تست‌های کانال 2026-11-22: A/C/E/G ✅ متن خالص — حتی با کیبورد کامل /
    H/J/K ❌ sendPhoto / L ❌ کارت preview). پیام خالصِ لینکی = IV درون اپ.
    کیبورد ندارد چون این پیام فقط لینک است؛ دکمه‌ها روی پیام اول می‌مانند.
    """
    return _call(
        "sendMessage",
        {
            "chat_id": channel,
            "text": f'📄 <a href="{translate_url}">ترجمه‌ی کامل</a>',
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        token,
        client,
    )


def get_chat_id(chat: str, token: str, client: httpx.Client) -> int | None:
    """شناسه‌ی عددی چت (کانال) — برای تطبیق update‌های واکنش با پست‌ها."""
    try:
        result = _call("getChat", {"chat_id": chat}, token, client)
    except TelegramError as exc:
        log.warning("getChat نشد: %s", exc)
        return None
    return result.get("id") if isinstance(result, dict) else None


def send_feedback_poll(token: str, channel: str, client: httpx.Client) -> str | None:
    """نظّم بازخورد را می‌فرستد و poll_id برمی‌گرداند (برای نگاشت poll_answer)."""
    try:
        result = _call(
            "sendPoll",
            {
                "chat_id": channel,
                "question": POLL_QUESTION,
                "options": [text for text, _ in POLL_OPTIONS],
                "type": "regular",
                "is_anonymous": True,
                "allows_multiple_answers": False,
                "open_period": POLL_DURATION,
            },
            token,
            client,
        )
    except TelegramError as exc:
        log.warning("نظّم نرفت: %s", exc)
        return None
    # sendPoll خودش همان اشیاء message را برمی‌گرداند (poll داخلش است).
    poll = result.get("poll") if isinstance(result, dict) else None
    return poll.get("id") if isinstance(poll, dict) else None


def _call(method: str, payload: dict, token: str, client: httpx.Client):
    """یک روش Bot API. مقدار خام result را برمی‌گرداند (dict یا list).

    برای getUpdates result لیست updateهاست، برای بقیه معمولاً dict یا None.
    """
    resp = client.post(API.format(token=token, method=method), json=payload, timeout=60)
    if resp.status_code != 200:
        raise TelegramError(f"{method} {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("result")


def send_digest(text: str, token: str, channel: str, client: httpx.Client) -> None:
    """پست خلاصه‌ی هفتگی. متنی است چون چند لینک دارد، نه یک خبر واحد."""
    _call(
        "sendMessage",
        {
            "chat_id": channel,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        token,
        client,
    )
