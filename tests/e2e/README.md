# MFA / Passkey Playwright E2E (lab only)

**Never** point these tests at the live ComfyUI / Cloudflare tunnel.

## Lab setup

1. Run a disposable ComfyUI+MSS-Login container with HTTPS on `https://localhost` (self-signed or mkcert).
2. Set `webauthn.rp_id` / `origins` for that lab host in a **lab** config copy.
3. Install Playwright Chromium (optional group `cpe-convert` or `npx playwright install chromium`).

## Virtual authenticator

Use Chrome DevTools Protocol `WebAuthn.enable` + `WebAuthn.addVirtualAuthenticator` (ctap2, hasResidentKey, hasUserVerification).

Example (sketch — fill lab URL / credentials locally; do not commit secrets):

```js
// tests/e2e/passkeys.spec.js — run only against https://localhost lab
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  const client = await context.newCDPSession(page);
  await client.send("WebAuthn.enable");
  await client.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
    },
  });
  // Login → /passkeys → register → 2FA login → passwordless → delete
  await browser.close();
})();
```

Cover: registration, 2FA login, passwordless login (policy on), recovery codes, passkey deletion.
