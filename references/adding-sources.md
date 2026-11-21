# Adding a new source to the pipeline

## Checklist (in order)
1. **Fetcher in `src/sources.py`** + registration in the `fetch()` dispatch dict (keyed on `type`). Handler signature `(source: dict, client: httpx.Client) -> list[Item]`; `fetch()` applies the per-source `min_engagement` floor after the handler.
2. **`config.yaml` entry.** `label` is the identity key for velocity baselines, rotation caps, feedback reweighting and the scorecard — pick a stable name and never rename it after posting history exists.
3. **`src/scorecard.py` pool tuple** (`pool="viral" if kind in (...)`): add the new kind if it carries engagement numbers — forgetting it mislabels the pool in the dashboard.
4. **uid namespace**: prefix `<kind>:...` (e.g. `tg:`, `gh:`) — a cross-source uid collision silently dedupes unrelated items forever.
5. **Choose the pool**: engagement numbers (points/stars/reactions) → `ranked=True` → viral pool. Velocity baseline self-calibrates per source (median of that source's OWN run), so a small source can beat HN without magic constants. No numbers → editorial, weight via `authority` + freshness.
6. **`min_engagement` floor** in config when raw numbers are tiny (Lobsters-style) or as a quality gate (GitHub Trending: 150 stars today). It filters in `fetch()` after the handler.
7. **Design `Item.text` deliberately**: the signature is built from its first 2–8 lines. Keep aggregator decorations (score suffixes, star counts) OUT of the first line when the same story also arrives from another source — extra tokens weaken cross-source matching. Store the number in `points`/`engagement` instead.

## Verification (all three, in order)
1. Live fetch test: venv python, call `sources.fetch(source_config, client)`, print uid/engagement/url/text of the first items; assert no unresolved shortlinks remain.
2. `--dry-run --explain --state <copy of real state.json>`: check the collect count for the new label, the cluster log, and that new-source items actually reach the top of the explain table.
3. Cross-source dedup check whenever the new source overlaps an existing one: build the equivalent item from the other source, assert `cluster.url_key` equality, `cluster.build` → one cluster with the intended `lead()`, and `main._drop_repeats` drops the new-source version of an already-posted story.

## t.me public channel (type `telegram`)
Scrapes `https://t.me/s/<handle>` — no bot token needed.
- 20 posts/page. **DOM order is oldest→newest** (Telegram renders it visually reversed): the FIRST `<time>` in the page is the OLDEST message.
- uid: `data-post="handle/<id>"`; published: `<time datetime=...>`.
- title = first `<b>` in the message text; the `(Score: N+ in X hours)` decoration follows OUTSIDE the bold — parse it into `points`/`engagement`, keep it out of `text`.
- `Link:`/`Comments:` are readhacker.news shortlinks (`/s/`, `/c/`). Resolve by following `Location` headers with `follow_redirects=False` (no body download, ≤4 hops, `urljoin` for relative locations): the final URL is valid even when the destination 403/429s; on network failure fall back to the shortlink (signature dedup still catches it).
- Reactions: sum the trailing digits of each `tgme_reaction` span.
- Pagination: the `tme_messages_more` anchor (in the tag, `href` comes BEFORE `class` — regex accordingly); fetch the next page only while the oldest post is still inside `max_age_hours + 2`, cap at 3 pages, dedupe by uid across pages.

## GitHub Trending (type `gittrending`)
- 20 `<article class="Box-row">` blocks. repo = `h2 > a` href; description = `<p class="col-9...">` (the class carries rotating `tmp-` prefix variants — use a loose regex); language = `itemprop="programmingLanguage"`; stars today = `N stars today`; total stars = digit after the `/stargazers` link.
- `engagement` = stars today (counter resets at GitHub midnight; the velocity code clamps negative growth, so no special handling). `published` = fetch time (a trending snapshot is fresh by definition).
- uid `gh:<owner>/<repo>`: a repo can trend for days; once posted it is seen forever — that is the intended one-shot-news semantics, not a bug.
- `points=None`: the 🔥 badge reads "points in X hours", which stars-today is not — a wrong badge is worse than none; the ranking weight comes from engagement anyway.
