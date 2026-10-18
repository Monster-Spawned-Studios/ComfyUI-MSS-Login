# Project status

## 2026-10-17

- **Symptom:** Every live `/api/history` entry had `status == {"completed": None, "messages": []}` — no `status_str`, no `execution_*` message timestamps, and no usable error/cancel flag for `jobs.normalize_history_item`.
- **Root cause:** MSS-Login’s `user_queue_task_done` override ignored ComfyUI core’s `status=ExecutionStatus(...)` and `process_item=` (v0.37.4); it only read legacy `completed=` / `messages=` kwargs that core never passes.
- **Fix:** Forward `status` (NamedTuple `_asdict` or mapping) and apply `process_item` to the core prompt tuple before 5-element normalization; keep legacy kwargs fallback and `**kwargs` forward-compat; stamp `user_id` as before.
- **Tests:** `tests/run_history_status_tests.py` (+ pytest wrapper) covers success/error/interrupted, positional status, unknown kwargs, legacy fallback, regression, history_result merge, and per-user filtering.
- **Unchanged:** Per-user history filtering in `user_queue_get_history` / `user_queue_wipe_history` (non-admins see only their own entries; admin/owner see all).
- **Shared guest workflows:** Portal/CPE and web userdata now merge **own + `workflows/guest`** (configurable `workflows.shared_user`) with precedence own > guest > optional legacy globals (`workflows.include_legacy_globals`, default false). Shared/legacy entries are labelled (`shared`/`source`/`readonly`/`format`); non-owners (and guest by default) cannot write/delete shared files (`SHARED_READONLY`); owner uses `scope=shared`. Web GET/save/delete accept safe relative subfolder paths; `_archive*` trees are skipped. Grant checks fail closed for shared/legacy origins; own-file grant check stays fail-open on unexpected errors. Web list hides API-format by default (`workflows.web_hide_api_format`).
