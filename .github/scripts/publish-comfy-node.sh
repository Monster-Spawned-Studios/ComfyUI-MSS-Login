#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# ComfyUI node publishing script (ComfyUI-MSS-Login)
# Use from CI (e.g. publish.yml) or locally to validate before publishing.
# Actual publish is done by Comfy-Org/publish-node-action when REGISTRY_ACCESS_TOKEN is set.
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="${1:-.}"
cd "$REPO_ROOT"

echo "[publish-comfy-node] Validating pyproject.toml and [tool.comfy]..."
if ! python3 -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
proj = data.get('project', {})
tool = data.get('tool', {}).get('comfy', {})
assert proj.get('name'), 'project.name required'
assert tool.get('PublisherId'), '[tool.comfy] PublisherId required'
assert tool.get('DisplayName'), '[tool.comfy] DisplayName required'
print('  name:', proj.get('name'))
print('  PublisherId:', tool.get('PublisherId'))
print('  DisplayName:', tool.get('DisplayName'))
" 2>/dev/null; then
    echo "[publish-comfy-node] Validation failed (tomllib needs Python 3.11+). Trying basic checks..."
    grep -q '^name = ' pyproject.toml || (echo "Missing project.name" && exit 1)
    grep -q 'PublisherId' pyproject.toml || (echo "Missing [tool.comfy] PublisherId" && exit 1)
    grep -q 'DisplayName' pyproject.toml || (echo "Missing [tool.comfy] DisplayName" && exit 1)
fi

echo "[publish-comfy-node] Validation passed. To publish: push to production with REGISTRY_ACCESS_TOKEN set in repo secrets, or run the Publish workflow manually."
