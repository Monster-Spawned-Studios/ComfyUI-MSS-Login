#!/usr/bin/env python3
"""Apply pull-request labels on Gitea from an actions/labeler-compatible config.

GitHub's actions/labeler talks to the GitHub Issues API and does not label
Gitea PRs. This script reads the same YAML shape (.gitea/labeler.yml), lists
changed files via the Gitea API, then adds matching labels through Gitea's
issue/PR label endpoints.

Environment (Gitea Actions / local dry-run):
  GITEA_TOKEN or GITHUB_TOKEN  — API token with issues write
  GITHUB_API_URL               — e.g. https://gitea.example/api/v1
  GITHUB_SERVER_URL            — fallback base if API URL is unset
  GITHUB_REPOSITORY            — owner/repo
  GITHUB_EVENT_PATH            — path to the webhook/event JSON
  LABELER_CONFIG               — path to labeler YAML (default .gitea/labeler.yml)
  PR_NUMBER                    — optional override when event has no PR
  DRY_RUN=1                    — print matches only; do not mutate labels
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

try:
	import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML
	print("PyYAML is required: python3 -m pip install pyyaml", file=sys.stderr)
	sys.exit(1)


def _api_base() -> str:
	api = (os.environ.get("GITHUB_API_URL") or "").rstrip("/")
	if api:
		return api
	server = (os.environ.get("GITHUB_SERVER_URL") or "").rstrip("/")
	if server:
		return f"{server}/api/v1"
	print("GITHUB_API_URL or GITHUB_SERVER_URL is required", file=sys.stderr)
	sys.exit(1)


def _token() -> str:
	token = os.environ.get("GITEA_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
	if not token:
		print("GITEA_TOKEN or GITHUB_TOKEN is required", file=sys.stderr)
		sys.exit(1)
	return token


def _repo() -> tuple[str, str]:
	repo = os.environ.get("GITHUB_REPOSITORY") or ""
	if "/" not in repo:
		print("GITHUB_REPOSITORY must be owner/repo", file=sys.stderr)
		sys.exit(1)
	owner, name = repo.split("/", 1)
	return owner, name


def _pr_number() -> int:
	override = os.environ.get("PR_NUMBER") or ""
	if override.strip().isdigit():
		return int(override.strip())
	path = os.environ.get("GITHUB_EVENT_PATH") or ""
	if not path or not os.path.isfile(path):
		print("GITHUB_EVENT_PATH missing and PR_NUMBER unset", file=sys.stderr)
		sys.exit(1)
	with open(path, encoding="utf-8") as fh:
		event = json.load(fh)
	pr = event.get("pull_request") or {}
	number = pr.get("number") or event.get("number")
	if not number:
		print("Event payload has no pull request number", file=sys.stderr)
		sys.exit(1)
	return int(number)


def _request(method: str, url: str, token: str, body: dict | None = None) -> object:
	data = None
	headers = {
		"Authorization": f"token {token}",
		"Accept": "application/json",
		"User-Agent": "mss-login-gitea-label-pr",
	}
	if body is not None:
		data = json.dumps(body).encode("utf-8")
		headers["Content-Type"] = "application/json"
	req = urllib.request.Request(url, data=data, headers=headers, method=method)
	try:
		with urllib.request.urlopen(req, timeout=60) as resp:
			raw = resp.read()
			if not raw:
				return None
			return json.loads(raw.decode("utf-8"))
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc


def _paginate(url: str, token: str) -> list:
	items: list = []
	parsed = urllib.parse.urlparse(url)
	query = dict(urllib.parse.parse_qsl(parsed.query))
	page = int(query.get("page") or "1")
	limit = int(query.get("limit") or "100")
	while True:
		query["page"] = str(page)
		query["limit"] = str(limit)
		page_url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
		batch = _request("GET", page_url, token)
		if not isinstance(batch, list) or not batch:
			break
		items.extend(batch)
		if len(batch) < limit:
			break
		page += 1
	return items


def _path_matches(path: str, pattern: str) -> bool:
	"""Match a repo-relative path against an actions/labeler-style glob."""
	normalized = path.replace("\\", "/")
	while normalized.startswith("./"):
		normalized = normalized[2:]
	pat = pattern.replace("\\", "/")
	try:
		if PurePosixPath(normalized).full_match(pat):
			return True
	except ValueError:
		pass
	# Fallback for patterns Path.full_match rejects or edge cases.
	regex = "^"
	i = 0
	while i < len(pat):
		if pat.startswith("**/", i):
			regex += "(?:.*/)?"
			i += 3
		elif pat.startswith("**", i):
			regex += ".*"
			i += 2
		elif pat[i] == "*":
			regex += "[^/]*"
			i += 1
		elif pat[i] == "?":
			regex += "[^/]"
			i += 1
		else:
			regex += re.escape(pat[i])
			i += 1
	regex += "$"
	return re.match(regex, normalized) is not None


def _extract_globs(matcher: object) -> list[str]:
	globs: list[str] = []
	if isinstance(matcher, str):
		return [matcher]
	if isinstance(matcher, list):
		for item in matcher:
			globs.extend(_extract_globs(item))
		return globs
	if not isinstance(matcher, dict):
		return globs
	changed = matcher.get("changed-files")
	if isinstance(changed, list):
		for entry in changed:
			if isinstance(entry, dict):
				for key in (
					"any-glob-to-any-file",
					"any-glob-to-all-files",
					"all-globs-to-any-file",
					"all-globs-to-all-files",
				):
					value = entry.get(key)
					if isinstance(value, str):
						globs.append(value)
					elif isinstance(value, list):
						globs.extend(str(v) for v in value)
			elif isinstance(entry, str):
				globs.append(entry)
	for key in ("any-glob-to-any-file", "any"):
		value = matcher.get(key)
		if isinstance(value, str):
			globs.append(value)
		elif isinstance(value, list):
			globs.extend(str(v) for v in value)
	return globs


def _labels_from_config(config: dict, changed_files: list[str]) -> list[str]:
	matched: list[str] = []
	for label, rules in config.items():
		if not isinstance(label, str) or label.startswith("."):
			continue
		globs = _extract_globs(rules)
		if not globs:
			continue
		if any(_path_matches(path, glob) for path in changed_files for glob in globs):
			matched.append(label)
	return matched


def _ensure_label_ids(
	api: str, owner: str, repo: str, token: str, names: list[str], dry_run: bool
) -> list[int]:
	existing = _paginate(
		f"{api}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/labels", token
	)
	by_name = {
		str(item.get("name") or "").lower(): int(item["id"])
		for item in existing
		if isinstance(item, dict) and item.get("id") is not None
	}
	ids: list[int] = []
	for name in names:
		key = name.lower()
		if key in by_name:
			ids.append(by_name[key])
			continue
		if dry_run:
			print(f"DRY_RUN: would create label {name!r}")
			continue
		created = _request(
			"POST",
			f"{api}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/labels",
			token,
			{"name": name, "color": "#ededed", "description": f"Auto: {name}"},
		)
		if not isinstance(created, dict) or created.get("id") is None:
			raise RuntimeError(f"Failed to create label {name!r}: {created!r}")
		label_id = int(created["id"])
		by_name[key] = label_id
		ids.append(label_id)
		print(f"Created label {name!r} (id={label_id})")
	return ids


def main() -> int:
	dry_run = os.environ.get("DRY_RUN", "").strip() in {"1", "true", "yes"}
	config_path = os.environ.get("LABELER_CONFIG") or ".gitea/labeler.yml"
	if not os.path.isfile(config_path):
		print(f"Labeler config not found: {config_path}", file=sys.stderr)
		return 1

	with open(config_path, encoding="utf-8") as fh:
		config = yaml.safe_load(fh) or {}
	if not isinstance(config, dict):
		print("Labeler config must be a mapping of label -> rules", file=sys.stderr)
		return 1

	api = _api_base()
	token = _token()
	owner, repo = _repo()
	pr = _pr_number()
	print(f"Labeling {owner}/{repo}#{pr} via {api}")

	files = _paginate(
		f"{api}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/pulls/{pr}/files",
		token,
	)
	changed = []
	for item in files:
		if not isinstance(item, dict):
			continue
		for key in ("filename", "name", "path"):
			value = item.get(key)
			if value:
				changed.append(str(value).replace("\\", "/"))
				break
	print(f"Changed files ({len(changed)}):")
	for path in changed:
		print(f"  - {path}")

	wanted = _labels_from_config(config, changed)
	if not wanted:
		print("No labels matched changed files.")
		return 0
	print(f"Matched labels: {', '.join(wanted)}")

	current = _paginate(
		f"{api}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/issues/{pr}/labels",
		token,
	)
	current_names = {
		str(item.get("name") or "").lower() for item in current if isinstance(item, dict)
	}
	to_add = [name for name in wanted if name.lower() not in current_names]
	if not to_add:
		print("All matched labels already present.")
		return 0

	label_ids = _ensure_label_ids(api, owner, repo, token, to_add, dry_run)
	if dry_run:
		print(f"DRY_RUN: would add labels {to_add} (ids={label_ids})")
		return 0
	if not label_ids:
		print("No label IDs resolved; nothing to add.")
		return 1

	_request(
		"POST",
		f"{api}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/issues/{pr}/labels",
		token,
		{"labels": label_ids},
	)
	print(f"Added labels: {', '.join(to_add)}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
