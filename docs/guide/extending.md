# Extending the node

You can integrate ComfyUI-MSS-Login into your own ComfyUI extensions or external tools in two main ways: **HTTP API** and **Python extension API**.

## HTTP API

The node registers custom routes under prefixes such as `/mss-login`, `/mss_login`, and `/mss-login-gallery`. Use them from:

- **Browser or frontend** (e.g. gallery extensions calling `/mss-login-gallery/mark-nsfw`)
- **Scripts or external apps** (e.g. login, token generation, admin user/group management)

Full list with methods and paths:

- [API Reference: HTTP Endpoints](../api-reference/endpoints.md) — Auth, admin, user, MFA, me, recovery
- [API Reference: Workflow & intercepted](../api-reference/workflow-endpoints.md) — Userdata workflows, `/view`, `/prompt`

Authentication is typically via session cookie (browser) or JWT / API token (Bearer) for programmatic access. Generate long-lived API tokens via `POST /mss-login/generate_token` (see endpoints doc).

## Python extension API (NSFW Guard)

Other ComfyUI custom nodes can use the **NSFW Guard** API to:

- Check if SFW is enforced for a user
- Validate image tensors, PIL Images, or file paths for NSFW content
- Set or clear NSFW tags (e.g. for manual review)
- Set user context in worker threads

Import the public API (when the node is installed as `ComfyUI-MSS-Login` or `mss_login`):

```python
try:
    from ComfyUI_mss_login.api import (
        is_available,
        is_sfw_enforced_for_user,
        check_tensor_nsfw,
        check_image_path_nsfw,
        set_image_nsfw_tag,
        set_user_context,
        get_current_user,
    )
    if not is_available():
        # NSFW guard not loaded
        pass
except ImportError:
    # Extension not installed
    pass
```

Full function list, signatures, and docstrings:

- [Extension API: NSFW Guard API](../extension-api/nsfw-guard-api.md)

## Extension Tabs API (JavaScript)

Other extensions can add custom tabs to the mss_login admin panel. See the project README or `readme/EXTENSION_TABS_API.md` for the JavaScript `window.mss_loginAdminTabs.register(...)` API.

## Summary

| Use case | Where to look |
|----------|----------------|
| Call login, token, admin, user, MFA, recovery endpoints | [API Reference: Endpoints](../api-reference/endpoints.md) |
| Workflow list/save/load/delete, /view NSFW, /prompt user tagging | [Workflow & intercepted](../api-reference/workflow-endpoints.md) |
| Use NSFW checks or tagging from Python (custom nodes) | [NSFW Guard API](../extension-api/nsfw-guard-api.md) |
| Add a tab to the mss_login settings modal | README / EXTENSION_TABS_API.md |
