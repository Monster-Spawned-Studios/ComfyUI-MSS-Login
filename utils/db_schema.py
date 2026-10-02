"""
Canonical schema registry for the MSS-Login users database.

Used by schema-drift CI to ensure CREATE/ALTER paths keep expected tables and
columns across SQLite (plain + SQLCipher). Additive-only: new columns are
appended to EXPECTED_TABLES; never rename/remove without a migration plan.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1
SCHEMA_VERSION_KEY = "schema_version"


@dataclass(frozen=True)
class ColumnSpec:
	name: str
	# Normalized affinity: text | integer | real | blob
	affinity: str
	nullable: bool = True
	pk: bool = False


@dataclass(frozen=True)
class TableSpec:
	name: str
	columns: tuple[ColumnSpec, ...]

	@property
	def column_names(self) -> set[str]:
		return {c.name for c in self.columns}

	@property
	def primary_key(self) -> tuple[str, ...]:
		return tuple(c.name for c in self.columns if c.pk)


def _normalize_affinity(declared: str) -> str:
	t = (declared or "").strip().upper()
	if not t:
		return "blob"
	if "INT" in t:
		return "integer"
	if any(x in t for x in ("CHAR", "CLOB", "TEXT", "VARCHAR")):
		return "text"
	if any(x in t for x in ("REAL", "FLOA", "DOUB")):
		return "real"
	if any(x in t for x in ("BLOB", "BINARY")):
		return "blob"
	return "text"


EXPECTED_TABLES: dict[str, TableSpec] = {
	"users": TableSpec(
		"users",
		(
			ColumnSpec("user_id", "text", nullable=False, pk=True),
			ColumnSpec("username", "text", nullable=False),
			ColumnSpec("password_hash", "text", nullable=False),
			ColumnSpec("admin", "integer", nullable=False),
			ColumnSpec("groups", "text", nullable=False),
			ColumnSpec("sfw_check", "integer", nullable=False),
			ColumnSpec("mfa_enabled", "integer", nullable=False),
			ColumnSpec("totp_secret_encrypted", "text", nullable=True),
			ColumnSpec("backup_code_hash", "text", nullable=True),
			ColumnSpec("backup_code_used", "integer", nullable=False),
		),
	),
	"app_settings": TableSpec(
		"app_settings",
		(
			ColumnSpec("key", "text", nullable=False, pk=True),
			ColumnSpec("value", "text", nullable=True),
		),
	),
	"user_model_source_api_keys": TableSpec(
		"user_model_source_api_keys",
		(
			ColumnSpec("user_id", "text", nullable=False, pk=True),
			ColumnSpec("source", "text", nullable=False, pk=True),
			ColumnSpec("api_key_encrypted", "text", nullable=True),
			ColumnSpec("created_at", "real", nullable=False),
		),
	),
	"api_tokens": TableSpec(
		"api_tokens",
		(
			ColumnSpec("token_hash", "text", nullable=False, pk=True),
			ColumnSpec("user_id", "text", nullable=False),
			ColumnSpec("username", "text", nullable=False),
			ColumnSpec("expires_iso", "text", nullable=False),
			ColumnSpec("label", "text", nullable=True),
			ColumnSpec("created_at_iso", "text", nullable=True),
			ColumnSpec("last_used_at_iso", "text", nullable=True),
		),
	),
	"session_tokens": TableSpec(
		"session_tokens",
		(
			ColumnSpec("jti", "text", nullable=False, pk=True),
			ColumnSpec("user_id", "text", nullable=False),
			ColumnSpec("username", "text", nullable=False),
			ColumnSpec("created_at_iso", "text", nullable=False),
			ColumnSpec("last_used_at_iso", "text", nullable=False),
			ColumnSpec("exp_at_iso", "text", nullable=True),
			ColumnSpec("revoked", "integer", nullable=False),
		),
	),
	"shared_items": TableSpec(
		"shared_items",
		(
			ColumnSpec("user_id", "text", nullable=False, pk=True),
			ColumnSpec("folder", "text", nullable=False, pk=True),
			ColumnSpec("item_name", "text", nullable=False, pk=True),
			ColumnSpec("source_backend", "text", nullable=False),
			ColumnSpec("granted_by_user_id", "text", nullable=False),
			ColumnSpec("granted_by_role", "text", nullable=False),
			ColumnSpec("created_at", "real", nullable=False),
		),
	),
	"cached_models": TableSpec(
		"cached_models",
		(
			ColumnSpec("folder", "text", nullable=False, pk=True),
			ColumnSpec("item_name", "text", nullable=False, pk=True),
			ColumnSpec("updated_at", "real", nullable=False),
		),
	),
	"model_download_history": TableSpec(
		"model_download_history",
		(
			ColumnSpec("job_id", "text", nullable=False, pk=True),
			ColumnSpec("user_id", "text", nullable=False),
			ColumnSpec("username", "text", nullable=False),
			ColumnSpec("source", "text", nullable=False),
			ColumnSpec("status", "text", nullable=False),
			ColumnSpec("destination_type", "text", nullable=False),
			ColumnSpec("folder_type", "text", nullable=False),
			ColumnSpec("model_id", "text", nullable=False),
			ColumnSpec("model_version_id", "text", nullable=False),
			ColumnSpec("repo_id", "text", nullable=False),
			ColumnSpec("filename", "text", nullable=False),
			ColumnSpec("subfolder", "text", nullable=False),
			ColumnSpec("civitai_host", "text", nullable=False),
			ColumnSpec("local_path", "text", nullable=False),
			ColumnSpec("saved_relpath", "text", nullable=False),
			ColumnSpec("description", "text", nullable=False),
			ColumnSpec("trigger_words", "text", nullable=False),
			ColumnSpec("bytes_done", "real", nullable=False),
			ColumnSpec("total_bytes", "real", nullable=True),
			ColumnSpec("error", "text", nullable=False),
			ColumnSpec("created_at", "real", nullable=False),
			ColumnSpec("updated_at", "real", nullable=False),
			ColumnSpec("finished_at", "real", nullable=False),
			ColumnSpec("extra_json", "text", nullable=False),
		),
	),
	"ip_blacklist": TableSpec(
		"ip_blacklist",
		(
			ColumnSpec("ip", "text", nullable=False, pk=True),
			ColumnSpec("expires_at", "integer", nullable=True),
		),
	),
	"ip_whitelist": TableSpec(
		"ip_whitelist", (ColumnSpec("entry", "text", nullable=False, pk=True),)
	),
	"locked_devices": TableSpec(
		"locked_devices", (ColumnSpec("device_id", "text", nullable=False, pk=True),)
	),
}

# Secret-bearing columns that must remain decryptable after write
SECRET_COLUMNS: tuple[tuple[str, str], ...] = (
	("user_model_source_api_keys", "api_key_encrypted"),
	("users", "totp_secret_encrypted"),
	("app_settings", "value"),  # may hold ntfy/S3 ciphertext under known keys
)


def introspect_sqlite(conn) -> dict[str, dict[str, dict]]:
	"""
	Return {table: {column: {affinity, notnull, pk}}} for user tables.
	"""
	tables: dict[str, dict[str, dict]] = {}
	rows = conn.execute(
		"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
	).fetchall()
	for (name,) in rows:
		cols: dict[str, dict] = {}
		for cid, cname, ctype, notnull, _dflt, pk in conn.execute(f"PRAGMA table_info({name})"):
			cols[cname] = {
				"affinity": _normalize_affinity(ctype or ""),
				"notnull": bool(notnull),
				"pk": bool(pk),
			}
		tables[name] = cols
	return tables


def diff_schema(live: dict[str, dict[str, dict]]) -> list[str]:
	"""
	Compare live introspection against EXPECTED_TABLES.
	Returns a list of human-readable problem strings (empty if OK).
	Extra tables/columns are warnings only when they do not remove required ones.
	"""
	problems: list[str] = []
	for tname, spec in EXPECTED_TABLES.items():
		if tname not in live:
			problems.append(f"missing table: {tname}")
			continue
		live_cols = live[tname]
		for col in spec.columns:
			if col.name not in live_cols:
				problems.append(f"missing column: {tname}.{col.name}")
				continue
			info = live_cols[col.name]
			if info["affinity"] != col.affinity:
				# SQLite affinity can be loose; only fail on integer/text swaps for PK/secrets
				if col.pk or col.name.endswith("_encrypted") or col.name.endswith("_hash"):
					problems.append(
						f"affinity mismatch: {tname}.{col.name} expected {col.affinity} got {info['affinity']}"
					)
			if col.pk and not info["pk"]:
				problems.append(f"primary key missing: {tname}.{col.name}")
	return problems


def ensure_schema_version(app_settings_store) -> int:
	"""Persist SCHEMA_VERSION in app_settings if missing; return current version."""
	raw = app_settings_store.get(SCHEMA_VERSION_KEY)
	try:
		current = int(raw) if raw is not None and str(raw).strip() != "" else 0
	except (TypeError, ValueError):
		current = 0
	if current < SCHEMA_VERSION:
		app_settings_store.set(SCHEMA_VERSION_KEY, str(SCHEMA_VERSION))
		return SCHEMA_VERSION
	return current
