# NSFW Guard API

Public Python API for other ComfyUI extensions to use NSFW detection and enforcement.

## Import

```python
try:
    from ComfyUI_mss_login.api import is_available, check_tensor_nsfw, ...
except ImportError:
    from mss-login.api import is_available, check_tensor_nsfw, ...
```

Always check `is_available()` before using other functions if the extension might be optional.

---

## Functions

### `is_available`

```python
is_available()
```

Check if the NSFW guard API is available.

Returns:
    bool: True if the NSFW guard is available, False otherwise

---

### `is_sfw_enforced_for_user`

```python
is_sfw_enforced_for_user(username=None)
```

Check if SFW (Safe For Work) restrictions are enforced for a user.

Args:
    username: Optional username to check. If None, checks the current session user.

Returns:
    bool: True if SFW is enforced (user should be blocked from NSFW),
          False if user is allowed to view NSFW content.

Note:
    Guest users always have SFW enforced (returns True) regardless of database settings.

Example:
    if is_sfw_enforced_for_user("john"):
        # User 'john' has SFW restrictions
        pass

---

### `check_tensor_nsfw`

```python
check_tensor_nsfw(images_tensor, threshold=0.5)
```

Check if an image tensor contains NSFW content.

Args:
    images_tensor: PyTorch tensor containing image data (shape: [batch, channels, height, width])
    threshold: Confidence threshold for NSFW detection (default: 0.5)

Returns:
    bool: True if NSFW content is detected above threshold, False otherwise.
          Returns False if SFW is not enforced for the current user.

Example:
    if check_tensor_nsfw(image_tensor):
        # Replace with black image or block
        image_tensor = torch.zeros_like(image_tensor)

---

### `check_image_path_nsfw`

```python
check_image_path_nsfw(image_path, username=None)
```

Check if an image file contains NSFW content.

Args:
    image_path: Path to the image file
    username: Optional username to check permissions for. If None, uses current session.

Returns:
    bool: True if image should be blocked (NSFW detected and user has restrictions),
          False otherwise.

Note:
    Guest users always have their images checked, regardless of session state.

Example:
    if check_image_path_nsfw("/output/image.png", "john"):
        # Block access to this image
        return web.Response(status=403, text="NSFW Blocked")

---

### `check_image_path_nsfw_fast`

```python
check_image_path_nsfw_fast(image_path, username=None)
```

Fast tag-only check for NSFW content. Only checks cache, never scans.
Use this for bulk operations where you want instant results.

Args:
    image_path: Path to the image file
    username: Optional username to check permissions for. If None, uses current session.

Returns:
    bool: True if NSFW (block), False if safe (allow), None if not tagged yet (needs scan)

Example:
    result = check_image_path_nsfw_fast("/output/image.png")
    if result is None:
        # Not tagged yet, do full scan or allow
        result = check_image_path_nsfw("/output/image.png")
    if result:
        # Block the image
        pass

---

### `check_pil_image_nsfw`

```python
check_pil_image_nsfw(image, threshold=0.5)
```

Check if a PIL Image contains NSFW content.

Args:
    image: PIL Image object
    threshold: Confidence threshold for NSFW detection (default: 0.5)

Returns:
    bool: True if NSFW content is detected above threshold, False otherwise.
          Returns False if SFW is not enforced for the current user.

Example:
    pil_image = Image.open("image.png")
    if check_pil_image_nsfw(pil_image):
        # Block or replace image
        pass

---

### `set_user_context`

```python
set_user_context(username)
```

Set the user context for the current execution thread.

This is useful when you need to set the user context in a worker thread
where the HTTP request context is not available.

Args:
    username: Username to set as the current context, or None for guest

Example:
    # In a worker thread
    set_user_context("john")
    # Now NSFW checks will use "john" as the user

---

### `get_current_user`

```python
get_current_user()
```

Get the current user from the context.

Returns:
    Optional[str]: Current username, or None if not set

Example:
    username = get_current_user()
    if username:
        print(f"Current user: {username}")

---

### `clear_image_nsfw_tag`

```python
clear_image_nsfw_tag(image_path)
```

Clear NSFW tag for an image, forcing rescan on next check.

Args:
    image_path: Path to the image file

---

### `clear_all_nsfw_tags`

```python
clear_all_nsfw_tags()
```

Clear all NSFW tags, forcing rescan of all images.

---

### `set_image_nsfw_tag`

```python
set_image_nsfw_tag(image_path, is_nsfw, score=1.0, label='manual')
```

Manually set NSFW tag on an image (for manual review/flagging).

This function allows extensions to manually flag images as NSFW or SFW,
bypassing automatic detection. Useful for gallery review workflows.

Args:
    image_path: Path to the image file
    is_nsfw: True to mark as NSFW, False to mark as SFW
    score: Confidence score (default: 1.0 for manual flags)
    label: Detection label (default: "manual" to indicate manual flagging)

Returns:
    bool: True if successful, False otherwise

Example:
    # Flag an image as NSFW
    set_image_nsfw_tag("/output/image.png", is_nsfw=True)

    # Mark an image as safe
    set_image_nsfw_tag("/output/image.png", is_nsfw=False)

---


*Generated by `scripts/build_docs.py`.*
