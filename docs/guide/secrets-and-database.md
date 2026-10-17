# Secrets and database

MSS-Login stores credentials in the **owner-chosen users database** (`sqlite` with optional SQLCipher, `postgresql`, or `mysql`), not as plaintext JSON.

## Layers

| Layer | Mechanism | What it protects |
|-------|-----------|------------------|
| **Fernet (field)** | `SECRET_KEY` → HKDF → Fernet | CivitAI/HF API keys, ntfy token, S3 keys saved in UI, TOTP secrets |
| **SQLCipher (file)** | `encryption_level` + Argon2id | Entire SQLite file when `users_db.encryption_level` is set |

Writes that encrypt secrets **round-trip decrypt before persist**. If verify fails, the write is aborted and prior storage is left unchanged.

## What lives where

| Secret | Storage |
|--------|---------|
| CivitAI / HuggingFace API keys | Table `user_model_source_api_keys` (per user, Fernet) |
| ntfy API token | `app_settings` key `ntfy.api_token_encrypted` (Fernet) |
| S3 access/secret (UI-saved) | `app_settings` `s3_storage.*_encrypted` (Fernet) |
| Passwords | bcrypt hashes in `users` |
| API tokens | SHA-256 hashes in `api_tokens` |
| Env overrides (`NTFY_API_KEY`, `S3_*`) | Runtime only; not written back to disk by MSS-Login |

`config.json` may keep non-secret ntfy fields (`topic`, `base_url`, `enabled_events`) with **empty** `api_token`. Legacy plaintext `ntfy.api_token` is migrated into the DB on startup (dual-read until cleared).

## Choosing a backend

Settings → **Users DB** (owner/admin). Restart after changing backend. PostgreSQL/MySQL passwords come from environment only.

## Schema drift CI

`tests/run_schema_drift_tests.py` (wired into `tests/run_ci.py`) compares live SQLite/SQLCipher tables to `utils/db_schema.py` and verifies secret decrypt after reopen.
