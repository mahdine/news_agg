# Competitor notes — HN Telegram channels (idea source for gathering/ranking)

Formats observed in the t.me public previews (~20 recent posts each); verify against the live channels before relying on details. No content curation claims — only pipeline/format facts.

## @hacker_news_feed (30.6K subs, 134K links) — the winning pattern
- HN only, polled every 10 min, gate: **score >= 150**.
- Title badge: `Score: 156+ in 2 hours` — **time-to-threshold (velocity)**; hot emoji when fast, cold emoji when slow, none for medium. The badge is the whole value prop: why this, why now.
- Own shortener (readhacker.news): `/s/<id>` → article, `/c/<id>` → HN thread; gives click analytics per post.
- Post layout: real publisher name (not "HN"), original English title, ~1 auto-scraped excerpt line, two link buttons (Read / N+ Comments). No LLM, no translation — raw + fast + transparent.
- Same author runs a vertical sister channel (designernewsbot, same pipeline, design niche) and a companion browsable directory site built from the same DB.

## @hackernews_summary (171 subs, 40K links) — the cautionary pattern
- Firehose: 1–2 sentence LLM summary of EVERY HN story, no gate. After ~10 years: 171 subs vs 30.6K for the curated feed. Lesson: **curation beats completeness in this niche** — keep the 13-posts/day pace; volume is not the lever, format density is.

## Backlog: improvement ideas (ranked by effort/impact; none implemented yet)
1. HN velocity from Algolia (`points / age_hours` — no extra polling): boost the viral pool and show a badge like «🔥 ۱۵۶ امتیاز در ۲ ساعت» in the post. (low effort / high impact)
2. «N منبع مستقل» badge in the post (`cluster.sources` is already computed) + a second keyboard row «دیدگاه‌ها» from `item.discussion_url`. (very low / medium)
3. `min_viral_engagement` floor (e.g. >=100 points or feed top-10) so cooled-off viral stories drop to editorial instead of posting stale. (very low / medium)
4. Default Persian summary to 3–4 sentences (translation prompt change); keep the long form only for the day's best story. (low / high)
5. Two-tier processing: viral = full pipeline (article scrape + long summary + image); editorial = light (RSS description → short summary, no scrape). (medium / medium)
6. Browsable archive of past posts on the existing Pages deploy, fed by state.json history (the competitor's directory-site analog). (medium / long-term)
- Considered and rejected: own shortener for click tracking — domain + redirect infra, poor value against the existing reaction-feedback loop.
