# commit-multi-changes examples

Example plans an agent might present after Stage 0. Adapt paths and messages to the real diff.

## Example 1 — Login UI + update notice + CI ntfy (~800 lines)

Working tree mixes CSS/JS login fixes, updater API, permission defaults, tests, docs, and workflow ntfy jobs.

**Proposed plan (await approval):**

1. **fix(auth): keep Remember Me compact; drop duplicate local-net copy**
   - `web/css/styles.css`, `web/js/auth.js`
   - Why: unlayered CSS was stretching the checkbox; JS duplicated trusted-network text

2. **feat(updater): public update notice, apply API, and can_update_mss_login**
   - `utils/updater.py`, `utils/model_visibility_policy.py`, `utils/bootstrap.py`, `users/defaults/default_group_config.json`, `routes/admin.py`, `routes/user.py`, `web/mss_login_settings.js`
   - Why: login/loading need redacted status and permission-gated apply

3. **feat(auth-ui): login and loading update banners**
   - `web/html/login.html`, `web/js/auth.js` (notice block only if not already in #1), `web/html/loading.html`, `web/js/loading.js`, `routes/auth.py` (loading docstring)
   - Why: themed notice + session-aware Update now without touching ComfyUI splash

4. **test(updater): cover notice redaction and update permission**
   - `tests/run_update_notice_tests.py`, `tests/run_ci.py`, `tests/README.md`
   - Why: CI coverage for public payload and permission defaults

5. **docs: loading-screen interstitial and update endpoints**
   - `docs/experimental/loading-screen.md`, `docs/api-reference/endpoints.md`
   - Why: document pre-ComfyUI handoff and new routes

6. **ci: ntfy notifications on GitHub and Gitea workflows**
   - `.github/workflows/*.yml` (except reusable), `.gitea/workflows/*.yml` (except reusable)
   - Why: admins get run/fail alerts via existing reusable ntfy action

After approval: commit 1 → 6 in order; then `git status` and list SHAs. Do not push.

## Example 2 — CI-only + docs under 500 lines but multi-topic

~320 lines across workflows and one docs page. Still multi-topic → two stages (not one dump).

**Proposed plan:**

1. **ci: add ntfy final job to code-quality and docs**
   - `.github/workflows/code-quality.yml`, `.github/workflows/docs.yml`, `.gitea/workflows/code-quality.yml`

2. **docs: note ntfy workflow notifications for admins**
   - Relevant guide page only

If the same change were a single workflow tweak (~40 lines), offer one commit instead of multi-stage.
