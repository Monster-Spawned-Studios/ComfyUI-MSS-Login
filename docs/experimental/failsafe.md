# Experimental failsafe

## What it does

Tracks repeated experimental-feature failures and can disable experimental flags or trigger recovery notifications (`experimental_recovery` ntfy event). Controlled by `experimental_failsafe` in `config.json` and `MSS_LOGIN_EXPERIMENTAL_FAILSAFE`.

## How to enable / tune

```json
"experimental_failsafe": {
  "enabled": true,
  "escalate_after_repeated_failure": true
}
```

Env: `MSS_LOGIN_EXPERIMENTAL_FAILSAFE=true|false`.

## When it is required

Recommended whenever experimental features are enabled in production so a bad experimental path can auto-disable rather than leave the server in a broken auth state. Failsafe paths preserve user DB credentials and token stores.

## See also

- [Experimental Settings hub](index.md)
