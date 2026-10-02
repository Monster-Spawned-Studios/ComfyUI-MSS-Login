#!/usr/bin/env bash
# Install a pinned Gitleaks binary without calling the GitHub Releases API
# (avoids unauthenticated API rate limits on shared CI runners).
#
# Usage:
#   bash scripts/ci/install-gitleaks.sh
#   GITLEAKS_VERSION=8.30.1 bash scripts/ci/install-gitleaks.sh
#
# Installs to GITLEAKS_BIN_DIR (default: ./.ci-bin) and prints the binary path.
set -euo pipefail

VERSION="${GITLEAKS_VERSION:-8.30.1}"
BIN_DIR="${GITLEAKS_BIN_DIR:-${PWD}/.ci-bin}"
mkdir -p "${BIN_DIR}"

uname_s="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "${uname_s}" in
	linux*) os="linux" ;;
	darwin*) os="darwin" ;;
	mingw*|msys*|cygwin*) os="windows" ;;
	*)
		echo "Unsupported OS: ${uname_s}" >&2
		exit 1
		;;
esac

uname_m="$(uname -m | tr '[:upper:]' '[:lower:]')"
case "${uname_m}" in
	x86_64|amd64) arch="x64" ;;
	aarch64|arm64) arch="arm64" ;;
	armv7*|armhf) arch="armv7" ;;
	i386|i686|x86) arch="x32" ;;
	*)
		echo "Unsupported architecture: ${uname_m}" >&2
		exit 1
		;;
esac

if [[ "${os}" == "windows" ]]; then
	ext="zip"
	bin_name="gitleaks.exe"
else
	ext="tar.gz"
	bin_name="gitleaks"
fi

asset="gitleaks_${VERSION}_${os}_${arch}.${ext}"
url="https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}/${asset}"
archive="${BIN_DIR}/${asset}"

echo "Downloading Gitleaks v${VERSION} (${os}/${arch}) from pinned release URL..."
echo "URL: ${url}"

# Direct release asset URL — no api.github.com (rate-limit safe).
# Retry transient network failures common on remote runners.
if command -v curl >/dev/null 2>&1; then
	curl -fsSL \
		--retry 5 \
		--retry-delay 2 \
		--retry-all-errors \
		-A "mss-login-ci-gitleaks-installer" \
		-o "${archive}" \
		"${url}"
elif command -v wget >/dev/null 2>&1; then
	wget -q --tries=5 --timeout=30 -O "${archive}" "${url}"
else
	echo "Neither curl nor wget is available on this runner." >&2
	exit 1
fi

if [[ ! -s "${archive}" ]]; then
	echo "Download failed or produced an empty file: ${archive}" >&2
	exit 1
fi

# Sanity-check: HTML error pages are not valid archives.
if file "${archive}" 2>/dev/null | grep -qi 'html\|text'; then
	echo "Download does not look like a binary archive (got text/HTML). First bytes:" >&2
	head -c 200 "${archive}" >&2 || true
	exit 1
fi

workdir="${BIN_DIR}/extract"
rm -rf "${workdir}"
mkdir -p "${workdir}"

if [[ "${ext}" == "zip" ]]; then
	if command -v unzip >/dev/null 2>&1; then
		unzip -qo "${archive}" -d "${workdir}"
	else
		python3 - <<PY
import zipfile
zipfile.ZipFile("${archive}").extractall("${workdir}")
PY
	fi
else
	tar --no-same-owner -xzf "${archive}" -C "${workdir}"
fi

# Binary is usually at the archive root; search just in case.
found="$(find "${workdir}" -type f -name "${bin_name}" | head -n 1 || true)"
if [[ -z "${found}" ]]; then
	echo "Could not find ${bin_name} after extracting ${asset}" >&2
	find "${workdir}" -maxdepth 2 -print >&2 || true
	exit 1
fi

install_path="${BIN_DIR}/${bin_name}"
cp -f "${found}" "${install_path}"
chmod +x "${install_path}"

# Soft-link a stable name without .exe for scripts that expect "gitleaks".
if [[ "${bin_name}" == "gitleaks.exe" ]]; then
	ln -sfn "${install_path}" "${BIN_DIR}/gitleaks" 2>/dev/null || cp -f "${install_path}" "${BIN_DIR}/gitleaks"
fi

echo "Installed: ${install_path}"
"${install_path}" version
echo "${install_path}"
