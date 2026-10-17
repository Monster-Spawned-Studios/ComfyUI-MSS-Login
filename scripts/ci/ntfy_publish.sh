#!/usr/bin/env bash
# Publish an ntfy notification, optionally attaching a local file (PUT + Filename).
#
# Required env:
#   NTFY_URL, NTFY_TOPIC, TITLE, MESSAGE
# Optional env:
#   NTFY_TOKEN, PRIORITY (default 3), TAGS, REF_NAME,
#   ATTACHMENT_PATH, ATTACHMENT_FILENAME, SKIP_IF_UNCONFIGURED (default true)
set -euo pipefail

SKIP_IF_UNCONFIGURED="${SKIP_IF_UNCONFIGURED:-true}"
PRIORITY="${PRIORITY:-3}"
TAGS="${TAGS:-}"
REF_NAME="${REF_NAME:-}"
ATTACHMENT_PATH="${ATTACHMENT_PATH:-}"
ATTACHMENT_FILENAME="${ATTACHMENT_FILENAME:-}"

if [ -z "${NTFY_URL:-}" ] || [ -z "${NTFY_TOPIC:-}" ]; then
	if [ "${SKIP_IF_UNCONFIGURED}" = "true" ]; then
		echo "NTFY_URL/NTFY_TOPIC not configured; skipping notification."
		exit 0
	fi
	echo "NTFY_URL and NTFY_TOPIC are required." >&2
	exit 1
fi

if [ -z "${TITLE:-}" ] || [ -z "${MESSAGE:-}" ]; then
	echo "TITLE and MESSAGE are required." >&2
	exit 1
fi

finished=$(date '+%m/%d/%Y %I:%M %p')
body="${MESSAGE}"
if [ -n "${REF_NAME}" ]; then
	body=$(printf '%s\n\nRef: `%s`\nFinished: %s\n' "$MESSAGE" "$REF_NAME" "$finished")
else
	body=$(printf '%s\n\nFinished: %s\n' "$MESSAGE" "$finished")
fi

# ntfy Message header cannot carry raw newlines safely — collapse for header use.
header_message=$(printf '%s' "$body" | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-900)

args=(-H "Title: ${TITLE}" -H "Priority: ${PRIORITY}")
if [ -n "${TAGS}" ]; then
	args+=(-H "Tags: ${TAGS}")
fi
if [ -n "${NTFY_TOKEN:-}" ]; then
	args+=(-H "Authorization: Bearer ${NTFY_TOKEN}")
fi

url="${NTFY_URL%/}/${NTFY_TOPIC}"

if [ -n "${ATTACHMENT_PATH}" ] && [ -f "${ATTACHMENT_PATH}" ]; then
	filename="${ATTACHMENT_FILENAME:-$(basename "${ATTACHMENT_PATH}")}"
	args+=(-H "Filename: ${filename}" -H "Message: ${header_message}")
	curl -fsS -X PUT "${args[@]}" -T "${ATTACHMENT_PATH}" "${url}"
	echo "ntfy notification sent with attachment: ${filename}"
else
	curl -fsS "${args[@]}" -d "${body}" "${url}"
	echo "ntfy notification sent."
fi
