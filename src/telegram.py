"""ارسال پست به کانال از طریق Bot API."""

import html
import logging

import httpx

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
READ_MORE = "برای ادامه‌ی خواندن روی <b>source</b> بزنید"


def build_message(
    item: Item,
    title: str,
    body: str,
    tag: str = "",
    channel: str = "",
) -> str:
    """تیتر، متن، خط منبع، و ردیف هشتگ و امضا."""
    source = html.escape(item.publisher or item.label)
    if item.publisher and item.publisher != item.label:
        source += f"  ·  via {html.escape(item.label)}"

    footer = "  ".join(part for part in (tag, _handle(channel)) if part)
    fixed = len(source) + len(footer) + len(title) + 12
    body = body.strip()
    if len(body) > MAX_TEXT - fixed:
        body = body[: MAX_TEXT - fixed].rsplit(" ", 1)[0] + "…"

    parts = []
    if title:
        parts.append(f"<b>{html.escape(title)}</b>")
    parts.append(html.escape(body))
    parts.append(f"<i>{source}</i>")
    if footer:
        parts.append(html.escape(footer))
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


def build_keyboard(item: Item, pid: str = "", bot_username: str = "", feedback: bool = True) -> dict:
    """دکمه‌های زیر پست.

    ردیف اول (وقتی فعال باشد) چهار دکمه‌ی بازخورد است و ردیف دوم دکمه‌ی
    source. همه url هستند — نه دکمه‌ای backend زنده می‌خواهد.
    """
    rows = []
    if feedback and pid and bot_username:
        rows.append(
            [{"text": text, "url": vote_url(bot_username, pid, vote)} for text, vote in FEEDBACK_BUTTONS]
        )
    rows.append([{"text": "source", "url": item.url}])
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
) -> dict:
    """پست را با عکس بزرگ بالای متن می‌فرستد.

    از sendPhoto استفاده نمی‌کنیم چون caption اش به ۱۰۲۴ کاراکتر محدود است و
    خلاصه‌های ما بلندترند. link preview همان عکس را بزرگ نشان می‌دهد بدون آن
    محدودیت. result تلگرام برمی‌گردد (message_id برای نگاشت واکنش‌ها).
    """
    text = build_message(item, title, body, tag, channel)
    keyboard = build_keyboard(item, pid, bot_username, feedback)
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
