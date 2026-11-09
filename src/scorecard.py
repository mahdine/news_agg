"""ساخت scorecard: یک صفحه‌ی HTML استاتیک که سلامتی منابع را نشان می‌دهد.

هر اجرا (غیر از dry-run) این فایل را روی disk می‌نویسد و workflow کنار
state.json همان را commit می‌کند؛ GitHub Pages آن را رایگان سرو می‌کند.
بدون سرور و بدون وابستگی خارجی — HTML کاملاً self-contained است.

این یک داشبورد تنظیم نیست؛ داشبورد تشخیص است. به‌جای اینکه تو حدس بزنی
کدام منبع خوب کار می‌کند، داده می‌دهد و یک فهرست پیشنهادِ قابل‌اجرا روی
`config.yaml` می‌دهد.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# وقت نمایشی صفحه (محاسبات داخلی روی UTC می‌مانند).
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Iran")


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


def render(rows: list[SourceRow], viral: int, editorial: int, target_share: float,
           checks: list[str], now: datetime) -> str:
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

    checks_html = "".join(f"<li>{html.escape(c)}</li>" for c in checks) or "<li>✅ نکته‌ی قابل‌اقدامی پیدا نشد.</li>"

    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scorecard — Persian Tech Bot</title>
<style>
  :root {{
    --bg: #0d1117; --fg: #e6edf3; --mut: #8b949e; --border: #30363d;
    --viral: #f78166; --editorial: #79c0ff; --accent: #58a6ff;
    --good: #3fb950; --warn: #d29922; --bad: #f85149; --card: #161b22;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 20px; background: var(--bg); color: var(--fg);
    font: 15px/1.6 -apple-system, "Segoe UI", Tahoma, sans-serif;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: var(--mut); margin-bottom: 24px; font-size: 13px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .card .n {{ font-size: 26px; font-weight: 700; }}
  .card .l {{ color: var(--mut); font-size: 12px; margin-top: 2px; }}
  .bar {{ height: 8px; border-radius: 4px; overflow: hidden; display: flex; margin-top: 8px; }}
  .bar .v {{ background: var(--viral); }} .bar .e {{ background: var(--editorial); }}
  .legend {{ display: flex; gap: 16px; margin-top: 8px; font-size: 12px; color: var(--mut); }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-left: 5px; vertical-align: middle; }}
  h2 {{ font-size: 16px; margin: 28px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  th {{ color: var(--mut); font-weight: 600; position: sticky; top: 0; background: var(--bg); }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  td.lbl {{ max-width: 220px; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }}
  td.mut, .mut {{ color: var(--mut); }}
  tr:hover td {{ background: #161b22; }}
  .pool {{ font-size: 11px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--border); }}
  .pool-viral {{ color: var(--viral); border-color: var(--viral); }}
  .pool-editorial {{ color: var(--editorial); border-color: var(--editorial); }}
  .tag {{ font-size: 11px; padding: 2px 8px; border-radius: 10px; }}
  .tag-silent {{ background: #388bfd22; color: var(--accent); }}
  .tag-skip {{ background: #d2992222; color: var(--warn); }}
  ul.checks li {{ margin: 8px 0; line-height: 1.7; }}
  .footer {{ color: var(--mut); font-size: 12px; margin-top: 32px; text-align: center; }}
  .tbl-scroll {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>📊 Scorecard</h1>
  <div class="sub">سلامتی منابع و پیشنهادهای تنظیم — به‌روزرسانی {now.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M")} (تهران)</div>

  <div class="cards">
    <div class="card"><div class="n">{len(rows)}</div><div class="l">منبع فعال</div></div>
    <div class="card"><div class="n">{viral + editorial}</div><div class="l">کل پست‌ها</div></div>
    <div class="card"><div class="n">{sum(1 for r in rows if r.posts_total==0)}</div><div class="l">منبع خاموش</div></div>
    <div class="card">
      <div class="n">{viral_pct:.0f}٪ / {(100-viral_pct):.0f}٪</div>
      <div class="l">viral / editorial</div>
      <div class="bar"><div class="v" style="width:{viral_pct:.0f}%"></div><div class="e" style="width:{100-viral_pct:.0f}%"></div></div>
      <div class="legend"><span><span class="dot" style="background:var(--viral)"></span>viral {viral}</span><span><span class="dot" style="background:var(--editorial)"></span>editorial {editorial}</span></div>
    </div>
  </div>

  <h2>پیشنهادها</h2>
  <ul class="checks">{checks_html}</ul>

  <h2>منابع</h2>
  <div class="tbl-scroll">
  <table>
    <thead><tr>
      <th>منبع</th><th>استخر</th><th>۷روز</th><th>کل</th>
      <th>آخرین پست</th><th>skip</th><th>میانگین امتیاز</th><th>تنظیمات</th><th>وضعیت</th>
    </tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
  </div>

  <div class="footer">تولید خودکار توسط bot · scorecard.html کنار state.json commit می‌شود</div>
</div>
</body>
</html>"""


def generate(state, config: dict, output: Path) -> Path:
    """scorecard.html می‌سازد و برمی‌گرداند."""
    now = datetime.now(timezone.utc)
    rows = build_rows(config, state)
    viral, editorial = pool_share(state)
    target = float(config.get("settings", {}).get("viral_share", 0.5))
    checks = health_checks(rows, viral, editorial, target)
    output.write_text(render(rows, viral, editorial, target, checks, now), encoding="utf-8")
    return output
