---
name: news-agg
description: Use when working on the news_agg PersianTech news bot.
---

# news_agg — PersianTech Telegram news aggregator

Repo: this checkout (github.com/mahdine/news_agg, public; production channel @ai_news_agg via GH Actions secret — @persiantechtwiter retired 2026-11-21). Collects tech/security items (HN Algolia, t.me HN channels, GitHub Trending, Lobsters, Bluesky, ~40 RSS) → dedupes + clusters into "stories" → ranks with a virality algorithm → Gemini free tier summarizes to Persian → posts to Telegram with image/badges/buttons → audience reactions/poll feedback reweights future ranking. No server, no database: committed `state.json` + GitHub Actions + GitHub Pages.

## Module map (src/, ~5200 lines, Python 3.12, no tests/ dir)

| File (lines) | Role |
|---|---|
| `main.py` (~640) | One-run orchestrator: collect → select → publish (incl. `_pretranslate`: full translation + iv page push BEFORE posting, then explicit iv-deploy dispatch + wait for Pages 200, so the IV link in the post body resolves) → digest → save state + scorecard. CLI: `--dry-run --explain --config --state` |
| `sources.py` (550) | Fetchers per `type`, dispatched in `fetch()`; `min_engagement` floor applied after the handler. Six kinds: `hackernews`, `lobsters`, `bluesky`, `telegram` (t.me/s/ preview, no token), `gittrending`, `rss` |
| `cluster.py` (210) | Signatures (latin/numeric tokens of leading lines), `url_key` normalization, within-run clustering (SIMILARITY=0.42), `best_match` cross-run |
| `scoring.py` (285) | Story ranking: corroboration + heat + pickup, gravity decay, velocity vs per-source baseline, echo/diversity/feedback modifiers. All constants documented inline |
| `models.py` (65) | `Item` dataclass — the normalized contract between sources and everything downstream |
| `state.py` (586) | `State`: seen/stories/snapshots/posts/feedback multipliers, caps, load/save |
| `translate.py` (320) | Gemini free-tier model chain: `summarize` (output contract: line 1 = topic keys, line 2 = Persian title, blank, ONE 60–80-word paragraph; or `SKIP`) and `translate_full` (live worker). `GEMINI_HTTP_PROXY` env routes only Gemini through a proxy (Iran geo-block) |
| `article.py` (60) | trafilatura full-article fetch (MIN_USEFUL=400, MAX_CHARS=8000) — feeds the summarizer; title-only input makes the model paraphrase the title |
| `images.py` (190) | Post image: feed-provided → og:image/twitter:image → in-text; real-size check via Pillow (min_side, default 400); never site logos/fallback — no image is cleanest |
| `telegram.py` (~535) | Bot API: `build_message` (incl. `translate_url` → IV link IN THE POST TEXT)/`_badges`/`build_keyboard` (all buttons are URL deep-links, no backend callbacks), `publish` (link-preview image, not sendPhoto — caption cap 1024), `iv_link`/`IV_RHASH` (shared by worker + pipeline), poll, digest, `get_username`/`get_chat_id`. `drain_feedback` is DEAD CODE — the live worker owns getUpdates |
| `live.py` (660) | Always-on worker (Docker in WSL): sole getUpdates consumer; instant full translation (share-dialog fallback, forward, or `/t`; resolves a shared article URL to the posted post's pid; HEAD-short-circuits when the pipeline already pre-deployed the page) + continuous feedback → pushes `live/feedback.json` snapshots |
| `ivpage.py` (64) | Minimal RTL Instant-View HTML from a full translation → `iv/<pid>.html` → GitHub Pages; `PAGES_BASE` constant |
| `scorecard.py` (1040) | Self-contained 3-tab HTML: Scorecard (per-source stats + actionable health checks), Config (line-oriented editor), Feedback (reaction stats + active multipliers) |
| `configedit.py` (250) | Line-oriented config.yaml edits (`set_setting`/`set_source`/`disable_source`/`add_source`) — PyYAML would drop the Persian comments |

## One run, in order (main.main)
1. `.env` → env (local `.env`'s TELEGRAM_CHANNEL is the PRODUCTION channel @ai_news_agg — a local run posts to production; prefer `--dry-run` and be deliberate about real local runs).
2. Load config + `State`; one-time `state.backfill(config)`.
3. If not dry-run: bot username + channel id, then overlay `live/feedback.json` via `State.apply_live_feedback` BEFORE ranking/scorecard (worker is the only update consumer).
4. `collect()`: per-source try/except — one dead source logs and continues.
5. `select()`: fresh filter (`state.has(uid)`, per-item age window, min text length) → `_drop_repeats` (cross-run: exact url_key OR fuzzy ≥ 0.42) → `scoring.rank` (one lead per cluster, `members`/`story_signature` set) → viral/editorial split → `_fill` keeps `viral_share` (0.5) over time via `state.posted`.
6. `publish()` per candidate: pre-publish repeat recheck → `article.fetch_full` (None = no usable article text, e.g. Bluesky/paywall — summary then runs on `item.text` alone) → `translate.summarize` (None = model SKIP → `count_skip` + archive whole cluster; `TranslationError` → NOT archived, retried next run) → `images.resolve` (lazily, only for the posting candidate) → `state.register_post` (pid BEFORE sending — buttons need it) → `_pretranslate` (only when a full article exists: `translate_full` → iv page → commit+push → **`_dispatch_iv_deploy`** (inside Actions a push does NOT wake iv-deploy.yml — GitHub suppresses workflow-originated push events; see Production model) → wait for Pages 200 ≤5 min; ANY failure → `""` = share-fallback button, the post is never blocked or delayed past the fetch) → `telegram.publish` (when a page exists the IV link goes IN THE POST TEXT via `build_message(translate_url=iv_link(page))` and NO «ترجمه کامل» button is added; else the share-dialog button) → `note_message_id` → optional poll → `_archive` + `count_post` + `remember` + `remember_story`.
7. `maybe_digest()`: weekly top-N from `state.week` by score, gated by `last_digest` + weekday + hour.
8. `state.save()` + `scorecard.generate()` — a scorecard failure must never break the run (it's debug tooling).

## Ranking (scoring.py)
`score = (corroboration + heat) × (1 + log10(1 + pickup)) / (age + 2)^1.8`
- **corroboration** = authority × (1 + 1.5 × (n_sources − 1)^0.9) — bonus per EXTRA independent outlet; decay clock starts at `story.latest()` (last coverage), not first — otherwise multi-source clusters die before the 3rd outlet writes.
- **heat** = log10(1 + weighted_engagement) × (1 + log10(1 + velocity/baseline)); WEIGHTS: like 1, comment/reply 2, repost/quote 3. velocity = growth between runs (state.snapshots) or engagement/age for first sight; baseline = median speed of that source's own items (self-calibrating, no magic constants).
- **pickup** = sources / (span + 1) — how fast coverage spread.
- Modifiers in order: `_apply_diversity` (0.55^repeats over `recent_labels`), `_apply_echo` (soft band: best_match vs just-posted stories ≥ ECHO_FLOOR 0.28 → ×0.25 — the hard filter is in `select`), `_apply_feedback` (label mult 0.75–1.25, topic mult = geometric mean over estimated topics, 0.85–1.15; shrinkage keeps small samples ≈ 1).
- `--explain` prints `item.breakdown` (sources/age_h/span_h/corrob/heat/pickup/velocity/echo/diversity/fb_src/fb_topic) — use it to debug "why was this chosen".

## Feedback loop (end-to-end)
Reactions (`message_reaction_count`, absolute per-emoji counts — channels react anonymously; bot must be channel admin) + polls + deep-link votes → **live worker** (sole getUpdates consumer, long-poll) → in-memory state → pushes `live/feedback.json` snapshot (on change, ≤60s; offset + translation cache in /data survive restarts) → hourly run overlays it via `apply_live_feedback` (full-replace is correct: worker is the only writer of these fields) → `state.feedback_multipliers()`/`topic_multipliers()` → reweight next runs. Topic keys come from the summarizer's line 1 (closed vocabulary: categories AI·SECURITY·PROGRAMMING·NETWORK·TECH + ~30 labels; `topics.py` only estimates them at rank time). Stats + live multipliers: scorecard Feedback tab.
Standing design rule: user-facing on-demand features respond within a minute; the hourly cadence is the pipeline, not the interaction layer. Never reintroduce a second getUpdates consumer (two long-poll consumers split updates randomly).

## Dedupe layers (order)
1. `state.has(uid)` — per-source uid, cross-run: `hn:<id>`, `lobsters:<id>`, `at://…` (Bluesky), raw URL (RSS), `tg:<handle>/<id>`, `gh:<owner>/<repo>`. By design does NOT catch the same article across sources.
2. Within-run `cluster.build()` — url_key + signature, SIMILARITY 0.42; lead = best authority/engagement; `members` = all uids of the story.
3. Cross-run: `_drop_repeats` + pre-publish recheck — exact `url_key` match or fuzzy `best_match(item.signature, state.recent_stories()) ≥ 0.42` (72h window; `remember_story` stores a `url` key on EVERY row, even with empty signature).
4. Soft echo band in scoring (below the hard threshold: penalize, don't drop).
Signatures: latin/numeric tokens from line 1 (fallback 2→4→8 lines for weak titles); <3 surviving tokens → EMPTY signature = no dedupe footprint (known failure mode). What `Item.text` actually is per source (verify in the fetcher, don't assume): HN = title only; Lobsters = title + tags; RSS = title + summary; Bluesky = post + embed title/desc; t.me = first `<b>` title (score suffix stripped) + link-preview desc; gittrending = `repo — description (lang) — stars`.

## state.json (~500KB, the memory brain)
`seen[≤3000]` · `stories[≤300: {sig, at, url}]` · `snapshots{uid: [engagement, ts], ≤600}` (velocity memory) · `posted{viral, editorial}` · `recent_labels[5]` · `history[7-day rows]` · `skips{label}` · `posts_by_label{}`/`last_post{}` (long-term per-source stats for scorecard) · `posts{pid → {label, pool, topics, title, url, at, votes{}, message_id, chat_id, poll_id?, reaction_counts?, poll_counts?}, ≤400}` · `next_post_id` · `last_update_id` · `last_digest`.
The production state of record is `origin/main` (state commits by github-actions[bot], `state: <UTC>`); the local tree is usually stale — pull before diffing. Never parse `git show origin/main:state.json` through terminal() (output truncation corrupts JSON) — dump to a file first, `json.loads(strict=False)` (CRLF). Never hand-edit dedupe fields (playbook in references/dedup-diagnostics.md).

## Production model
- `publish.yml`: `workflow_dispatch` only — cron-job.org wakes it hourly (cron `23 5-21 * * *` Asia/Tehran → dispatches land minute **:53 UTC**). concurrency group `state-publisher` + `sync main` step (queued runs must re-sync or they read stale state; the group serializes but does NOT refresh).
- Every run commits `state.json` + `scorecard.html` and deploys the Pages site (index=scorecard, preserves `iv/*.html` — `iv-deploy.yml` mirrors the same staging for worker pushes; change staging in BOTH).
- **A push made from inside a GitHub Actions workflow does NOT trigger other `on: push` workflows** (GitHub suppresses workflow-originated push events, loop prevention; verified 2026-11-22: overnight iv pages pushed from Actions never woke iv-deploy.yml → every post fell back). External pushes (worker container) trigger normally. Hence `_pretranslate` calls `_dispatch_iv_deploy()` — `POST /repos/{o}/{r}/actions/workflows/iv-deploy.yml/dispatches` with GITHUB_TOKEN (needs `actions: write` in publish.yml permissions; iv-deploy.yml needs a `workflow_dispatch:` trigger; outside Actions the token is absent or PAT-without-Actions → it no-ops/silently warns).
- Live worker = separate Docker project in WSL (project dir `~/news-live`; container `newsagg-live`; repo checkout is the /repo volume; its `.env` is root-owned, not readable by the regular user). Deliberately separate from tg-media-bot (own clone/.env/data — never mix their code). Worker pushes ONLY `iv/*.html` + `live/feedback.json`, never state.json (different files = no commit conflict).
- Pushing from this machine needs the GCM-hang workaround (see memory). Dispatch/logs: GitHub API token via git-credential-manager; logs via `curl -sL` (302 to signed S3; urllib mangles the signature). **Workflow dispatch works with the GCM token** (POST `/actions/workflows/{id}/dispatches` → 204); the container's fine-grained PAT is 403 (no Actions permission). **Docker daemon access from Windows: `wsl -u root -e sh -c "docker ..."`** — the default WSL user (mahdi) is NOT in the docker group (socket is root:docker); `wsl -e` runs as mahdi.
- User standing preference: show a summary of code changes and get approval BEFORE committing.

## Instant View (translated full-text pages)
- Page (`ivpage.build_page`): og:title/og:description/og:image + `<img class="hero">`; EVERYTHING (h1, meta line, paragraphs, footer link) lives inside ONE `<article>` — the IV template relies on that exact structure; don't split it.
- **Template**: created at instantview.telegram.org, bound to domain `mahdine.github.io`, v2.1. Rules: `?path: /news_agg/iv/.*.html` · `title: //h1` · `body: //article` · `@remove: //article/h1` · `cover: //img[@class="hero"]` · `image_url` (hero src) · `description` (og:description) · `site_name: "PersianTech"` · `channel: "@ai_news_agg"` (join button).
- **Delivery, two tiers**: (1) PRIMARY one-tap — the pipeline pre-translates BEFORE posting (`main._pretranslate`) and the IV link (`iv_link()`, in telegram.py, shared with the worker) goes IN THE POST TEXT (`build_message(translate_url=...)`, line `📄 <a href="t.me/iv?...">ترجمه‌ی کامل</a>` after the body): one tap, Instant View in-app, no bot, no chat, and no button row for it. **CRITICAL (observed 2026-11-22, post 108): a `t.me/iv` link inside a URL BUTTON opens in the EXTERNAL browser (redirects to the domain) — only links in message text trigger in-app IV.** Never put the IV link back in a button. (2) FALLBACK — posts with no fetchable article body (Bluesky, paywall, fetch failure) get the share-dialog button (`t.me/share/url?url=<article>&text=<prefix + bot>`); the worker resolves the URL to the posted post's pid (so it reuses the pipeline's page via its HEAD check) and translates on demand. (3) Legacy posts (108, 111–115, pre-fix): button points directly at the page URL (one tap, in-app webview) — retrofit script, not reproducible from state: the Bot API has NO endpoint to read an old channel message's text, and the Persian summary is not persisted anywhere, so posted message text is unrecoverable (if retro text edits ever matter, persist the body in state). Worker DONE message shows the ARTICLE TITLE as an HTML hyperlink (`parse_mode=HTML`, `DONE_MSG` = `📄 ترجمه‌ی کامل:\n<a href="{url}">{title}</a>`) with the hidden target `t.me/iv?url=<enc>&rhash=<IV_RHASH>` — tapping opens Instant View in-app, NO Telegram approval needed for our own audience. Standing user rule (2026-11-21): never show a raw t.me/iv URL string in a chat message. `IV_RHASH` is env-overridable and pinned to the template REVISION: editing the template in the editor changes the rhash — take the new one from the editor's "View in Telegram" link and update `IV_RHASH` (or the container env) in the same change.
- **Title contract**: `translate_full` FULL_PROMPT requires line 1 = Persian article title + blank line + body; `translate.split_full_text(raw) -> (title, body)` enforces it (title ≤120 chars, blank line right after — else title="" and worker falls back to the post title). The prompt also drops nav/UI one-word fragments (e.g. «اخبار»، «خدمات» seen on gov.br).
- Editor ops quirks: login session is PER-TAB (sessionStorage) — never open a new tab mid-session; phone login needs the user's in-app tap and the temp_session expires in ~5 min; save via `App.editor` + `saveRules()` (Ctrl+S key dispatch is focus-flaky — call `saveRules()` directly); `?path` regex takes a SINGLE leading slash, no closing slash; the 3-column editor needs a wide viewport (`Emulation.setDeviceMetricsOverride` width ≥1800).

## Hard rules (each has a why)
- **config.yaml is edited line-oriented via `src/configedit.py` — never round-trip through PyYAML** (it silently drops the Persian comments, which ARE the design docs). The scorecard Config tab and any new tooling use the same functions.
- **`label` is the identity key** (velocity baseline, rotation, feedback reweighting, scorecard). Never rename a label after posting history exists.
- **Priority sources**: Hacker News Feed (t.me/hacker_news_feed) authority 0.9 and GitHub Trending 0.85 — keep them ABOVE the rest when tuning (they win lead selection and corroboration).
- **scorecard.html is generated** — on git conflicts rebuild from the remote state.json + regenerate, never merge by hand.
- **Local runs are real bot runs** (live `.env`), and local Gemini from Iran 403s (geo-block, not a code bug) — `GEMINI_HTTP_PROXY` via privoxy → the user's SOCKS5 relay (worker container uses `http://host.docker.internal:8118`). Actions network (US) is unaffected. gemini-2.5-flash is REMOVED (404 since 2026-11); free tier = 20 requests/day PER model, so the config model chain is a budget multiplier — don't collapse it to one model.
- Project is pyright-checked (no config file — run it ad hoc over touched files; fix type complaints, e.g. argv vs args).
- To verify what was posted, read `posts`/`history` in the run's state commit — the Bot API has no channel history.
- Diagnose with the state diff across commits + the dedupe layers above before proposing fixes.

## Adding a feature — where it goes
| Feature | Where |
|---|---|
| New source kind | `sources.py` fetcher + `fetch()` dispatch + config entry + scorecard pool tuple + uid namespace — full checklist & 3-step verification in references/adding-sources.md |
| New setting | `config.yaml` settings (+ a Persian "why" comment) → consume in main/scoring → add to `scorecard.py` `SETTING_FIELDS` so the Config tab can edit it |
| New ranking signal/weight | `scoring.py` (constant + `_score`/modifier + a `breakdown` key so `--explain` shows it) |
| New badge / post line / button | `telegram.py` `_badges`/`build_message`/`build_keyboard` (MAX_TEXT=3500 budget; Persian digits via `_fa`; URL buttons only — no backend callbacks) |
| New feedback signal | `live.py` handler (new update type) + `state.py` record + `live/feedback.json` schema + `apply_live_feedback` overlay + scorecard Feedback tab |
| New on-demand user action | live worker only (must respond <1 min). Primary trigger for «ترجمه کامل» is the ONE-TAP `iv_link` IN THE POST TEXT (pipeline pre-deployed page — never in a button: URL buttons with t.me/iv open externally); the share-dialog fallback (`t.me/share/url?url=<article>&text=<stable prefix + bot username>`) covers posts without a pre-built page — the worker matches the prefix (`TRANSLATE_SHARE_PREFIX`) and extracts the URL, then resolves it to the post's pid. NEVER `?start=<pid>` as a trigger: the payload fires only on FIRST /start, so a returning user's click produces no event at all (proven dead-end 2026-11-21). End every success message with a next-steps line so the chat always closes with fresh guidance |
| Summary/translation behavior | `translate.py` PROMPT — line 1 topic format is a machine contract `_split` depends on; the SKIP list is editorial policy, not noise |
| IV page content | `ivpage.py` — keep minimal RTL (`dir=rtl`, `<article>`, `<h1>`, `<p>`); the IV TEMPLATE (instantview.telegram.org, domain mahdine.github.io) reads `//h1`/`//article`/`img.hero` — see "Instant View" section |
| New diagnostic / dashboard tab | `scorecard.py` — self-contained HTML, zero external deps, generated each run |

## Two locations — keep in sync
This skill lives as (1) the LIVE skill in the Hermes agent's skill directory (machine-local path, recorded only in the live copy) — the copy loaded into agent context — and (2) a committed snapshot at the repo root (`SKILL.md` + `references/`), which is the PORTABLE form: no local absolute paths, usernames, or machine names (use `~/...` or "this checkout" instead). When editing, update both (repo copy in portable form) and commit.

## Local development
- venv: `.venv/Scripts/python.exe` from the repo root (the execute_code kernel python lacks project deps like trafilatura — it fails at import).
- `.venv/Scripts/python.exe -m src.main --dry-run --explain` — no Telegram calls, state not saved, but all feeds ARE fetched (watch for 429s).
- Local Gemini/fetch 403s from this machine's IP are geo/network, not code — confirm with a raw curl before "fixing" working code.
- Regression tests are scratch scripts (no tests/ dir): construct `Item`s, `cluster.build` + `main._drop_repeats` against a COPY of real state.json; delete the script after.
- Untracked working files may exist from parallel sessions (e.g. config_demo.yaml, state_demo.json, state_dbg.json) — not canonical. Parallel sessions share this repo: check `git log`/`status` for commits you didn't make; if local main is AHEAD, `git rebase origin/main` (plain pull --ff-only fails) and flag the unpushed commits to the user.

## References
- `references/adding-sources.md` — 7-step source checklist, t.me public-channel + GitHub Trending scraping techniques, Item.text design rule, 3-step verification.
- `references/running-and-deploying.md` — manual dispatch (workflow_dispatch URL), run-log retrieval (curl -sL + Windows path pitfall), cron timing (:53 UTC), local dry-run/test patterns, state-edit commit discipline.
- `references/dedup-diagnostics.md` — "same story posted N times" diagnosis (state diffs, empty signatures, stale-checkout race) + restoring erased post records.
- `references/live-worker.md` — worker architecture (single-consumer, file-based handoff), translation flow, Pages/Instant View facts, WSL deploy.
- `references/competitor-notes.md` — competitor HN Telegram channels (formats/signals) + ranked backlog of gathering/ranking ideas.
