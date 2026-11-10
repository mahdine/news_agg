"""ساخت scorecard: یک صفحه‌ی HTML استاتیک دو-تب.

هر اجرا (غیر از dry-run) این فایل را روی disk می‌نویسد و workflow کنار
state.json همان را commit می‌کند؛ GitHub Pages آن را رایگان سرو می‌کند.
بدون سرور و بدون وابستگی خارجی — HTML کاملا‌ self-contained است.

تب ۱ «Scorecard» — داشبورد تشخیص: داده می‌دهد و فهرست پیشنهادِ قابل‌اقدام
می‌دهد.
تب ۲ «Config» — ویرایشگر: تغییرات را *روی متن اصلی config.yaml* اعمال
می‌کند (line-oriented، همان الگوریتم src/configedit.py) تا کامنت‌های فارسی
فایل حفظ بمانند. خروجی، YAML کامل یا فهرست تغییرات است که کاربر کپی می‌کند
و یا در GitHub commit می‌کند یا برای bot می‌فرستد — خودِ صفحه هیچ‌جا نمی‌نویسد
(GitHub Pages فقط خواندنی است و token در مرورگر با فلسفه‌ی $0 پروژه نمی‌آید).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import configedit

# وقت نمایشی صفحه (محاسبات داخلی روی UTC می‌مانند).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Iran")

# فیلدهای قابل‌ویرایش هر نوع منبع (ترتیب = نمایش در تب Config).
KIND_FIELDS = {
    "hackernews": ["min_points"],
    "lobsters": ["min_engagement"],
    "bluesky": ["min_engagement"],
    "rss": ["authority", "max_age_hours"],
}

# تنظیمات عمومی قابل‌ویرایش: (key, label فارسی, نوع).
SETTING_FIELDS = [
    ("max_posts_per_run", "پست در هر اجرا", "number"),
    ("spare_candidates", "کاندیدای یدکی", "number"),
    ("viral_share", "سهم viral (۰ تا ۱)", "number"),
    ("max_age_hours", "سقف سن منابع ویروسی (ساعت)", "number"),
    ("editorial_max_age_hours", "سقف سن فید خبری (ساعت)", "number"),
    ("seconds_between_posts", "فاصله‌ی پست‌ها (ثانیه)", "number"),
    ("min_text_length", "حداقل طول متن فید", "number"),
    ("scrape_images", "استخراج تصویر", "checkbox"),
    ("min_image_side", "حداقل ضلع تصویر (px)", "number"),
    ("scrape_article_text", "استخراج متن مقاله", "checkbox"),
    ("weekly_digest", "خلاصه‌ی هفتگی", "checkbox"),
    ("digest_weekday", "روز هفته (۰=دوشنبه…۴=جمعه)", "number"),
    ("digest_hour_utc", "ساعت digest (UTC)", "number"),
    ("digest_size", "اندازه‌ی digest", "number"),
]


@dataclass
class SourceRow:
    label: str
    pool: str = "editorial"          # viral | editorial (از type config)
    kind: str = "rss"                # hackernews | lobsters | bluesky | rss
    authority: float | None = None
    min_engagement: float | None = None
    min_points: float | None = None

    posts_total: int = 0             # بلندمدت، از posts_by_label
    posts_7d: int = 0                # از history (که پنجره‌ی ۷ روز دارد)
    last_at: datetime | None = None
    skips: int = 0
    avg_score: float = 0.0
    score_sum: float = 0.0
    last_title: str = ""
    last_url: str = ""


def _parse(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def build_rows(config: dict, state) -> list[SourceRow]:
    """یک ردیف برای هر منبع فعال، پر از آمار.

    کل تعداد پست و آخرین پست از `posts_by_label`/`last_post` می‌آیند (بلندمدت).
    شمارش ۷ روزه و میانگین امتیاز از `history` (که پنجره‌ی ۷ روز دارد).
    """
    now = datetime.now(timezone.utc)
    d7 = now - timedelta(days=7)

    rows: dict[str, SourceRow] = {}
    for src in config.get("sources", []):
        label = src.get("label") or src.get("handle") or src.get("url")
        kind = src.get("type", "rss")
        rows[label] = SourceRow(
            label=label,
            kind=kind,
            pool="viral" if kind in ("hackernews", "lobsters", "bluesky") else "editorial",
            authority=src.get("authority"),
            min_engagement=src.get("min_engagement"),
            min_points=src.get("min_points"),
            posts_total=state.posts_by_label.get(label, 0),
            last_at=_parse(state.last_post.get(label)),
        )

    # history: پنجره‌ی ۷ روز؛ برای شمارش ۷ روزه، میانگین امتیاز و نمونه‌ی
    # آخرین پست. ردیف‌ها که label ندارند (پست‌های قدیمی) نادیده می‌شوند.
    for entry in state.history:
        row = rows.get(entry.get("label"))
        if row is None:
            continue
        at = _parse(entry.get("at"))
        if at and at >= d7:
            row.posts_7d += 1
        if row.last_at is None or (at and at > row.last_at):
            row.last_at = at
        if entry.get("title") and (row.last_at and _parse(entry.get("at")) == row.last_at):
            row.last_title = entry.get("title", "")
            row.last_url = entry.get("url", "")
        row.score_sum += entry.get("score", 0.0)

    for row in rows.values():
        if row.posts_7d:
            row.avg_score = row.score_sum / row.posts_7d

    for label, count in state.skips.items():
        if label in rows:
            rows[label].skips = count

    return sorted(rows.values(), key=lambda r: (-r.posts_total, r.label))


def pool_share(state) -> tuple[int, int]:
    viral = state.posted.get("viral", 0)
    editorial = state.posted.get("editorial", 0)
    return viral, editorial


def health_checks(rows: list[SourceRow], viral: int, editorial: int, target_share: float) -> list[str]:
    """فهرست پیشنهادهای قابل‌اجرا."""
    now = datetime.now(timezone.utc)
    checks: list[str] = []

    # ۱. منابع خاموش: فعال‌اند ولی هیچ پست نداده‌اند.
    silent = [r for r in rows if r.posts_total == 0]
    if silent:
        names = ", ".join(r.label for r in sorted(silent, key=lambda r: r.label))
        checks.append(
            f"🔇 {len(silent)} منبع فعال تا حالا هیچ پست نداده‌اند: {names}. "
            "برای هرکدام یکی از سه دلیل: URL فید مرده، آستانه‌ی min_engagement/min_points سخت، "
            "یا همیشه رقیب قوی‌تری جلوتر است. فیدها را دستی باز کن و min_* را شل کن."
        )

    # ۲. skip rate بالاست.
    for r in rows:
        attempts = r.posts_total + r.skips
        if r.skips >= 5 and r.skips / attempts > 0.5:
            checks.append(
                f"⏭️ {r.label}: مدل {r.skips} خبرش را SKIP کرده (روی {attempts} تلاش). "
                "اگر پست کم هم می‌دهد، احتمالاً بیشترش نویز است — min_engagement را بالا ببر یا حذفش کن."
            )

    # ۳. سهم استخرها.
    total = viral + editorial
    if total >= 20:
        actual = viral / total
        dev = abs(actual - target_share)
        if dev > 0.15:
            which = "viral" if actual > target_share else "editorial"
            checks.append(
                f"⚖️ سهم واقعی استخر {which} ≈ {actual*100:.0f}٪ است در مقابل هدف {target_share*100:.0f}٪. "
                "اگر همین تعادل مطلوب است بگذار؛ وگرنه viral_share را تنظیم کن یا منبعِ استخر قوی‌تر را شل/سخت کن."
            )

    # ۴. تمرکز بالا روی یک منبع (۷ روز اخیر).
    recent = [r for r in rows if r.posts_7d]
    if recent:
        top = max(recent, key=lambda r: r.posts_7d)
        tot7 = sum(r.posts_7d for r in recent)
        if tot7 >= 10 and top.posts_7d / tot7 > 0.25:
            checks.append(
                f"🎯 {top.label} {top.posts_7d} پست در ۷ روز اخیر ({top.posts_7d/tot7*100:.0f}٪ کل). "
                "REPEAT_PENALTY فعال است؛ اگر هنوز زیاد به نظر می‌رسد، authority/min_engagement آن را سخت‌تر کن."
            )

    # ۵. منابعی که آخرین پستشان قدیمی‌تر از پنجره‌ی سنی‌شان است.
    stale = []
    for r in rows:
        if r.posts_total and r.last_at:
            age_h = (now - r.last_at).total_seconds() / 3600
            window = 30 if r.pool == "viral" else 8
            if age_h > window:
                stale.append((r, age_h))
    if stale:
        stale.sort(key=lambda t: -t[1])
        worst = ", ".join(f"{r.label} ({a/24:.0f} روز)" for r, a in stale[:5])
        checks.append(f"🕰️ منابعی که مدتی است پست ندادند: {worst}.")

    return checks


# ─────────────────────────────────────────────── تب Config: داده‌ها

def _coerce(value):
    """string خام YAML → عدد/بول اگر شد، وگرنه string."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        v = value.strip()
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v
    return value


