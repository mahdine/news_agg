"""ویرایش line-oriented برای config.yaml.

PyYAML کامنت‌های فارسی فایل را دور می‌ریزد و ما نمی‌خواهیم — این فایل پر از
توضیح «چرا» است. پس تغییرات را *روی متن اصلی* اعمال می‌کنیم: فقط value‌های
تغییرکرده جایگزین می‌شوند و بقیه‌ی خطوط (شامل کامنت‌ها) دست‌نخورده می‌مانند.

سه نوع تغییر:
  set_setting  — یک key داخل بلوک settings
  set_source   — یک field داخل بلوک یک منبع (authority, min_engagement, ...)
  disable      — کامنت‌کردن کل بلوک یک منبع (یا برعکسش)
  add_source   — افزودن بلوک منبع جدید به انتهای فایل

هر تابع (text, patch) -> (new_text, diff_summary) برمی‌گرداند.
"""

from __future__ import annotations

import re


class ConfigEditError(ValueError):
    """تغییری که روی متن فعلی قابل اعمال نیست (فایل عوض شده)."""


# ─────────────────────────────────────────────────────── بلوک‌یابی

def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _strip_comment(line: str) -> str:
    """متن YAML خط، بدون کامنتِ دنباله‌ای. (کامنت داخل string را نادیده می‌گیریم؛
    در این config مقدارهای string فقط url و label هستند و # در آن‌ها نیست.)"""
    out = []
    in_str = None
    for i, ch in enumerate(line):
        if in_str:
            out.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
            out.append(ch)
        elif ch == "#":
            # کامنت YAML باید بعد از whitespace یا ابتدای خط باشد.
            if i == 0 or line[i - 1] in " \t":
                break
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _uncomment(line: str) -> str:
    """متن مؤثر خط: اگر کامنت‌شده باشد، بدون علامت # (با حفظ indent)."""
    if _is_comment(line):
        return re.sub(r"^(\s*)#\s?", r"\1", line)
    return line


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def find_setting_block(text: str, key: str) -> tuple[int, int]:
    """خط‌های بلوک settings که key را نگه می‌دارند: (index_value_line, indent).

    بلوک settings با `settings:` در column 0 شروع می‌شود؛ key‌های مستقیمش
    indent ۲ دارند (بیشتر از settings: و کمتر از زیرمجموعه‌ها).
    """
    lines = text.splitlines()
    s = None
    for i, raw in enumerate(lines):
        if raw.rstrip() == "settings:":
            s = i
            break
    if s is None:
        raise ConfigEditError("بلوک settings پیدا نشد")
    key_re = re.compile(rf"^{key}\s*:")
    for i in range(s + 1, len(lines)):
        raw = lines[i]
        if not raw.strip():
            continue
        if _is_comment(raw):
            continue
        stripped = _strip_comment(raw).strip()
        if _indent(raw) <= 0:
            break  # خارج از بلوک
        if key_re.match(stripped) and _indent(raw) == 2:
            return i, _indent(raw)
    raise ConfigEditError(f"setting پیدا نشد: {key}")


def find_source_block(text: str, label: str) -> tuple[int, int, list[int], bool]:
    """بلوک یک منبع (فعال یا کامنت‌شده): (start, indent, [خطوط], commented).

    بلوک منبع با `  - type:` در indent ۲ شروع می‌شود. متن مؤثر هر خط را می‌خوانیم
    (اگر کامنت باشد بدون #) تا منابع غیرفعال هم پیدا شوند.
    """
    lines = text.splitlines()
    label_norm = label.strip().lower()
    start = None
    for i, raw in enumerate(lines):
        eff = _uncomment(raw).strip()
        if not eff.startswith("- ") or "- type:" not in eff:
            continue
        block = _list_item_lines(lines, i)
        ident = _item_identity(lines, block)
        if ident and ident.strip().lower() == label_norm:
            start = i
            break
    if start is None:
        raise ConfigEditError(f"منبع پیدا نشد: {label}")
    # بلوک غیرفعال است فقط وقتی خطِ `  - type:` آن کامنت شده باشد. کامنت‌های
    # توضیحی داخل بلوک (مثل بلوک Lobsters) وضعیت فعال/غیرفعال را نمی‌گویند.
    return start, 2, _list_item_lines(lines, start), _is_comment(lines[start])


def _list_item_lines(lines: list[str], start: int) -> list[int]:
    """همه‌ی خطوط یک list-item YAML (فعال یا کامنت‌شده).

    بلوک با اولین خط خالی تمام می‌شود؛ با خط بعدی که آیتم list است (فعال یا
    کامنت‌شده) تمام می‌شود؛ یا با خطی که indent مؤثرش کمتر از ۲ است. خط خالی
    هیچ‌وقت *داخل* بلوک منبع نیست، پس کامنت‌های بین منابع و بلوک‌های کامنت‌شده‌ی
    بعدی به بلوکِ قبلی چسبیده نمی‌شوند.
    """
    out = [start]
    for i in range(start + 1, len(lines)):
        raw = lines[i]
        if not raw.strip():
            break
        eff = _uncomment(raw).strip()
        if eff.startswith("- "):
            break  # آیتم بعدی list (فعال یا کامنت‌شده)
        if _indent(_uncomment(raw)) < 2:
            break
        out.append(i)
    return out


