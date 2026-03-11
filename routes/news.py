# --- START OF FILE routes/news.py ---
"""
Experimental news feed: serve RSS generated from a markdown file in the data directory.
Gated by experimental_features; returns 404 when disabled or when news.md is missing.
"""

import html
import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime

from aiohttp import web

from ..constants import get_host_base_url, experimental_news_enabled
from ..globals import routes
from ..utils.data_dir import get_data_dir


def _xml_escape(text: str) -> str:
	"""Escape string for safe use in RSS/XML text nodes and attributes."""
	if not text:
		return ""
	return html.escape(text, quote=True)


def _parse_news_md(content: str) -> list[tuple[str, str, str]]:
	"""
	Parse markdown content into (date_str, title, body) tuples.
	Format: ## YYYY-MM-DD Optional title
	then body until the next ## or end of file.
	"""
	items = []
	pattern = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*(.*)$", re.MULTILINE)
	pos = 0
	while True:
		m = pattern.search(content, pos)
		if not m:
			break
		date_str = m.group(1).strip()
		title = m.group(2).strip() or date_str
		start = m.end()
		next_m = pattern.search(content, start)
		body = content[start : next_m.start()].strip() if next_m else content[start:].strip()
		body = re.sub(r"\n+", " ", body)[:2000]  # single line, limit length
		items.append((date_str, title, body))
		pos = next_m.start() if next_m else len(content)
	return items


def _format_rfc822_date(date_str: str) -> str:
	"""Validate YYYY-MM-DD and return RFC 822 formatted date, or empty string on error."""
	try:
		dt = datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")
		dt = dt.replace(tzinfo=timezone.utc)
		return format_datetime(dt, usegmt=True)
	except (ValueError, TypeError):
		return ""


def _build_rss(items: list[tuple[str, str, str]], base_url: str = "") -> str:
	"""Build RSS 2.0 XML with sanitized title, description, and pubDate."""
	channel_title = _xml_escape("Server News")
	channel_link = _xml_escape(base_url or "/")
	channel_desc = _xml_escape("ComfyUI server news and announcements.")

	parts = [
		'<?xml version="1.0" encoding="UTF-8"?>',
		'<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
		"<channel>",
		f"  <title>{channel_title}</title>",
		f"  <link>{channel_link}</link>",
		f"  <description>{channel_desc}</description>",
	]
	for date_str, title, body in items:
		pub_date = _format_rfc822_date(date_str)
		if not pub_date:
			continue
		safe_title = _xml_escape(title)
		safe_desc = _xml_escape(body)
		parts.append("  <item>")
		parts.append(f"    <title>{safe_title}</title>")
		parts.append(f"    <pubDate>{pub_date}</pubDate>")
		parts.append(f"    <description>{safe_desc}</description>")
		parts.append("  </item>")
	parts.append("</channel>")
	parts.append("</rss>")
	return "\n".join(parts)


@routes.get("/mss-login/api/news/feed.xml")
async def get_news_feed(request: web.Request) -> web.Response:
	"""
	Serve RSS feed generated from data-dir news.md (experimental).
	Returns 404 when experimental_features is off or news.md is missing.
	"""
	if not experimental_news_enabled():
		return web.Response(status=404)

	data_dir = get_data_dir()
	news_path = os.path.join(data_dir, "news.md")
	if not os.path.isfile(news_path):
		return web.Response(status=404)

	try:
		with open(news_path, "r", encoding="utf-8") as f:
			content = f.read()
	except OSError:
		return web.Response(status=404)

	items = _parse_news_md(content)
	if not items:
		return web.Response(status=404)

	base_url = get_host_base_url() or (request.url.origin if request.url else "") or ""
	rss_xml = _build_rss(items, base_url)
	return web.Response(
		text=rss_xml,
		content_type="application/rss+xml",
		charset="utf-8",
	)


routes.get("/api/mss-login/api/news/feed.xml")(get_news_feed)