def enumerate_sources(text: str) -> list[dict]:
    """همه‌ی بلوک‌های منابع (فعال و کامنت‌شده) را با فیلدها برمی‌گرداند.

    از فیلدهای line-orientedِ configedit استفاده می‌کند تا منابع غیرفعال (که
    PyYAML آن‌ها را نمی‌خواند) هم در تب Config دیده شوند و قابل‌فعال‌سازی باشند.
    """
    lines = text.splitlines()
    out: list[dict] = []
    for i, raw in enumerate(lines):
        eff = configedit._uncomment(raw).strip()
        if not eff.startswith("- ") or "- type:" not in eff:
            continue
        block = configedit._list_item_lines(lines, i)
        ident = configedit._item_identity(lines, block)
        if not ident:
            continue
        fields: dict[str, str] = {}
        for j in block:
            s = configedit._strip_comment(configedit._uncomment(lines[j])).strip()
            if s.startswith("- "):
                s = s[2:]
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", s)
            if m:
                fields[m.group(1)] = m.group(2).strip().strip("\"'")
        commented = configedit._is_comment(lines[block[0]])
        out.append({
            "label": ident,
            "type": fields.get("type"),
            "fields": fields,
            "commented": commented,
        })
    return out


def _payload(config: dict, config_text: str) -> str:
    """داده‌ی تب Config برای جاوااسکریپت: settings + همه‌ی منابع (با فیلدها)."""
    sources = []
    for s in enumerate_sources(config_text):
        entry: dict = {
            "label": s["label"],
            "kind": s["type"] or "rss",
            "commented": s["commented"],
        }
        for k, v in s["fields"].items():
            entry[k] = _coerce(v)
        sources.append(entry)
    data = {
        "settings": config.get("settings", {}),
        "sources": sources,
        "kindFields": KIND_FIELDS,
        "settingFields": [[k, lab, ty] for k, lab, ty in SETTING_FIELDS],
    }
    return json.dumps(data, ensure_ascii=False)


