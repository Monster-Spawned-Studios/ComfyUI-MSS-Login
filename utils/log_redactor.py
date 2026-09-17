# --- START OF FILE utils/log_redactor.py ---
"""
Log Redaction Utilities for ComfyUI-MSS-Login.

Sanitizes log files before export or display to remove sensitive information:
- JWT tokens (access and refresh)
- Bearer tokens
- Passwords and secret values (in JSON or config)
- Password hashes (Argon2, bcrypt)
- SQLCipher / encryption keys and PRAGMAs
- AWS / S3 credentials (access keys, secret keys)
- IP addresses (public and private, preserving localhost 127.0.0.1)
- Authorization and Cookie HTTP headers
"""

from __future__ import annotations

import re

# Regex patterns for sensitive data redaction
_JWT_PATTERN = re.compile(
	r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}\.[A-Za-z0-9._-]+\b"
)

_BEARER_PATTERN = re.compile(
	r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*",
	re.IGNORECASE,
)

_ARGON2_PATTERN = re.compile(
	r"\$argon2[a-z0-9]*\$v=\d+\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+"
)

_BCRYPT_PATTERN = re.compile(
	r"\$2[aby]?\$\d{2}\$[A-Za-z0-9./]{53}"
)

_AWS_ACCESS_KEY_PATTERN = re.compile(
	r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"
)

_SQLCIPHER_PRAGMA_PATTERN = re.compile(
	r"""(?i)PRAGMA\s+key\s*=\s*(?:'[^']*'|"[^"]*"|x'[0-9a-fA-F]+')"""
)

# JSON key-value pairs containing secrets
_JSON_SECRET_PATTERN = re.compile(
	r'("(?:password|new_password|old_password|pass|secret|apiKey|api_key|token|access_token|refresh_token|auth_token|ssh_pass|encryption_key|sqlcipher_key|db_key|s3_secret_key|secret_key|aws_secret_access_key)"\s*:\s*)"(?:[^"\\]|\\.)*"',
	re.IGNORECASE,
)

# Header patterns
_AUTH_HEADER_PATTERN = re.compile(
	r"(?i)^(Authorization:\s*)[^\r\n]+",
	re.MULTILINE,
)

_COOKIE_HEADER_PATTERN = re.compile(
	r"(?i)^((?:Set-)?Cookie:\s*)[^\r\n]+",
	re.MULTILINE,
)

# IPv4 pattern (excluding 127.0.0.1 and 0.0.0.0)
_IPV4_PATTERN = re.compile(
	r"\b(?!(?:127\.0\.0\.1|0\.0\.0\.0)\b)(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)

# IPv6 pattern (excluding ::1)
_IPV6_PATTERN = re.compile(
	r"\b(?!(?:::1)\b)(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
)


def redact_log_text(text: str) -> str:
	"""
	Sanitize log text by masking sensitive information.
	Safe for sharing publicly on GitHub issue trackers.
	"""
	if not text:
		return ""

	redacted = text

	# Redact JSON secret values first
	redacted = _JSON_SECRET_PATTERN.sub(r'\1"[REDACTED]"', redacted)

	# Redact HTTP headers
	redacted = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED]", redacted)
	redacted = _COOKIE_HEADER_PATTERN.sub(r"\1[REDACTED]", redacted)

	# Redact SQLCipher PRAGMA keys
	redacted = _SQLCIPHER_PRAGMA_PATTERN.sub(
		"PRAGMA key = '[REDACTED_SQLCIPHER_KEY]'", redacted
	)

	# Redact Hashes
	redacted = _ARGON2_PATTERN.sub("[REDACTED_ARGON2_HASH]", redacted)
	redacted = _BCRYPT_PATTERN.sub("[REDACTED_BCRYPT_HASH]", redacted)

	# Redact JWTs and Bearer tokens
	redacted = _JWT_PATTERN.sub("[REDACTED_JWT]", redacted)
	redacted = _BEARER_PATTERN.sub("Bearer [REDACTED_TOKEN]", redacted)

	# Redact Cloud credentials
	redacted = _AWS_ACCESS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", redacted)

	# Redact IP addresses
	redacted = _IPV4_PATTERN.sub("[REDACTED_IP]", redacted)
	redacted = _IPV6_PATTERN.sub("[REDACTED_IPV6]", redacted)

	return redacted
# --- END OF FILE utils/log_redactor.py ---
