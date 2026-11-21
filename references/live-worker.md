# Live worker — instant full translation + continuous feedback

The always-on half of the runtime: a Docker container in WSL owning user-facing interaction and feedback collection. The hourly Actions run stays the pipeline (fetch/rank/translate/post/state commit).

## Why this shape (hard-to-reverse decisions)
- **Single update consumer.** Two long-poll consumers on one bot split updates randomly — each update is delivered to exactly ONE consumer. The worker consumes ALL update types; the Actions run must NOT call getUpdates (`drain_feedback` was removed from main.py). Missing reactions/votes ⇒ suspect a second poller, not Telegram.
- **File-based handoff, not shared state.** The worker never writes state.json. It pushes only `iv/*.html` and `live/feedback.json` (fetch → `reset --hard origin/main` → re-copy file → commit → push, ×3 retry). Different files = no commit conflict with the hourly state commit.
- **Trigger = forward to the bot.** Deep-link URL buttons are unreliable: the /start payload fires only on FIRST start — a returning user's click generates no event at all. A forward always delivers a message carrying `forward_from_message_id` (+ `forward_from_chat.id` to verify it's our channel). `/t <message_id|url>` is the explicit fallback.
- **Instant UX is a requirement.** Any user-tap feature must respond within a minute; the hourly cadence applies only to the pipeline.

## Translation flow (src/live.py → translate_full → ivpage → Pages)
1. Forward or /t → job thread with its OWN httpx.Client (httpx is not thread-safe; the poll loop must not block on the job either).
2. Resolve: message_id → `State.find_pid_by_message` → post {url,title,label,at}; a /t URL skips state.
3. `article.fetch` (trafilatura, ≤8000 chars) → None means paywall/JS challenge → honest failure message + original link.
4. `translate.translate_full` — full-translation prompt, same free-tier model chain as summarize (20 RPD per model; 429 = exhausted). Cache in /data/translated.json keyed by pid+url so a re-forward doesn't burn quota.
5. `ivpage.build_page` — minimal RTL HTML (`dir="rtl"`, `<article>`, `<h1>`, `<p>` per paragraph, escaped; original link in footer) so Telegram's GENERIC Instant View parser works without a template.
6. Push `iv/<pid>.html` to main → `iv-deploy.yml` (on: push, paths iv/**) deploys the Pages site.
7. Worker HEAD-polls the page URL until 200 (≤4 min) and DMs the link. URL is deterministic: `https://mahdine.github.io/news_agg/iv/<pid>.html`.

## Pages facts
- Repo is PUBLIC; Pages project page `mahdine.github.io/news_agg` (already live for the scorecard).
- BOTH workflows deploy the same site structure (index.html = scorecard + iv/*.html) — change staging in both or the hourly deploy drops the article pages.
- Instant View only renders for domains with an IV template (Telegram-maintained + some community). Most of this feed's domains (news.ycombinator.com, github.com, 404media.co, bsky.app, cnews.ru/vc.ru/mail.ru) have none — IV cannot be the reading path for ORIGINAL articles; it is a delivery format only for pages WE host (own domain ⇒ generic parser or own template).
- If in-app rendering is bad: author a template at instantview.telegram.org for mahdine.github.io.

## Feedback handoff
- Worker records reactions (message_reaction + message_reaction_count), /start votes, poll answers/closings, callback votes in an in-memory State; every ≥60s when dirty it pushes `live/feedback.json` ({last_update_id, posts: {pid: {votes, reaction_counts, poll_counts}}}).
- Hourly run: `State.apply_live_feedback(ROOT/"live"/"feedback.json")` in main.py BEFORE scoring + scorecard (feedback_reweight consumes the votes). Overwrite semantics are safe: the worker is the sole writer of those fields and builds from the latest state.json (on refresh it adopts only NEW pids, keeps its own feedback records).
- Offset lives in /data/offset in the container, seeded from state.last_update_id on first start. Telegram holds undelivered updates ~24h, so a stopped worker catches up on boot (cutover double-processing is idempotent; worst case a duplicate /start confirmation).

## Deploy (WSL; separate from tg-media-bot — standing user rule)
- Home: `~/news-live` (WSL home) — own clone, own .env, container `newsagg-live`, data in ./data. Never mix code/env/containers with tg-media-bot.
- .env: production TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL + GEMINI_API_KEY (from GH Secrets — the local .env key is stale/invalid) + GITHUB_TOKEN = fine-grained PAT on news_agg (Contents: read+write), used only for the worker's pushes.
- First time: `bash docker/bootstrap.sh` (clones repo, sets git identity) then `docker compose -f docker/docker-compose.yml up -d --build live`. Later: `git pull && docker compose ... up -d --build live`, then verify the deployed code (grep a new string in /app) — same discipline as tg-media-bot.
- Dockerfile carries `git config --system --add safe.directory /repo` (container git runs as root over a user-owned volume).

## Testing
- Units: build_page HTML escaping/structure, apply_live_feedback overlay, worker init — run with the repo's .venv python (the Hermes kernel python lacks trafilatura).
- Gemini diagnostics: 403 with an HTML body = invalid key (valid keys start `AIza...`; a Firebase-style `AQ.Ab8...` token fails) — NOT quota; 429 = free-tier quota. The local .env points at the old test channel and its GEMINI key is invalid ⇒ test translation paths with production creds in the deployed container.
- Live smoke (before asking the user to test): drive the deployed worker's own handler with a synthetic forward update (real message_id from state) end-to-end — real fetch, real Gemini, real git push, real Pages deploy, real sendMessage to the owner's chat (97114822); then the user forwards one real post and checks the link opens as Instant View in-app.
