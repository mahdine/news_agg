"""صفحه‌ی متن کامل ترجمه‌شده، در قالبی که Instant View درست بخواند.

worker هر ترجمه را به‌صورت HTML ایستا در پوشه‌ی iv/ مخزن می‌نویسد و push
می‌کند؛ workflow ای-وی-دیپلوی (iv-deploy) آن را روی GitHub Pages می‌نهد.
URL قطعی است، یعنی لینک را می‌شود پیش از کامل‌شدن deploy فرستاد — worker
منتظر ۲۰۰ بودن صفحه می‌ماند و بعد می‌فرستد.

برای اینکه پارسر عمومی Instant View (و پیش‌نمایش لینک تلگرام) صفحه را
«مقاله» بشناسد، سه چیز لازم است که اینجا می‌گذاریم:
  1. <title> و og:title با تیتر واقعی مقاله (نه «متن کامل»)
  2. og:description از اولین پاراگراف
  3. og:image = تصویر کاور مقاله (og:image سایت اصلی) + <img> در بدنه

اگر رندر داخل تلگرام هنوز خوب نبود، template اختصاصی در
instantview.telegram.org ساخته می‌شود (قالب و تصویر خودش را می‌گذارد).
"""

import html
import logging
import os
import re

log = logging.getLogger("ivpage")

# ریشه‌ی سایت GitHub Pages — worker و خط پست‌گذاری هر دو همین را می‌سازند.
PAGES_BASE = os.environ.get("PAGES_BASE_URL", "https://mahdine.github.io/news_agg")

# چند ثانیه‌ی صبر پس از اولین 200، پیش از ارسال پیامی که لینک IV دارد.
# تلگرام صلاحت IV را در لحظه‌ی ارسال پیام تعیین و cache می‌کند؛ edge‌ای که
# سرورهای تلگرام صفحه را از آن fetch می‌کنند دیرتر از runner پر می‌شود.
# سوابق (2026-11-22): 0.4s (پست‌های 116/119/120) ❌، 45s (122) ❌، 180s
# (تست 999 با URL تازه و صفحه‌ی گرم) ❌، اما صفحه‌هایی که ۶-۲۰ دقیقه پیش از
# اولین نمایش لینک live بودند (تست‌های A–G و کپی‌های دستی) ✅. پس لینکِ
# t.me/iv نباید پیش از آنکه صفحه همین مدت warm باشد در هیچ پیامی دیده شود.
IV_PAGE_WARM_SECONDS = int(os.environ.get("IV_PAGE_WARM_SECONDS", "600"))

_STYLE = """<style>
body{margin:0;background:#fff;color:#111;font-family:-apple-system,'Segoe UI',Tahoma,'Vazirmatn',sans-serif;line-height:1.9;font-size:17px}
article{max-width:680px;margin:0 auto;padding:24px 16px 48px}
.hero{width:100%;height:auto;border-radius:12px;margin:0 0 18px;display:block}
h1{font-size:24px;line-height:1.5;margin:0 0 6px}
p{margin:0 0 14px}
.meta{color:#777;font-size:14px}
.footer{color:#999;font-size:14px;border-top:1px solid #eee;margin-top:28px;padding-top:14px}
a{color:#3466c4;text-decoration:none}
</style>"""


def build_page(title: str, label: str, date_str: str, source_url: str,
               translated: str, image_url: str = "") -> str:
    """HTML صفحه از ترجمه‌ی فارسی. پاراگراف‌ها از خط‌تک‌های دوگانهٔ متن مدل."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", translated) if p.strip()]
    # بعضی مدل‌ها پاراگراف را با یک خط‌تک جدا می‌کنند؛ آن‌ها را هم بشکن.
    blocks: list[str] = []
    for p in paragraphs:
        blocks.extend(line.strip() for line in p.splitlines() if line.strip())
    body = "\n".join(f"<p>{html.escape(b)}</p>" for b in blocks)

    safe_title = html.escape(title or "متن کامل")
    # og:description از محتوای واقعی مقاله می‌آید، نه تیتر تکراری.
    first_para = blocks[0] if blocks else ""
    desc = first_para[:150] + ("…" if len(first_para) > 150 else "")
    safe_desc = html.escape(desc, quote=True)

    meta = "  ·  ".join(x for x in [html.escape(label or ""), html.escape(date_str or "")] if x)
    meta_line = f'<p class="meta">{meta}</p>\n' if meta else ""
    hero = (
        f'<img class="hero" src="{html.escape(image_url, quote=True)}" alt="" loading="lazy">\n'
        if image_url.startswith("http") else ""
    )
    og_image = (
        f'<meta property="og:image" content="{html.escape(image_url, quote=True)}">\n'
        if image_url.startswith("http") else ""
    )
    tw_image = (
        f'<meta name="twitter:image" content="{html.escape(image_url, quote=True)}">\n'
        if image_url.startswith("http") else ""
    )

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<meta name="description" content="{safe_desc}">
<meta property="og:type" content="article">
<meta property="og:title" content="{safe_title}">
<meta property="og:description" content="{safe_desc}">
<meta property="og:url" content="{html.escape(source_url, quote=True)}">
<meta property="og:site_name" content="PersianTech">
{og_image}<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{safe_title}">
<meta name="twitter:description" content="{safe_desc}">
{tw_image}{_STYLE}
</head>
<body>
<article>
{hero}<h1>{safe_title}</h1>
{meta_line}{body}
<p class="footer">ترجمه‌ی کامل خودکار — <a href="{html.escape(source_url, quote=True)}">مقاله‌ی اصلی</a></p>
</article>
</body>
</html>
"""


def page_filename(key: str) -> str:
    """pid یا کلید دلخواه → نام فایل امن (فقط حرف/عدد/خط تیره)."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", key)[:48].strip("-")
    return f"{safe or 'page'}.html"


def page_url(base: str, key: str) -> str:
    base = (base or "").rstrip("/")
    return f"{base}/iv/{page_filename(key)}"