def _item_identity(lines: list[str], block_lines: list[int]) -> str | None:
    """label یک بلوک منبع را برمی‌گرداند (اگر نبود، handle)."""
    for want in ("label", "handle"):
        for i in block_lines:
            raw = _strip_comment(_uncomment(lines[i]))
            m = re.match(rf"^{want}\s*:\s*(.+)$", raw.strip())
            if m:
                return m.group(1).strip().strip("\"'")
    return None


def _key_in_block(lines: list[str], block_lines: list[int], key: str) -> int | None:
    for i in block_lines:
        s = _strip_comment(_uncomment(lines[i])).strip()
        if re.match(rf"^{key}\s*:", s):
            return i
    return None


# ─────────────────────────────────────────────────────── اعمال تغییرات

def set_setting(text: str, key: str, value) -> tuple[str, str]:
    idx, indent = find_setting_block(text, key)
    lines = text.splitlines()
    prefix = " " * indent
    new_val = _format(value)
    old_line = lines[idx]
    lines[idx] = f"{prefix}{key}: {new_val}"
    return "\n".join(lines), f"{key}: {_format_value_of(old_line)} → {new_val}"


def set_source(text: str, label: str, key: str, value) -> tuple[str, str]:
    start, _, block, commented = find_source_block(text, label)
    lines = text.splitlines()
    idx = _key_in_block(lines, block, key)
    if idx is None:
        # field تازه — به انتهای بلوک اضافه می‌شود (با indentِ fieldهای بلوک).
        # indent fieldها = indentِ خط `  - ` + ۲.
        first = _strip_comment(_uncomment(lines[block[0]]))
        field_indent = (len(first) - len(first.lstrip())) + 2
        new_line = " " * field_indent + f"{key}: {_format(value)}"
        # بعد از آخرین خط غیرکامنت بلوک بگذاریم.
        last = max(i for i in block if _strip_comment(_uncomment(lines[i])).strip())
        lines.insert(last + 1, new_line)
        if commented:
            raise ConfigEditError(f"منبع {label} غیرفعال است؛ اول فعالش کن")
        return "\n".join(lines), f"{label}.{key}: ← {_format(value)}"
    # اگر خط کامنت‌شده باشد، اول کامنت را بردار، بعد value را بگذار.
    old = lines[idx]
    base = _uncomment(old)
    lines[idx] = re.sub(rf"(?m)^(?P<sp>\s*{key}\s*:\s*).*", rf"\g<sp>{_format(value)}", base)
    return "\n".join(lines), f"{label}.{key}: {_format_value_of(base)} → {_format(value)}"


def disable_source(text: str, label: str, enable: bool = False) -> tuple[str, str]:
    """کامنت‌کردن (یا بازکردن) کل بلوک یک منبع."""
    start, _, block, commented = find_source_block(text, label)
    lines = text.splitlines()
    action = "بازکردن" if enable else "غیرفعال‌کردن"
    # فقط خط‌هایی که قبلا‌ *کد* بوده‌اند را باز می‌کنیم — یک بلوک منبع ممکن است
    # کامنتِ توضیحی داشته باشد که نباید دست بخورد. خط کدِ کامنت‌شده یا
    # `# - ...` (آیتم list) است یا `# key:` با یکی از فیلدهای شناخته‌شده.
    # `\s+` لازم است: بلوک‌هایی که از اول در فایل کامنت بوده‌اند indent فیلدها
    # را بعد از # حفظ می‌کنند (مثل `#   url:`) و کامنتِ توضیحی فارسی با فیلد
    # لاتین نمی‌شود شروع شود.
    code_re = re.compile(
        r"^(\s*)#\s+(?:- .+|(?:type|label|handle|url|authority|"
        r"min_engagement|min_points|max_age_hours)\s*:)"
    )
    for i in block:
        if _is_comment(lines[i]):
            if enable and code_re.match(lines[i]):
                lines[i] = re.sub(r"^(\s*)#\s?", r"\1", lines[i])
        else:
            if not enable:
                lines[i] = re.sub(r"^(\s*)", r"\1# ", lines[i])
    return "\n".join(lines), f"{action} منبع {label}"


def add_source(text: str, source_type: str, fields: dict) -> tuple[str, str]:
    """بلوک منبع جدید به انتهای فایل اضافه می‌کند. fields: {label: ..., ...}"""
    lines = text.rstrip("\n").splitlines()
    # بلوک را از آخر به اول بچینیم تا ترتیب fields حفظ شود.
    body = [f"  - type: {source_type}"]
    for k, v in fields.items():
        if k == "type":
            continue
        body.append(f"    {k}: {_format(v)}")
    lines.extend(["", "  # ── منبع جدید (از طریق صفحه‌ی تنظیمات) ──"])
    lines.extend(body)
    name = fields.get("label", source_type)
    return "\n".join(lines) + "\n", f"افزودن منبع {name}"


def _format(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value == int(value):
            return f"{value:.2f}"
        return str(value)
    if isinstance(value, int):
        return str(value)
    s = str(value).strip()
    if re.search(r"[:#\[\]{},&*!|>'\"%@`]", s) or s == "":
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _format_value_of(line: str) -> str:
    m = re.search(r":\s*(.*)$", _strip_comment(line).strip())
    return (m.group(1).strip() if m else "")
