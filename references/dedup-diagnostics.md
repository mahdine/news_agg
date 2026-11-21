# "Same story posted N times" — diagnosis + restore playbook

## Diagnosis (in this order; each root cause is independent — fixing one layer does not close the others)

1. **State diff across the suspect commits.** `git fetch origin && git log --oneline origin/main`, find the state commits bracketing the duplicate posts, `git show <sha>:state.json` for each, and diff `seen` / `stories` / `history` / `posts` between adjacent commits with Python (JSON), not by eye.
   - A post with NO `stories` row → its signature was empty (step 2).
   - The posting run's `seen` gained a uid of a DIFFERENT source than an earlier post of the same article → cross-source dup (uids are source-scoped by design; the cross-run safety net is the story layer, not `seen`).
   - Caps matter: `seen` caps at 3000 and `stories` at 300 — an old uid/row may have been evicted; absence of a row is evidence only within the window.

2. **Reproduce the signature from the REAL article title.** Fetch the article (`curl -sL <url>`, read `<title>`), then run `cluster.signature_of(title)` in the project venv. Titles with <3 surviving tokens (stopword + len<3 + digit filters) yield an empty signature → no dedupe footprint at all. The text signatures are built from differs per source (see SKILL.md "Item text per source") — e.g. HN external links are title-only, so a weak title has no body to fall back on.

3. **Run timing from the Actions logs** (retrieval in running-and-deploying.md). For each run note created_at, started_at, and the checkout "resolved to <sha>" line. A run that queued long (slow runner or concurrency slot) starts on trigger-time state: if the earlier run's post is absent from that state AND step 2 left no footprint, it re-posts. Verify the workflow at that commit has BOTH the concurrency group and the `sync main` step — the group serializes runs but does NOT refresh their state; only the sync does.

4. Only propose fixes after all three; name which bug (empty signature / cross-source uid / stale state) each fix closes.

## Restore an erased post record

A late run's commit step resets to origin/main and overwrites `state.json` with state built on its stale base — records committed in the meantime get erased while the channel message stays alive (message_id gap). Restore by editing the state on origin/main with a NEW pid:

1. `git fetch origin`; base everything on `origin/main` state.json (pull first — the local tree is usually stale; parallel sessions touch this repo).
2. `posts[str(new_pid)] = {label, pool, topics, title[:120], url, at, votes: {}, message_id, chat_id}` — values from the channel post / the earlier run's log.
3. Bump `next_post_id` past the new pid.
4. Re-insert the `history[]` row at its chronological position (title = first sentence of the Persian post; `score: 0.0` when unknown — it then sorts out of the weekly-digest ranking, which is the honest state for a restored record).
5. Add the `stories[]` row the current code would have written (`{sig, at, url: url_key}`) so the 72h dedup window still protects the story against a 4th post.
6. Regenerate the scorecard with the project venv: `scorecard.generate(State(path), config, config_text, ROOT/"scorecard.html")` with config yaml-loaded from config.yaml.
7. Commit + push (summary + approval first, per standing rules), then verify the NEXT run preserved the record: after its state commit lands, re-read `posts` on origin/main.
