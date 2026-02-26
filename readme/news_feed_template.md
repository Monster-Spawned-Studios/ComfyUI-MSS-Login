# News Feed Template (Experimental)

This feature is **experimental**. Enable `experimental_features` in your config (or set `EXPERIMENTAL_FEATURES=1` in the environment) to use the server news feed.

## Setup

1. Copy this file (or create a new file) into your MSS-Login **data directory** as `news.md`.
   - Default data directory: `~/.comfyui-mss-login/`
   - Or the path set by `MSS_LOGIN_DATA_DIR`.
2. Edit `news.md` using the format below.
3. The login page will show a scrolling "Server news" section when the feed is available (experimental features enabled and `news.md` present).

## Format

Each news item is a level-2 heading with a date and optional title, followed by the body until the next heading.

```markdown
## YYYY-MM-DD Optional title here

Body text for this item. You can write multiple lines;
they will be joined into a single paragraph in the feed.

## 2025-02-25 Another update

Second item body.
```

- **Date**: Use `YYYY-MM-DD` (e.g. `2025-02-26`) immediately after `##`. Required.
- **Title**: Everything after the date on the same line is the item title. If omitted, the date is used as the title.
- **Body**: All lines after the heading until the next `##` are the description. Line breaks are collapsed; keep descriptions reasonably short.

## Example

```markdown
## 2025-02-26 Server maintenance

Scheduled maintenance will occur tonight from 22:00 to 24:00 UTC. ComfyUI may be briefly unavailable.

## 2025-02-25 New models added

Checkpoints and LoRAs have been updated. Ask your admin for the full list.
```

Save the file as `news.md` in your data directory. Changes take effect on the next feed request (no restart required).