# ─────────────────────────────────────────────── قالب HTML (placeholder)

_TEMPLATE = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scorecard — Persian Tech Bot</title>
<style>
  :root {
    --bg: #0d1117; --fg: #e6edf3; --mut: #8b949e; --border: #30363d;
    --viral: #f78166; --editorial: #79c0ff; --accent: #58a6ff;
    --good: #3fb950; --warn: #d29922; --bad: #f85149; --card: #161b22;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 20px; background: var(--bg); color: var(--fg);
    font: 15px/1.6 -apple-system, "Segoe UI", Tahoma, sans-serif;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--mut); margin-bottom: 16px; font-size: 13px; }
  .tabs { display: flex; gap: 8px; margin-bottom: 20px; }
  .tab {
    padding: 8px 18px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--card); color: var(--fg); cursor: pointer; font-size: 14px; font-weight: 600;
  }
  .tab.active { border-color: var(--accent); color: var(--accent); }
  .view { display: none; }
  .view.active { display: block; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .card .n { font-size: 26px; font-weight: 700; }
  .card .l { color: var(--mut); font-size: 12px; margin-top: 2px; }
  .bar { height: 8px; border-radius: 4px; overflow: hidden; display: flex; margin-top: 8px; }
  .bar .v { background: var(--viral); } .bar .e { background: var(--editorial); }
  .legend { display: flex; gap: 16px; margin-top: 8px; font-size: 12px; color: var(--mut); }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-left: 5px; vertical-align: middle; }
  h2 { font-size: 16px; margin: 28px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { color: var(--mut); font-weight: 600; position: sticky; top: 0; background: var(--bg); z-index: 1; }
  td.num { font-variant-numeric: tabular-nums; }
  td.lbl { max-width: 210px; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
  td.mut, .mut { color: var(--mut); }
  tr:hover td { background: #161b22; }
  .pool { font-size: 11px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--border); }
  .pool-viral { color: var(--viral); border-color: var(--viral); }
  .pool-editorial { color: var(--editorial); border-color: var(--editorial); }
  .tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
  .tag-silent { background: #388bfd22; color: var(--accent); }
  .tag-skip { background: #d2992222; color: var(--warn); }
  ul.checks li { margin: 8px 0; line-height: 1.7; }
  .footer { color: var(--mut); font-size: 12px; margin-top: 32px; text-align: center; }
  .tbl-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
  input[type=number] {
    width: 74px; background: var(--bg); color: var(--fg); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 6px; font: inherit; font-size: 13px;
  }
  input[type=number]:focus { outline: none; border-color: var(--accent); }
  input[type=checkbox] { width: 16px; height: 16px; accent-color: var(--accent); cursor: pointer; }
  .row-changed td { background: #1c2530 !important; }
  .fldlbl { display: inline-block; min-width: 88px; color: var(--mut); font-size: 11px; }
  .btn {
    display: inline-block; padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--card); color: var(--fg); cursor: pointer; font: inherit; font-size: 14px; font-weight: 600;
  }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .btn.primary { background: #238636; border-color: #2ea043; color: #fff; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 16px 0; }
  .changelist { font-size: 13px; color: var(--mut); margin: 8px 0; line-height: 1.9; }
  .changelist b { color: var(--fg); }
  #yamlOut {
    width: 100%; min-height: 200px; max-height: 480px; overflow: auto; background: #010409; color: #c9d1d9;
    border: 1px solid var(--border); border-radius: 10px; padding: 14px;
    font: 12px/1.6 ui-monospace, Consolas, monospace; direction: ltr; text-align: left; white-space: pre; tab-size: 2;
  }
  .hint { font-size: 12px; color: var(--mut); line-height: 1.9; margin: 10px 0; }
  .addsrc { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0; align-items: center; }
  .addsrc input, .addsrc select {
    background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 8px; font: inherit; font-size: 13px;
  }
  .addsrc input[type=text] { width: 200px; }
  .addsrc input[type=url] { width: 280px; }
  .note {
    border: 1px solid #58a6ff44; background: #58a6ff11; border-radius: 8px;
    padding: 10px 14px; font-size: 13px; line-height: 1.9; margin: 12px 0;
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>📊 Scorecard</h1>
  <div class="sub">سلامتی منابع و تنظیمات — به‌روزرسانی __TIMESTAMP__ (تهران)</div>

  <div class="tabs">
    <button class="tab active" data-view="score">Scorecard</button>
    <button class="tab" data-view="config">Config</button>
  </div>

  <!-- ═══════════════ تب ۱: Scorecard (تشخیص) ═══════════════ -->
  <div class="view active" id="view-score">
  <div class="cards">
    <div class="card"><div class="n">__N_SOURCES__</div><div class="l">منبع فعال</div></div>
    <div class="card"><div class="n">__N_POSTS__</div><div class="l">کل پست‌ها</div></div>
    <div class="card"><div class="n">__N_SILENT__</div><div class="l">منبع خاموش</div></div>
    <div class="card">
      <div class="n">__VIRAL_PCT__٪ / __ED_PCT__٪</div>
      <div class="l">viral / editorial</div>
      <div class="bar"><div class="v" style="width:__VIRAL_PCT__%"></div><div class="e" style="width:__ED_PCT__%"></div></div>
      <div class="legend"><span><span class="dot" style="background:var(--viral)"></span>viral __VIRAL_N__</span><span><span class="dot" style="background:var(--editorial)"></span>editorial __ED_N__</span></div>
    </div>
  </div>

  <h2>پیشنهادها</h2>
  <ul class="checks">__CHECKS_HTML__</ul>

  <h2>منابع</h2>
  <div class="tbl-scroll">
  <table>
    <thead><tr>
      <th>منبع</th><th>استخر</th><th>۷روز</th><th>کل</th>
      <th>آخرین پست</th><th>skip</th><th>میانگین امتیاز</th><th>تنظیمات</th><th>وضعیت</th>
    </tr></thead>
    <tbody>__BODY_ROWS__</tbody>
  </table>
  </div>
  </div>

  <!-- ═══════════════ تب ۲: Config (ویرایش) ═══════════════ -->
  <div class="view" id="view-config">
  <div class="note">تغییرات اینجا فقط <b>متن YAML</b> می‌سازند؛ خودِ صفحه‌ی استاتیک به هیچ‌جا نمی‌نویسد.
  بعد از زدن «تولید»، YAML را کپی کن و یا در GitHub روی <code>config.yaml</code> paste و commit کن،
  یا فهرست تغییرات را برای bot بفرست. هر تغییر، یک commit با diff در گیت باقی می‌ماند — کامنت‌های فارسی فایل حفظ می‌شوند.</div>

  <h2>تنظیمات عمومی (settings)</h2>
  <div class="tbl-scroll">
  <table id="settingsTable">
    <thead><tr><th>تنظیم</th><th>مقدار فعلی</th><th>تغییر</th></tr></thead>
    <tbody></tbody>
  </table>
  </div>

  <h2>منابع</h2>
  <div class="tbl-scroll">
  <table id="sourcesTable">
    <thead><tr><th>منبع</th><th>نوع</th><th>تنظیم</th><th>فعال</th></tr></thead>
    <tbody></tbody>
  </table>
  </div>

  <h2>افزودن منبع جدید</h2>
  <div class="addsrc">
    <span class="mut">نوع:</span>
    <select id="newType">
      <option value="rss">rss (فید)</option>
      <option value="bluesky">bluesky</option>
    </select>
    <input type="url" id="newUrl" placeholder="https://site.com/feed.xml">
    <input type="text" id="newHandle" placeholder="handle.bsky.social" style="display:none">
    <input type="text" id="newLabel" placeholder="label">
    <input type="number" id="newAuthority" placeholder="authority" step="0.05" min="0" max="1" style="width:90px">
    <input type="number" id="newMinEng" placeholder="min_engagement" step="1" style="display:none;width:120px">
    <button class="btn" id="addBtn">افزودن</button>
  </div>

  <div class="actions">
    <button class="btn primary" id="genBtn" disabled>📋 تولید config.yaml</button>
    <button class="btn" id="copyYamlBtn" style="display:none">کپی YAML کامل</button>
    <button class="btn" id="copyChangesBtn" style="display:none">📝 کپی تغییرات برای چت</button>
    <button class="btn" id="resetBtn" style="display:none">بازنشانی</button>
  </div>
  <div class="changelist" id="changes"></div>
  <pre id="yamlOut" style="display:none"></pre>
  <div class="hint">نکته: برای حذف یک منبع، تیک «فعال» را بردار — بلوکش کامنت می‌شود (نه حذف) و بعدا‌ با زدن دوباره‌ی تیک برمی‌گردد.
  ردیف‌های آبی‌رنگ یعنی تغییری نسبت به مقدار فعلی دارند.</div>
  </div>

  <div class="footer">تولید خودکار توسط bot · scorecard.html کنار state.json commit می‌شود</div>
</div>

<script>
const CONFIG = __CONFIG_JSON__;
const CONFIG_TEXT = __CONFIG_TEXT_JSON__;
let TEXT = CONFIG_TEXT;

// ── کمک‌های line-oriented (همان الگوریتم configedit.py) ──
const isComment = l => l.trimStart().startsWith("#");
const uncomment = l => isComment(l) ? l.replace(/^(\\s*)#\\s?/, "$1") : l;
const leadingSpaces = s => (s.match(/^ */) || [""])[0].length;
function stripComment(l) {
  let out = "", str = null;
  for (let i = 0; i < l.length; i++) {
    const ch = l[i];
    if (str) { out += ch; if (ch === str) str = null; continue; }
    if (ch === '"' || ch === "'") { str = ch; out += ch; }
    else if (ch === "#" && (i === 0 || l[i-1] === " " || l[i-1] === "\\t")) break;
    else out += ch;
  }
  return out.replace(/\\s+$/, "");
}
function listItemLines(ls, start) {
  const out = [start];
  for (let i = start + 1; i < ls.length; i++) {
    const raw = ls[i];
    if (!raw.trim()) break;
    const eff = uncomment(raw).trim();
    if (eff.startsWith("- ")) break;
    if (leadingSpaces(uncomment(raw)) < 2) break;
    out.push(i);
  }
  return out;
}
function itemIdentity(ls, block) {
  for (const want of ["label", "handle"]) {
    for (const i of block) {
      const raw = stripComment(uncomment(ls[i])).trim();
      const m = raw.match(new RegExp("^" + want + "\\\\s*:\\\\s*(.+)$"));
      if (m) return m[1].trim().replace(/^["']|["']$/g, "");
    }
  }
  return null;
}
function findSourceBlock(label) {
  const ls = TEXT.split("\\n");
  const norm = label.toLowerCase();
  for (let i = 0; i < ls.length; i++) {
    const eff = uncomment(ls[i]).trim();
    if (!eff.startsWith("- ") || !eff.includes("- type:")) continue;
    const block = listItemLines(ls, i);
    const ident = itemIdentity(ls, block);
    if (ident && ident.trim().toLowerCase() === norm) return block;
  }
  return null;
}
function fmtVal(v) {
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "number") return String(v);
  const s = String(v).trim();
  if (/[:#[\\]{}&,*!|>'"%@`]/.test(s) || s === "") return '"' + s + '"';
  return s;
}
function applySetting(key, val) {
  const ls = TEXT.split("\\n");
  let s = -1;
  for (let i = 0; i < ls.length; i++) if (ls[i].replace(/\\s+$/, "") === "settings:") { s = i; break; }
  for (let i = s + 1; i < ls.length; i++) {
    const raw = ls[i];
    if (!raw.trim()) continue;
    if (isComment(raw)) continue;
    if (leadingSpaces(raw) <= 0) break;
    const st = stripComment(raw).trim();
    if (st.match(new RegExp("^" + key + "\\\\s*:")) && leadingSpaces(raw) === 2) {
      ls[i] = "  " + key + ": " + fmtVal(val);
      TEXT = ls.join("\\n");
      return;
    }
  }
  throw new Error("تنظیم پیدا نشد: " + key);
}
function applySource(label, field, val) {
  const block = findSourceBlock(label);
  if (!block) throw new Error("منبع پیدا نشد: " + label);
  const ls = TEXT.split("\\n");
  let idx = -1;
  for (const i of block) {
    const s = stripComment(uncomment(ls[i])).trim();
    if (s.match(new RegExp("^" + field + "\\\\s*:"))) { idx = i; break; }
  }
  const v = fmtVal(val);
  const blockCommented = isComment(ls[block[0]]);
  if (idx >= 0) {
    const wasComment = isComment(ls[idx]);
    const base = uncomment(ls[idx]);
    let newLine = base.replace(new RegExp("^(\\\\s*" + field + "\\\\s*:\\\\s*).*$"), "\\$1" + v);
    if (wasComment) newLine = newLine.replace(/^(\\s*)/, "$1# ");
    ls[idx] = newLine;
  } else {
    const first = stripComment(uncomment(ls[block[0]]));
    const fi = leadingSpaces(first) + 2;
    let newLine = " ".repeat(fi) + field + ": " + v;
    if (blockCommented) newLine = newLine.replace(/^(\\s*)/, "$1# ");
    let last = -1;
    for (const i of block) if (stripComment(uncomment(ls[i])).trim()) last = i;
    ls.splice(last + 1, 0, newLine);
  }
  TEXT = ls.join("\\n");
}
function toggleSource(label, enabled) {
  const block = findSourceBlock(label);
  if (!block) throw new Error("منبع پیدا نشد: " + label);
  const ls = TEXT.split("\\n");
  const codeRe = /^(\\s*)#\\s+(?:- .+|(?:type|label|handle|url|authority|min_engagement|min_points|max_age_hours)\\s*:)/;
  for (const i of block) {
    if (isComment(ls[i])) {
      if (enabled && codeRe.test(ls[i])) ls[i] = ls[i].replace(/^(\\s*)#\\s?/, "$1");
    } else {
      if (!enabled) ls[i] = ls[i].replace(/^(\\s*)/, "$1# ");
    }
  }
  TEXT = ls.join("\\n");
}
function addSource(item) {
  const ls = TEXT.replace(/\\n+$/, "").split("\\n");
  const body = ["  - type: " + item.kind];
  if (item.kind === "rss") {
    body.push("    url: " + item.url);
    body.push("    label: " + item.label);
    if (item.authority != null) body.push("    authority: " + fmtVal(item.authority));
  } else {
    body.push("    handle: " + item.handle);
    body.push("    label: " + item.label);
    if (item.min_engagement != null) body.push("    min_engagement: " + item.min_engagement);
  }
  ls.push("", "  # ── منبع جدید (از صفحه‌ی تنظیمات) ──", ...body);
  TEXT = ls.join("\\n") + "\\n";
}
function generateYaml() {
  TEXT = CONFIG_TEXT;
  for (const [k, v] of Object.entries(edits.settings)) applySetting(k, v);
  for (const [label, ch] of Object.entries(edits.sources)) {
    for (const [f, v] of Object.entries(ch)) if (f !== "enabled") applySource(label, f, v);
    if (ch.enabled === false) toggleSource(label, false);
    else if (ch.enabled === true) toggleSource(label, true);
  }
  for (const item of edits.added) addSource(item);
  return TEXT;
}

// ── state ویرایشگر ──
const edits = { settings: {}, sources: {}, added: [] };
const ORIG = {};
for (const s of CONFIG.sources) {
  ORIG[s.label] = { _enabled: !s.commented };
  for (const f of (CONFIG.kindFields[s.kind] || []))
    if (s[f] !== undefined && s[f] !== null) ORIG[s.label][f] = s[f];
}

// ── تب‌ها ──
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".view").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  document.getElementById("view-" + t.dataset.view).classList.add("active");
}));

// ── جدول settings ──
(function() {
  const tb = document.querySelector("#settingsTable tbody");
  for (const [key, label, kind] of CONFIG.settingFields) {
    const cur = CONFIG.settings[key];
    const tr = document.createElement("tr");
    if (kind === "checkbox") {
      tr.innerHTML = `<td>${label}</td><td class="mut">${cur ? "فعال" : "غیرفعال"}</td>` +
        `<td><input type="checkbox" data-key="${key}" ${cur ? "checked" : ""}></td>`;
    } else {
      const step = (key === "viral_share" || key === "authority") ? "0.05" : "1";
      tr.innerHTML = `<td>${label}</td><td class="num mut">${cur === undefined || cur === null ? "—" : cur}</td>` +
        `<td><input type="number" data-key="${key}" value="${cur === undefined || cur === null ? "" : cur}" step="${step}"></td>`;
    }
    tr.querySelector("input").addEventListener("change", e => {
      const v = e.target.type === "checkbox" ? e.target.checked
        : (e.target.value === "" ? null : parseFloat(e.target.value));
      if (v === cur) delete edits.settings[key]; else edits.settings[key] = v;
      tr.classList.toggle("row-changed", v !== cur);
      showChanges();
    });
    tb.appendChild(tr);
  }
})();

// ── جدول منابع ──
(function() {
  const tb = document.querySelector("#sourcesTable tbody");
  const groups = {};
  for (const s of CONFIG.sources) (groups[s.kind] = groups[s.kind] || []).push(s);
  for (const kind of ["hackernews", "lobsters", "bluesky", "rss"]) {
    if (!groups[kind]) continue;
    for (const s of groups[kind]) {
      const tr = document.createElement("tr");
      let fieldHtml = "<span class='mut'>—</span>";
      const inputs = [];
      for (const f of (CONFIG.kindFields[kind] || [])) {
        if (s[f] === undefined || s[f] === null) continue;
        const step = (f === "authority") ? "0.05" : "1";
        inputs.push(`<div><span class='fldlbl'>${f}</span>` +
          `<input type="number" data-label="${s.label}" data-field="${f}" value="${s[f]}" step="${step}"></div>`);
      }
      if (inputs.length) fieldHtml = inputs.join("");
      tr.innerHTML = `<td class="lbl" title="${s.label}">${s.label}</td>` +
        `<td class="mut">${kind}</td>` +
        `<td class="num">${fieldHtml}</td>` +
        `<td><input type="checkbox" data-label="${s.label}" ${s.commented ? "" : "checked"}></td>`;
      tr.querySelectorAll("input[type=number]").forEach(inp => inp.addEventListener("change", e => {
        const f = e.target.dataset.field, lab = e.target.dataset.label;
        const v = e.target.value === "" ? null : parseFloat(e.target.value);
        edits.sources[lab] = edits.sources[lab] || {};
        if (v === ORIG[lab][f]) delete edits.sources[lab][f]; else edits.sources[lab][f] = v;
        tr.classList.toggle("row-changed", v !== ORIG[lab][f]);
        showChanges();
      }));
      tr.querySelector("input[type=checkbox]").addEventListener("change", e => {
        const lab = e.target.dataset.label;
        edits.sources[lab] = edits.sources[lab] || {};
        if (e.target.checked === ORIG[lab]._enabled) delete edits.sources[lab].enabled;
        else edits.sources[lab].enabled = e.target.checked;
        tr.classList.toggle("row-changed", e.target.checked !== ORIG[lab]._enabled);
        showChanges();
      });
      tb.appendChild(tr);
    }
  }
})();

// ── افزودن منبع ──
const newType = document.getElementById("newType");
newType.addEventListener("change", () => {
  const rss = newType.value === "rss";
  document.getElementById("newUrl").style.display = rss ? "" : "none";
  document.getElementById("newAuthority").style.display = rss ? "" : "none";
  document.getElementById("newHandle").style.display = rss ? "none" : "";
  document.getElementById("newMinEng").style.display = rss ? "none" : "";
});
document.getElementById("addBtn").addEventListener("click", () => {
  const kind = newType.value;
  const label = document.getElementById("newLabel").value.trim();
  if (!label) { alert("label لازم است"); return; }
  const item = { label, kind };
  if (kind === "rss") {
    const url = document.getElementById("newUrl").value.trim();
    if (!url) { alert("url لازم است"); return; }
    item.url = url;
    const a = document.getElementById("newAuthority").value;
    item.authority = a === "" ? 0.7 : parseFloat(a);
  } else {
    const h = document.getElementById("newHandle").value.trim();
    if (!h) { alert("handle لازم است"); return; }
    item.handle = h;
    const m = document.getElementById("newMinEng").value;
    item.min_engagement = m === "" ? 10 : parseInt(m);
  }
  edits.added.push(item);
  ["newUrl","newHandle","newLabel","newAuthority","newMinEng"].forEach(id => document.getElementById(id).value = "");
  showChanges();
});

// ── نمایش تغییرات ──
function changesText() {
  const parts = ["تغییرات config.yaml:"];
  for (const [k, v] of Object.entries(edits.settings)) parts.push("  " + k + ": " + fmtVal(v));
  for (const [label, ch] of Object.entries(edits.sources)) {
    const bits = [];
    for (const [f, v] of Object.entries(ch)) if (f !== "enabled") bits.push(f + ": " + fmtVal(v));
    if (ch.enabled === false) bits.push("غیرفعال");
    if (ch.enabled === true) bits.push("فعال");
    if (bits.length) parts.push("  " + label + ": " + bits.join("، "));
  }
  for (const it of edits.added) parts.push("  + افزودن " + it.label + " (" + it.kind + ")");
  return parts.join("\\n");
}
function showChanges() {
  const el = document.getElementById("changes");
  const parts = [];
  for (const [k, v] of Object.entries(edits.settings)) parts.push(`<b>${k}</b> ← ${v}`);
  for (const [label, ch] of Object.entries(edits.sources)) {
    const bits = [];
    for (const [f, v] of Object.entries(ch)) if (f !== "enabled") bits.push(f + " ← " + v);
    if (ch.enabled === false) bits.push("غیرفعال");
    if (ch.enabled === true) bits.push("فعال");
    if (bits.length) parts.push(`<b>${label}</b>: ${bits.join("، ")}`);
  }
  for (const it of edits.added) parts.push(`+ افزودن <b>${it.label}</b> (${it.kind})`);
  el.innerHTML = parts.length ? "تغییرات: " + parts.join(" · ") : "";
  const has = parts.length > 0;
  document.getElementById("genBtn").disabled = !has;
  document.getElementById("resetBtn").style.display = has ? "" : "none";
  if (!has) {
    document.getElementById("yamlOut").style.display = "none";
    document.getElementById("copyYamlBtn").style.display = "none";
    document.getElementById("copyChangesBtn").style.display = "none";
  }
}

// ── دکمه‌ها ──
document.getElementById("genBtn").addEventListener("click", () => {
  try {
    const out = generateYaml();
    const pre = document.getElementById("yamlOut");
    pre.textContent = out;
    pre.style.display = "block";
    document.getElementById("copyYamlBtn").style.display = "";
    document.getElementById("copyChangesBtn").style.display = "";
    pre.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    alert("تولید YAML شکست خورد: " + e.message);
  }
});
document.getElementById("copyYamlBtn").addEventListener("click", async () => {
  const out = document.getElementById("yamlOut").textContent;
  try { await navigator.clipboard.writeText(out); alert("کپی شد — حالا در GitHub روی config.yaml paste و commit کن."); }
  catch { document.getElementById("yamlOut").select(); document.execCommand("copy"); alert("کپی شد."); }
});
document.getElementById("copyChangesBtn").addEventListener("click", async () => {
  const txt = changesText();
  try { await navigator.clipboard.writeText(txt); alert("فهرست تغییرات کپی شد — برای bot/چت بفرست."); }
  catch { alert(txt); }
});
document.getElementById("resetBtn").addEventListener("click", () => location.reload());
</script>
</body>
</html>"""


def render(rows: list[SourceRow], viral: int, editorial: int, target_share: float,
           checks: list[str], now: datetime, config: dict, config_text: str) -> str:
    total = viral + editorial
    viral_pct = (viral / total * 100) if total else 0

    def fmt_age(dt) -> str:
        if not dt:
            return "—"
        age_h = (now - dt).total_seconds() / 3600
        if age_h < 1:
            return f"{age_h*60:.0f} دقیقه"
        if age_h < 48:
            return f"{age_h:.0f} ساعت"
        return f"{age_h/24:.1f} روز"

    def cfg(r: SourceRow) -> str:
        parts = []
        if r.authority is not None:
            parts.append(f"auth {r.authority:.2f}")
        if r.min_engagement is not None:
            parts.append(f"min_eng {int(r.min_engagement)}")
        if r.min_points is not None:
            parts.append(f"min_pts {int(r.min_points)}")
        return " · ".join(parts) or "—"

    body_rows = []
    for r in rows:
        attempts = r.posts_total + r.skips
        skip_rate = (r.skips / attempts * 100) if attempts else 0
        status = ""
        if r.posts_total == 0:
            status = '<span class="tag tag-silent">خاموش</span>'
        elif skip_rate > 50 and r.skips >= 5:
            status = '<span class="tag tag-skip">skip بالا</span>'
        body_rows.append(
            "<tr>"
            f"<td class='lbl'>{html.escape(r.label)}</td>"
            f"<td><span class='pool pool-{r.pool}'>{r.pool}</span></td>"
            f"<td class='num'>{r.posts_7d}</td>"
            f"<td class='num'>{r.posts_total}</td>"
            f"<td>{fmt_age(r.last_at)}</td>"
            f"<td class='num'>{r.skips} <span class='mut'>({skip_rate:.0f}٪)</span></td>"
            f"<td class='num'>{r.avg_score:.3f}</td>"
            f"<td class='mut'>{html.escape(cfg(r))}</td>"
            f"<td>{status}</td>"
            "</tr>"
        )

    checks_html = "".join(f"<li>{html.escape(c)}</li>" for c in checks) or \
        "<li>✅ نکته‌ی قابل‌اقدامی پیدا نشد.</li>"

    replacements = {
        "__TIMESTAMP__": now.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M"),
        "__N_SOURCES__": str(len(rows)),
        "__N_POSTS__": str(viral + editorial),
        "__N_SILENT__": str(sum(1 for r in rows if r.posts_total == 0)),
        "__VIRAL_PCT__": f"{viral_pct:.0f}",
        "__ED_PCT__": f"{100 - viral_pct:.0f}",
        "__VIRAL_N__": str(viral),
        "__ED_N__": str(editorial),
        "__CHECKS_HTML__": checks_html,
        "__BODY_ROWS__": "".join(body_rows),
        "__CONFIG_JSON__": _payload(config, config_text),
        "__CONFIG_TEXT_JSON__": json.dumps(config_text, ensure_ascii=False),
    }
    out = _TEMPLATE
    for token, value in replacements.items():
        out = out.replace(token, value)
    return out


def generate(state, config: dict, config_text: str, output: Path) -> Path:
    """scorecard.html می‌سازد و برمی‌گرداند."""
    now = datetime.now(timezone.utc)
    rows = build_rows(config, state)
    viral, editorial = pool_share(state)
    target = float(config.get("settings", {}).get("viral_share", 0.5))
    checks = health_checks(rows, viral, editorial, target)
    html_out = render(rows, viral, editorial, target, checks, now, config, config_text)
    output.write_text(html_out, encoding="utf-8")
    return output
