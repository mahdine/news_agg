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
- **iv-deploy race (fixed 2026-11-21):** deploy-pages@v4 queries the artifact index ~0.3–0.6s after upload finalization; sometimes the artifact isn't indexed yet → "No artifacts named 'github-pages' were found" even though the upload succeeded (artifact is visible via API seconds later). `iv-deploy.yml` has a `sleep 20` between upload-pages-artifact and deploy-pages — don't remove it. The hourly publish.yml runs without the sleep and has not hit it, but shares the same latent race.
- Instant View only renders for domains with an IV template (Telegram-maintained + some community). Most of this feed's domains (news.ycombinator.com, github.com, 404media.co, bsky.app, cnews.ru/vc.ru/mail.ru) have none — IV cannot be the reading path for ORIGINAL articles; it is a delivery format only for pages WE host (own domain ⇒ generic parser or own template).
- If in-app rendering is bad: author a template at instantview.telegram.org for mahdine.github.io.

## Feedback handoff
- Worker records reactions (message_reaction + message_reaction_count), /start votes, poll answers/closings, callback votes in an in-memory State; every ≥60s when dirty it pushes `live/feedback.json` ({last_update_id, posts: {pid: {votes, reaction_counts, poll_counts}}}).
- Hourly run: `State.apply_live_feedback(ROOT/"live"/"feedback.json")` in main.py BEFORE scoring + scorecard (feedback_reweight consumes the votes). Overwrite semantics are safe: the worker is the sole writer of those fields and builds from the latest state.json (on refresh it adopts only NEW pids, keeps its own feedback records).
- Offset lives in /data/offset in the container, seeded from state.last_update_id on first start. Telegram holds undelivered updates ~24h, so a stopped worker catches up on boot (cutover double-processing is idempotent; worst case a duplicate /start confirmation).

## Pitfalls (learned the hard way)
- **getUpdates offset = last_id + 1 (Telegram semantics).** The worker's /data/offset stores the NEXT offset; state.json `last_update_id` keeps the legacy "last processed id" semantics (snapshot writes `offset - 1`, startup seed reads `last_update_id + 1`). Dropping the +1 makes Telegram re-deliver the LAST update forever → the worker reprocesses it on every poll → the same reply sent in a tight loop (2026-11-21 spam incident: one forwarded post = endless identical messages until the container was killed). Reactions hid it because re-recording is idempotent and silent; any message-type update (forward, /start, /t) makes it visible.
- **Logging is invisible in spam diagnosis:** the forward/rejection paths log nothing on success, and SIGKILL loses unflushed stdout — diagnose from the offset file + git log + API, not the logs.

## Deploy (WSL; separate from tg-media-bot — standing user rule)
- Home: `~/news-live` (WSL home) — own clone, own .env, container `newsagg-live`, data in ./data. Never mix code/env/containers with tg-media-bot.
- .env: production TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL=@ai_news_agg (the PRODUCTION channel — @persiantechtwiter retired 2026-11-21; a worker pointing at the wrong channel rejects every legitimate forward with NOT_OUR_CHANNEL) + GEMINI_API_KEY (from GH Secrets — the local .env key is stale/invalid) + GITHUB_TOKEN = fine-grained PAT on news_agg (Contents: read+write), used only for the worker's pushes. NOTE: the PAT has NO Actions permission (workflow rerun API → 403); trigger workflow re-runs by pushing a change instead.
- First time: `bash docker/bootstrap.sh` (clones repo, sets git identity) then `docker compose -f docker/docker-compose.yml up -d --build live`. Later code changes: `git pull` in the repo volume only — the Dockerfile has NO COPY of source (WORKDIR /repo), so the container runs the volume's code; no rebuild needed.
- **`docker compose restart` does NOT reload .env** (env_file loads at container creation) — after editing .env use `docker compose up -d --force-recreate live`.
- Privoxy on the WSL host bridges the container to the user's SOCKS5 relay: `listen-address 0.0.0.0:8118` (NOT 127.0.0.1 — the container reaches it via host.docker.internal = 172.17.0.1) and `forward-socks5 / proxy.shenakhtic.ir:8124 .` — the 3-argument syntax is the only form privoxy accepts (2- or 4-arg lines are silently ignored → traffic goes DIRECT and 403s from Iran). Enabled via systemd. `GEMINI_HTTP_PROXY=http://host.docker.internal:8118` routes ONLY Gemini through it.
- Docker Hub pulls also 403 from Iran: daemon proxy via systemd drop-in `/etc/systemd/system/docker.service.d/proxy.conf` (HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:8118) — affects `docker pull`/build only, not container runtime traffic.
- **git push from the container uses GIT_ASKPASS, not token-in-URL** (`src/live.py::_git_push`): git 2.47's URL parser rejects long tokens (≥~90 chars) embedded in `https://user:token@…` with "URL rejected: Port number was not a decimal number" (reproduced with a dummy token of the same length — it's a parser bug, not the token). The ASKPASS temp script (chmod 700, deleted after) keeps the token out of the URL.
- Dockerfile carries `git config --system --add safe.directory /repo` (container git runs as root over a user-owned volume).
- WSL repo sync: the outer `~/news-live` clone AND the inner `~/news-live/repo` (the /repo volume) both need `git pull`/`fetch + reset --hard origin/main` — the worker pushes from the inner repo. Uncommitted local edits in the outer clone block `git pull` (stash → pull → the upstream often already contains the fix from a parallel session).

## Testing
- Units: build_page HTML escaping/structure, apply_live_feedback overlay, worker init — run with the repo's .venv python (the Hermes kernel python lacks trafilatura).
- Gemini diagnostics: 403 with an HTML body = invalid key (valid keys start `AIza...`; a Firebase-style `AQ.Ab8...` token fails) — NOT quota; 429 = free-tier quota. The local .env points at the old test channel and its GEMINI key is invalid ⇒ test translation paths with production creds in the deployed container.
- Live smoke (before asking the user to test): drive the deployed worker's own handler with a synthetic forward update (real message_id from state) end-to-end — real fetch, real Gemini, real git push, real Pages deploy, real sendMessage to the owner's chat (97114822); then the user forwards one real post and checks the link opens as Instant View in-app.
