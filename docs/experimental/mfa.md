# MFA (experimental)

## What it does

Enables TOTP multi-factor authentication and backup codes for users. Login may return `mfa_required` until a code is verified.

## How to enable

- Master: `EXPERIMENTAL_FEATURES=true`
- Feature: `experimental.mfa: true` or `EXPERIMENTAL_MFA=1`
- Optional global kill: `MFA_DISABLED=true` disables MFA even when the feature flag is on

## When it is required

Required when you need second-factor login for admin/owner accounts or any role with `mfa_required` in group config. Not required for API-token-only clients that already hold a long-lived token (tokens are created after MFA when applicable).

## See also

- [Authentication](../guide/authentication.md)
- [Experimental Settings hub](index.md)
