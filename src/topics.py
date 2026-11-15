"""تخمین موضوع خبر از روی متن انگلیسی — فقط برای رتبه‌بندی.

موضوعات نهایی هر خبر (برای جمع‌بندی بازخورد) را مدل هنگام خلاصه‌سازی می‌دهد
(translate.py)، ولی آن لحظه بعد از انتخاب است. برای اعمال ضریب موضوع در
*زمان رتبه‌بندی*، متن کاندیدا هنوز خلاصه نشده، پس این‌جا با واژگان بسته‌ی
همان برچسب‌ها و چند کلیدواژه‌ی لاتین تخمین می‌زنیم.

این تخمین prior ضعیفی است نه برچسب نهایی: محدوده‌ی ضریب موضوع باریک است
(±۱۵٪) و فقط موضوعاتی اثر می‌گذارند که رأی کافی جمع کرده‌اند. خطای
طبقه‌بندی هزینه‌ی اندک‌تری نسبت به فایده‌ی «موضوعی که مخاطب آن را دوست ندارد
جلو نرود» دارد.
"""

import re

# کلیدواژه‌های هر موضوع. همه‌ی کوچک‌تر؛ تطبیق با مرز کلمه.
_KEYWORDS: dict[str, list[str]] = {
    # دسته‌ها
    "AI": [
        "ai", "artificial intelligence", "machine learning", "deep learning",
        "neural", "llm", "language model", "gpt", "chatgpt", "openai",
        "claude", "gemini", "anthropic", "copilot", "diffusion",
    ],
    "SECURITY": [
        "security", "hacker", "hacking", "malware", "ransomware", "exploit",
        "vulnerability", "breach", "phishing", "cyber", "zero-day", "0day",
        "malicious", "attackers", "hacked",
    ],
    "PROGRAMMING": [
        "programming", "code", "compiler", "framework", "library", "sdk",
        "api", "developer", "programming language", "type system", "runtime",
    ],
    "NETWORK": [
        "network", "internet", "router", "dns", "tcp", "bandwidth", "isp",
        "fiber", "wifi", "5g", "6g", "cdn", "protocol", "http", "ipv6",
    ],
    "TECH": [
        "tech", "smartphone", "gadget", "device", "app", "software",
        "product", "release",
    ],
    # برچسب‌های مشخص (همان EXTRA در translate.py)
    "LINUX": ["linux"],
    "WINDOWS": ["windows"],
    "ANDROID": ["android"],
    "APPLE": ["apple", "iphone", "ipad", "macos"],
    "GOOGLE": ["google", "chrome", "gmail", "youtube"],
    "MICROSOFT": ["microsoft", "azure"],
    "OPENSOURCE": ["open source", "open-source"],
    "PYTHON": ["python"],
    "JAVASCRIPT": ["javascript", "typescript", "node.js", "nodejs"],
    "RUST": ["rust"],
    "MALWARE": ["malware", "trojan", "spyware", "rootkit"],
    "RANSOMWARE": ["ransomware"],
    "BREACH": ["breach", "leaked", "data leak", "exfiltrat"],
    "VULNERABILITY": ["vulnerability", "cve", "zero-day", "0day", "patch"],
    "PHISHING": ["phishing"],
    "CRYPTO": ["crypto", "bitcoin", "ethereum", "blockchain", "defi"],
    "CLOUD": ["cloud", "aws", "gcp"],
    "DATABASE": ["database", "sql", "postgres", "mysql", "mongodb", "redis"],
    "MOBILE": ["mobile", "smartphone"],
    "HARDWARE": ["hardware", "cpu", "gpu", "processor", "motherboard"],
    "CHIP": ["chip", "tsmc", "semiconductor", "nvidia", "gpu"],
    "STARTUP": ["startup", "venture capital", "funding", "acquisition", "ipo"],
    "PRIVACY": ["privacy", "tracking", "gdpr", "data protection"],
    "LLM": ["llm", "language model", "gpt", "chatgpt", "claude", "gemini",
            "mistral", "llama"],
    "CHATBOT": ["chatbot"],
    "BROWSER": ["browser", "firefox", "safari", "chrome"],
    "GAMEDEV": ["game", "gaming", "unity", "unreal engine"],
    "DEVOPS": ["devops", "kubernetes", "docker", "ci/cd"],
    "CENSORSHIP": ["censorship", "censor", "filtering"],
    "SHUTDOWN": ["internet shutdown", "shutdown", "outage", "blackout"],
    "IRAN": ["iran", "iranian"],
    "VPN": ["vpn"],
    "SURVEILLANCE": ["surveillance", "monitoring"],
}

# regex هر بار build می‌شود ولی واژه‌ها ثابت‌اند؛ یک بار بساز و نگه دار.
_PATTERNS: dict[str, re.Pattern] = {
    topic: re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")\b")
    for topic, kws in _KEYWORDS.items()
}


def estimate(text: str) -> set[str]:
    """موضوعاتی که کلیدواژه‌شان در متن دیده می‌شود. ممکن است خالی باشد."""
    if not text:
        return set()
    low = text.lower()
    return {topic for topic, pat in _PATTERNS.items() if pat.search(low)}
