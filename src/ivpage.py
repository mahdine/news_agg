"""صفحه‌ی متن کامل ترجمه‌شده، در قالبی که Instant View درست بخواند.

worker هر ترجمه را به‌صورت HTML ایستا در پوشه‌ی iv/ مخزن می‌نویسد و push
می‌کند؛ workflow ای-وی-دیپلوی (iv-deploy) آن را روی GitHub Pages می‌نهد.
URL قطعی است، یعنی لینک را می‌شود پیش از کامل‌شدن deploy فرستاد — worker
منتظر ۲۰۰ بودن صفحه می‌ماند و بعد می‌فرستد.

HTML عمداً مینیمال است: پارسر عمومی Instant View با <article> و <p> ساده
بدون template هم درست کار می‌کند. اگر بعداً رندر داخل تلگرام خوب نبود،
template اختصاصی در instantview.telegram.org ساخته می‌شود.
"""

import html
import logging
import re

log = logging.getLogger("ivpage")


def build_page(title: str, label: str, date_str: str, source_url: str, translated: str) -> str:
    """HTML صفحه از ترجمه‌ی فارسی. پاراگراف‌ها از خط‌تک‌های دوگانهٔ متن مدل."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", translated) if p.strip()]
    # بعضی مدل‌ها پاراگراف را با یک خط‌تک جدا می‌کنند؛ آن‌ها را هم بشکن.
    blocks: list[str] = []
    for p in paragraphs:
        blocks.extend(line.strip() for line in p.splitlines() if line.strip())
    body = "\n".join(f"<p>{html.escape(b)}</p>" for b in blocks)

    safe_title = html.escape(title or "متن کامل")
    meta = "  ·  ".join(x for x in [html.escape(label or ""), html.escape(date_str or "")] if x)
    meta_line = f'<p class="meta">{meta}</p>\n' if meta else ""

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<meta property="og:title" content="{safe_title}">
</head>
<body>
<article>
<h1>{safe_title}</h1>
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
