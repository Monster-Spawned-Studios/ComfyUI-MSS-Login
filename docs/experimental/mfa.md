# MFA (experimental)

## What it does

Enables TOTP multi-factor authentication and recovery codes for users. Login may return `mfa_required` until a code is verified. Setup is blocked once MFA is enabled (authenticator change requires re-auth). TOTP replay within the ±1 step window is rejected.

## How to enable

- Master: `EXPERIMENTAL_FEATURES=true`
- Feature: `experimental.mfa: true` or `EXPERIMENTAL_MFA=1`
- Optional emergency kill: `MFA_DISABLED=true` skips MFA for **non-admin/non-owner** users only (logged + ntfy). Admins/owners still require MFA unless `MFA_DISABLED_FORCE_ALL=true` (or config `mfa_disabled_force_all`).

## When it is required

Required for admin/owner accounts and any group with `mfa_required` in group config (checked across **all** of a user's groups). Local and identity-header login never satisfy MFA. Creating API tokens requires MFA enrollment for those roles. Existing long-lived API tokens remain valid until revoked; minting new ones is MFA-gated.

Live config changes (`mfa_disabled`, `mfa_disabled_force_all`, group `mfa_required`) are the operator's call — this doc does not change production config.

## See also

- [Authentication](../guide/authentication.md)
- [Experimental Settings hub](index.md)
