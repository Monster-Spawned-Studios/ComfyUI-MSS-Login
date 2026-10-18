# --- START OF FILE utils/webauthn_guard.py ---
"""Guarded import for py_webauthn. Missing dependency disables passkeys only."""

from __future__ import annotations

WEBAUTHN_AVAILABLE = False
_import_error: str | None = None

try:
	import webauthn  # noqa: F401
	from webauthn import (
		generate_authentication_options,
		generate_registration_options,
		options_to_json,
		verify_authentication_response,
		verify_registration_response,
	)
	from webauthn.helpers.structs import (
		AttestationConveyancePreference,
		AuthenticatorSelectionCriteria,
		PublicKeyCredentialDescriptor,
		ResidentKeyRequirement,
		UserVerificationRequirement,
	)

	WEBAUTHN_AVAILABLE = True
except Exception as e:  # ImportError or version skew
	_import_error = str(e)
	generate_authentication_options = None  # type: ignore
	generate_registration_options = None  # type: ignore
	options_to_json = None  # type: ignore
	verify_authentication_response = None  # type: ignore
	verify_registration_response = None  # type: ignore
	AttestationConveyancePreference = None  # type: ignore
	AuthenticatorSelectionCriteria = None  # type: ignore
	PublicKeyCredentialDescriptor = None  # type: ignore
	ResidentKeyRequirement = None  # type: ignore
	UserVerificationRequirement = None  # type: ignore


def passkeys_enabled() -> bool:
	"""True when the webauthn library is importable."""
	return WEBAUTHN_AVAILABLE


def passkeys_unavailable_reason() -> str:
	return _import_error or "webauthn package not installed"
