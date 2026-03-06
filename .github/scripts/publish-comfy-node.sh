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
VALID=false
if python3 -c "
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
    VALID=true
else
    echo "[publish-comfy-node] tomllib check unavailable (needs Python 3.11+). Trying basic checks..."
    if grep -q '^name = ' pyproject.toml &&
       grep -q 'PublisherId' pyproject.toml &&
       grep -q 'DisplayName' pyproject.toml; then
        VALID=true
    fi
fi

if [ "$VALID" != true ]; then
    echo "[publish-comfy-node] Validation failed. Skipping publish."
    exit 1
fi

echo "[publish-comfy-node] Validation passed."

if [ -z "${REGISTRY_ACCESS_TOKEN:-}" ]; then
    echo "[publish-comfy-node] Error: REGISTRY_ACCESS_TOKEN is not set. Skipping publish."
    exit 1
fi

if [[ "$REGISTRY_ACCESS_TOKEN" != pat* ]]; then
    echo "[publish-comfy-node] Error: REGISTRY_ACCESS_TOKEN does not start with 'pat'. Skipping publish."
    exit 1
fi

echo "[publish-comfy-node] Publishing node to ComfyUI Registry..."
comfy node publish
