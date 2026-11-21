# Running, dispatching, verifying

## Manual dispatch
- `POST /repos/mahdine/news_agg/actions/workflows/publish.yml/dispatches` body `{"ref":"main"}` → 204. NOT `POST /repos/.../dispatches` — that is repository_dispatch and 422s without `event_type`. Token: GCM `get` (protocol=https, host=github.com, username from the stored credential).
- Poll `GET /actions/runs?per_page=2` for status/conclusion/head_sha. A full run takes ~1–3 min once a runner is up; runner queue time is unbounded (an hour+ has happened).

## Reading run logs
- `curl -sL -H "Authorization: token <t>" -o <file> https://api.github.com/repos/mahdine/news_agg/actions/runs/<id>/logs` — the URL 302s to a signed download; urllib mangles the signature following the redirect, curl -sL does not.
- **Windows path pitfall**: bash `$TMPDIR`/`/tmp` values are passed to native curl/python untranslated (MSYS conversion is off) → curl exit 23 or the file landing somewhere unexpected. Convert before handing to native tools: `S=$(cygpath -w "$TMPDIR/logs-<id>")` and pass `$S\\file` (or the literal `C:\...` path) to curl/python.
- The zip contains per-step files (`publish/6_run bot.txt` etc.); step 3 is `sync main`.

## Cron schedule (cron-job.org)
- Job expression `23 5-21 * * *` runs in the **Asia/Tehran** timezone: 5:23–21:23 Tehran = 01:53–17:53 UTC. Dispatches therefore land at minute **:53 UTC, not :23**.
- Before declaring the timer broken: fetch `/actions/runs` and check created_at against that pattern (hour range in Tehran, :53 UTC minute). "Missing" :23-UTC dispatches are simply outside the schedule — the false alarm cost a needless manual dispatch.

## Local code tests
- Anything importing `src.main` needs the project venv: `.venv/Scripts/python.exe` from the repo root (the execute_code kernel interpreter lacks project deps such as trafilatura and fails at import).
- Dedup regression pattern: construct `Item`s (src.models), run `cluster.build(...)` then `main._drop_repeats(items, state.recent_stories(now), state.recent_story_url_keys(now))` against a COPY of the real state.json with the suspect story row removed (simulates pre-post state). Assert: (a) same URL with `.html` dropped, (b) same story dropped, (c) unrelated story kept, (d) fuzzy-signature match still works for strong titles. Delete the test script after (repo has no tests/ dir).
- Local dry-run: `.venv/Scripts/python.exe -m src.main --dry-run` — no Telegram calls, state not saved, but it still fetches all feeds (watch for 429s).
- A local dry-run may show 403s on Gemini, hn.algolia.com, or feeds: this machine's residential IP is sometimes blocked (Google answers with a 403 HTML error page, not a JSON API error). Confirm with a raw curl using the key — same failure = network, not code; the Actions network is unaffected, so do not "fix" working code for it.

## Commit discipline for state.json edits
- Before committing any state.json edit, check `/actions/runs` for an in-progress run: its commit step fetches, resets to origin/main, then overwrites `state.json` with state built from ITS checkout — a state edit pushed while a run is in flight gets silently clobbered (same mechanism as the erase in the restore playbook). If a run is in progress, wait for it to finish, then re-apply the edit to the fresh state.
- A local run is a production run: same `.env`, same bot, same channel — test posts go to the owner's private chat, get deleted via deleteMessage after, and are tracked by pid/message_id if they slip into the channel.
