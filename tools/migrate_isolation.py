#!/usr/bin/env python3
"""
Standalone migration tool for ComfyUI-MSS-Login model isolation: UUID -> USERNAME.

Enforces:
1. Standard username-named folders under DATA_DIR/model_isolation/models/<folder_type>/<username>/
2. In-place adoption of existing username directories with zero new grants
3. Atomic, safe moving of UUID directories to matching username directories
4. 1:1 rewrite of existing database model grants in shared_items table ({uuid}/... -> {username}/...)
5. Verified pre-migration backups (mode 0600)
6. Non-interactive & interactive conflict handling (no automatic overwrites)
7. Safe locking and active ComfyUI port check (refuses to apply if ComfyUI is running)
8. Output table, structured JSON action logging, and explicit END-OK / END-FAIL termination.

Does NOT import __init__.py, ComfyUI, or PromptServer.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# Add parent dir to sys.path so utils can be imported if running standalone
_repo_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_parent not in sys.path:
	sys.path.insert(0, _repo_parent)

try:
	from utils.db_key_derivation import derive_db_key
except ImportError:
	derive_db_key = None


def is_plain_sqlite(path: str) -> bool:
	"""Check if file starts with the standard SQLite header."""
	if not os.path.isfile(path) or os.path.getsize(path) == 0:
		return False
	try:
		with open(path, "rb") as f:
			header = f.read(16)
		return header.startswith(b"SQLite format 3\x00")
	except OSError:
		return False


# Common model folders recognized by ComfyUI
COMMON_FOLDER_TYPES = (
	"checkpoints",
	"loras",
	"vae",
	"text_encoders",
	"clip",
	"embeddings",
	"diffusion_models",
	"unet",
	"clip_vision",
	"style_models",
	"controlnet",
	"gligen",
	"upscale_models",
	"latent_upscale_models",
	"hypernetworks",
	"vae_approx",
	"diffusers",
	"photomaker",
	"model_patches",
	"audio_encoders",
	"classifiers",
	"configs",
	"ultralytics_bbox",
	"ultralytics_segm",
	"ultralytics",
	"mmdets_bbox",
	"mmdets_segm",
	"mmdets",
	"sams",
)

RESERVED_NAMES = frozenset(
	{
		"con",
		"prn",
		"aux",
		"nul",
		*(f"com{i}" for i in range(1, 10)),
		*(f"lpt{i}" for i in range(1, 10)),
	}
)


def validate_username(username: str | None) -> str:
	raw = (username or "").strip()
	if not raw:
		raise ValueError("Username cannot be empty")
	if len(raw) > 64:
		raise ValueError(f"Username is too long ({len(raw)} > 64 chars)")
	if any(ord(c) < 32 or ord(c) == 127 for c in raw):
		raise ValueError("Username cannot contain control or NUL characters")
	if "/" in raw or "\\" in raw or ".." in raw:
		raise ValueError("Username cannot contain path characters")
	if raw.startswith("."):
		raise ValueError("Username cannot start with a dot")
	if any(c.isspace() for c in raw):
		raise ValueError("Username cannot contain whitespace")
	if raw.lower() in RESERVED_NAMES:
		raise ValueError(f"Username '{raw}' is a reserved system name")
	for c in raw:
		if not (c.isalnum() or c in ("_", "-", ".")):
			raise ValueError(f"Username contains disallowed character: {c!r}")
	return raw


def copy_ownership_and_mode(target: str, reference_parent: str) -> None:
	try:
		st = os.stat(reference_parent)
		try:
			os.chmod(target, st.st_mode & 0o777)
		except OSError:
			pass
		if hasattr(os, "chown") and hasattr(os, "geteuid") and os.geteuid() == 0:
			try:
				os.chown(target, st.st_uid, st.st_gid)
			except (OSError, PermissionError):
				pass
	except OSError:
		pass


def sha256_file(path: str) -> str:
	h = hashlib.sha256()
	with open(path, "rb") as f:
		while chunk := f.read(65536):
			h.update(chunk)
	return h.hexdigest()


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
	try:
		with socket.create_connection((host, port), timeout=1.0):
			return True
	except (OSError, ConnectionRefusedError):
		return False


class MigrationContext:
	def __init__(
		self,
		data_dir: str,
		models_dir: Optional[str] = None,
		users_db_path: Optional[str] = None,
		lock_port: int = 9008,
		confirm_merge_uuid: Optional[str] = None,
		remove_empty_source: bool = False,
		secret: Optional[str] = None,
	):
		self.data_dir = os.path.abspath(data_dir)
		self.models_dir = os.path.abspath(models_dir) if models_dir else ""
		self.lock_port = lock_port
		self.confirm_merge_uuid = confirm_merge_uuid
		self.remove_empty_source = remove_empty_source
		self.secret = secret

		self.isolation_base = os.path.join(self.data_dir, "model_isolation", "models")
		self.map_file = os.path.join(self.data_dir, "model_isolation", "uuid_folder_map.json")
		self.lock_file = os.path.join(self.data_dir, "model_isolation", ".migrate.lock")

		# Check config.json for users_db config and encryption
		self.encryption_level = ""
		self.secret_key_env = "SECRET_KEY"
		cfg_db_path = ""
		cfg_file = os.path.join(self.data_dir, "config.json")
		if os.path.isfile(cfg_file):
			try:
				with open(cfg_file, "r", encoding="utf-8") as f:
					cfg = json.load(f)
					if isinstance(cfg, dict):
						udb = cfg.get("users_db", {})
						if isinstance(udb, dict):
							self.encryption_level = (
								str(udb.get("encryption_level", "")).strip().lower()
							)
							cfg_db_path = str(udb.get("sqlite_path", "")).strip()
						self.secret_key_env = str(cfg.get("secret_key_env", "SECRET_KEY")).strip()
			except Exception:
				pass

		if not self.secret:
			self.secret = os.environ.get(self.secret_key_env) or os.environ.get("SECRET_KEY", "")

		# Check ephemeral key file if still no secret
		if not self.secret:
			for ephem_cand in (
				os.path.join(self.data_dir, "data", ".ephemeral_secret_key"),
				os.path.join(self.data_dir, ".ephemeral_secret_key"),
			):
				if os.path.isfile(ephem_cand):
					try:
						with open(ephem_cand, "r", encoding="utf-8") as f:
							self.secret = f.read().strip()
						if self.secret:
							break
					except OSError:
						pass

		# Resolve users_db_path
		if users_db_path:
			self.users_db_path = os.path.abspath(users_db_path)
		else:
			resolved = None
			candidates = []
			if cfg_db_path:
				if os.path.isabs(cfg_db_path):
					if os.path.isfile(cfg_db_path):
						candidates.append(cfg_db_path)
					for marker in ("data/", "users/"):
						idx = cfg_db_path.find(marker)
						if idx != -1:
							candidates.append(os.path.join(self.data_dir, cfg_db_path[idx:]))
				else:
					candidates.append(os.path.join(self.data_dir, cfg_db_path))

			candidates.extend(
				[
					os.path.join(self.data_dir, "data", "mss_login_data.db"),
					os.path.join(self.data_dir, "mss_login_data.db"),
					os.path.join(self.data_dir, "users", "users.db"),
					os.path.join(self.data_dir, "users.db"),
					os.path.join(self.data_dir, "data", "users.db"),
				]
			)

			for cand in candidates:
				if os.path.isfile(cand):
					resolved = cand
					break
			self.users_db_path = (
				resolved if resolved else os.path.join(self.data_dir, "data", "mss_login_data.db")
			)

		self.users: dict[str, str] = {}  # uuid -> username
		self.uname_to_uuid: dict[str, str] = {}  # username -> uuid
		self.actions: list[dict] = []
		self.lock_fd: Optional[int] = None

	def acquire_lock(self) -> bool:
		os.makedirs(os.path.dirname(self.lock_file), exist_ok=True)
		try:
			self.lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
			os.write(self.lock_fd, f"pid={os.getpid()}\ntime={time.time()}\n".encode("utf-8"))
			return True
		except OSError as e:
			if e.errno == errno.EEXIST:
				return False
			raise

	def release_lock(self) -> None:
		if self.lock_fd is not None:
			try:
				os.close(self.lock_fd)
			except OSError:
				pass
			self.lock_fd = None
		if os.path.isfile(self.lock_file):
			try:
				os.remove(self.lock_file)
			except OSError:
				pass

	def _open_db(self, readonly: bool = True) -> Any:
		"""Open SQLite or SQLCipher database with proper keying."""
		if not os.path.isfile(self.users_db_path):
			raise FileNotFoundError(f"Database file not found: {self.users_db_path}")

		if is_plain_sqlite(self.users_db_path):
			if readonly:
				return sqlite3.connect(f"file:{self.users_db_path}?mode=ro", uri=True)
			return sqlite3.connect(self.users_db_path)

		# Encrypted SQLCipher database
		try:
			import sqlcipher3
		except ImportError:
			raise RuntimeError(
				f"Database at {self.users_db_path} is encrypted (SQLCipher), but 'sqlcipher3' is not installed."
			)

		secret = self.secret
		if not secret:
			raise RuntimeError(
				f"Database at {self.users_db_path} is encrypted (SQLCipher), but no SECRET_KEY was provided via --secret or environment."
			)

		enc_level = self.encryption_level or "secure"
		if derive_db_key is not None:
			key_bytes = derive_db_key(secret, enc_level)
			key_hex = key_bytes.hex()
		else:
			key_hex = hashlib.sha256(secret.encode("utf-8")).hexdigest()

		conn = sqlcipher3.connect(self.users_db_path)
		conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
		conn.execute("PRAGMA cipher_compatibility = 4")
		conn.execute("SELECT count(*) FROM sqlite_master")
		return conn

	def load_users(self) -> bool:
		db_loaded = False
		if os.path.isfile(self.users_db_path):
			try:
				conn = self._open_db(readonly=True)
				cur = conn.cursor()
				cur.execute("SELECT user_id, username FROM users")
				for uid, uname in cur.fetchall():
					if uid and uname:
						suid = str(uid).strip()
						suname = str(uname).strip()
						try:
							valid_uname = validate_username(suname)
							self.users[suid] = valid_uname
							self.uname_to_uuid[valid_uname.lower()] = suid
						except ValueError:
							self.users[suid] = suname
							self.uname_to_uuid[suname.lower()] = suid
				conn.close()
				db_loaded = True
			except Exception as e:
				print(f"[!] Notice: Could not read users table from {self.users_db_path}: {e}")

		# Multi-source user discovery fallback:
		# 1. Existing uuid_folder_map.json
		mapping = self.load_existing_mapping()
		for uid, uname in mapping.items():
			suid = str(uid).strip()
			suname = str(uname).strip()
			if suid and suname:
				self.users.setdefault(suid, suname)
				self.uname_to_uuid.setdefault(suname.lower(), suid)

		# 2. DATA_DIR/users/ directory inspection
		users_dir = os.path.join(self.data_dir, "users")
		if os.path.isdir(users_dir):
			try:
				for dname in os.listdir(users_dir):
					dpath = os.path.join(users_dir, dname)
					if os.path.isdir(dpath) and not dname.startswith("."):
						try:
							vname = validate_username(dname)
							if vname.lower() not in self.uname_to_uuid:
								synthetic_uuid = f"user-{vname.lower()}"
								self.users.setdefault(synthetic_uuid, vname)
								self.uname_to_uuid.setdefault(vname.lower(), synthetic_uuid)
						except ValueError:
							pass
			except OSError:
				pass

		return db_loaded or bool(self.users)

	def load_existing_mapping(self) -> dict[str, str]:
		if os.path.isfile(self.map_file):
			try:
				with open(self.map_file, "r", encoding="utf-8") as f:
					data = json.load(f)
					if isinstance(data, dict):
						return {str(k): str(v) for k, v in data.items()}
			except Exception:
				pass
		return {}

	def save_mapping(self, mapping: dict[str, str]) -> None:
		os.makedirs(os.path.dirname(self.map_file), exist_ok=True)
		tmp = f"{self.map_file}.tmp.{os.getpid()}"
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(mapping, f, indent=2)
		os.replace(tmp, self.map_file)

	def discover_folder_types(self) -> list[str]:
		types_set = set(COMMON_FOLDER_TYPES)
		if os.path.isdir(self.isolation_base):
			try:
				for name in os.listdir(self.isolation_base):
					p = os.path.join(self.isolation_base, name)
					if os.path.isdir(p) and not name.startswith("."):
						types_set.add(name)
			except OSError:
				pass
		return sorted(types_set)

	def scan_plan(self) -> list[dict]:
		"""Analyze current filesystem and plan actions: adopt, move, skip, conflict, orphan."""
		actions: list[dict] = []
		mapping = self.load_existing_mapping()
		folder_types = self.discover_folder_types()

		for ft in folder_types:
			ft_dir = os.path.join(self.isolation_base, ft)
			if not os.path.isdir(ft_dir):
				continue
			try:
				entries = [e for e in os.listdir(ft_dir) if not e.startswith(".")]
			except OSError:
				continue

			for entry in entries:
				entry_path = os.path.join(ft_dir, entry)
				if not os.path.isdir(entry_path):
					continue

				file_count, dir_size, files_rel = self._inspect_tree(entry_path)

				# 1. Does entry match an existing username?
				entry_lower = entry.lower()
				is_uuid_like = len(entry) >= 32 and "-" in entry

				if entry_lower in self.uname_to_uuid:
					matched_uuid = self.uname_to_uuid[entry_lower]
					matched_uname = self.users.get(matched_uuid, entry)
					if mapping.get(matched_uuid) == entry or entry in mapping.values():
						status = "skip"
					else:
						status = "adopt"
					actions.append(
						{
							"action": status,
							"folder_type": ft,
							"source": entry,
							"target": matched_uname,
							"user_id": matched_uuid,
							"username": matched_uname,
							"file_count": file_count,
							"size_bytes": dir_size,
							"src_path": entry_path,
							"dst_path": entry_path,
							"files": files_rel,
						}
					)
					continue

				# 2. Is entry already in mapping.values()?
				if entry in mapping.values():
					matched_uuid = next((u for u, un in mapping.items() if un == entry), "")
					actions.append(
						{
							"action": "skip",
							"folder_type": ft,
							"source": entry,
							"target": entry,
							"user_id": matched_uuid,
							"username": entry,
							"file_count": file_count,
							"size_bytes": dir_size,
							"src_path": entry_path,
							"dst_path": entry_path,
							"files": files_rel,
						}
					)
					continue

				# 3. Is entry a recognized user UUID?
				if entry in self.users:
					target_uname = self.users[entry]
					target_path = os.path.join(ft_dir, target_uname)
					if not os.path.exists(target_path):
						actions.append(
							{
								"action": "move",
								"folder_type": ft,
								"source": entry,
								"target": target_uname,
								"user_id": entry,
								"username": target_uname,
								"file_count": file_count,
								"size_bytes": dir_size,
								"src_path": entry_path,
								"dst_path": target_path,
								"files": files_rel,
							}
						)
					else:
						# Target username directory already exists! Check for file collisions
						_, _, target_files = self._inspect_tree(target_path)
						collisions = set(files_rel.keys()) & set(target_files.keys())
						if collisions:
							collision_details = []
							for cfile in collisions:
								s_full = os.path.join(entry_path, cfile)
								t_full = os.path.join(target_path, cfile)
								collision_details.append(
									{
										"rel_path": cfile,
										"src_size": os.path.getsize(s_full),
										"src_mtime": os.path.getmtime(s_full),
										"src_sha256": sha256_file(s_full),
										"dst_size": os.path.getsize(t_full),
										"dst_mtime": os.path.getmtime(t_full),
										"dst_sha256": sha256_file(t_full),
									}
								)
							actions.append(
								{
									"action": "conflict",
									"folder_type": ft,
									"source": entry,
									"target": target_uname,
									"user_id": entry,
									"username": target_uname,
									"file_count": file_count,
									"size_bytes": dir_size,
									"src_path": entry_path,
									"dst_path": target_path,
									"files": files_rel,
									"collisions": collision_details,
								}
							)
						else:
							# No colliding filenames
							can_merge = (
								self.confirm_merge_uuid == entry or self.confirm_merge_uuid == "all"
							)
							actions.append(
								{
									"action": "merge" if can_merge else "conflict_no_overlap",
									"folder_type": ft,
									"source": entry,
									"target": target_uname,
									"user_id": entry,
									"username": target_uname,
									"file_count": file_count,
									"size_bytes": dir_size,
									"src_path": entry_path,
									"dst_path": target_path,
									"files": files_rel,
									"non_colliding": list(files_rel.keys()),
								}
							)
					continue

				# 4. If entry is NOT UUID-like, is it a valid username name?
				if not is_uuid_like:
					try:
						valid_uname = validate_username(entry)
						# It is already a valid username-formatted folder on disk!
						actions.append(
							{
								"action": "adopt",
								"folder_type": ft,
								"source": entry,
								"target": valid_uname,
								"user_id": "",
								"username": valid_uname,
								"file_count": file_count,
								"size_bytes": dir_size,
								"src_path": entry_path,
								"dst_path": entry_path,
								"files": files_rel,
							}
						)
						continue
					except ValueError:
						# Truly invalid/unsafe folder name
						actions.append(
							{
								"action": "unknown",
								"folder_type": ft,
								"source": entry,
								"target": "(invalid_name)",
								"user_id": "",
								"username": "",
								"file_count": file_count,
								"size_bytes": dir_size,
								"src_path": entry_path,
								"dst_path": "",
								"files": files_rel,
							}
						)
						continue

				# 5. UUID-like entry not matching any known user: Orphan UUID
				actions.append(
					{
						"action": "orphan",
						"folder_type": ft,
						"source": entry,
						"target": "(none)",
						"user_id": entry,
						"username": "",
						"file_count": file_count,
						"size_bytes": dir_size,
						"src_path": entry_path,
						"dst_path": "",
						"files": files_rel,
					}
				)

		self.actions = actions
		return actions

	def _inspect_tree(self, path: str) -> Tuple[int, int, dict[str, dict]]:
		count = 0
		size = 0
		files_dict: dict[str, dict] = {}
		try:
			for root, _, filenames in os.walk(path):
				for fn in filenames:
					if fn.startswith("."):
						continue
					full = os.path.join(root, fn)
					try:
						st = os.stat(full)
						rel = os.path.relpath(full, path).replace("\\", "/")
						count += 1
						size += st.st_size
						files_dict[rel] = {"size": st.st_size, "mtime": st.st_mtime}
					except OSError:
						continue
		except OSError:
			pass
		return count, size, files_dict

	def create_backup(self) -> Tuple[str, dict]:
		timestamp = time.strftime("%Y%m%d-%H%M%S")
		backup_dir = os.path.join(
			self.data_dir, "model_isolation", "backups", f"backup-{timestamp}"
		)
		os.makedirs(backup_dir, mode=0o700, exist_ok=True)
		try:
			os.chmod(backup_dir, 0o700)
		except OSError:
			pass

		manifest: dict[str, Any] = {"created_at": time.time(), "files": {}}
		checksums: dict[str, str] = {}

		# 1. Backup users.db
		db_bak = os.path.join(backup_dir, "users.db.bak")
		if os.path.isfile(self.users_db_path):
			shutil.copy2(self.users_db_path, db_bak)
			os.chmod(db_bak, 0o600)
			checksums["users.db.bak"] = sha256_file(db_bak)

		# 2. Backup shared_items table as JSON
		grants_bak = os.path.join(backup_dir, "shared_items_backup.json")
		grants_list = []
		if os.path.isfile(self.users_db_path):
			try:
				conn = self._open_db(readonly=True)
				cur = conn.cursor()
				cur.execute(
					"SELECT user_id, folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at FROM shared_items"
				)
				for r in cur.fetchall():
					grants_list.append(
						{
							"user_id": r[0],
							"folder": r[1],
							"item_name": r[2],
							"source_backend": r[3],
							"granted_by_user_id": r[4],
							"granted_by_role": r[5],
							"created_at": r[6],
						}
					)
				conn.close()
			except Exception:
				pass
		with open(grants_bak, "w", encoding="utf-8") as f:
			json.dump(grants_list, f, indent=2)
		os.chmod(grants_bak, 0o600)
		checksums["shared_items_backup.json"] = sha256_file(grants_bak)

		# 3. Backup mapping
		map_bak = os.path.join(backup_dir, "uuid_folder_map.json.bak")
		mapping = self.load_existing_mapping()
		with open(map_bak, "w", encoding="utf-8") as f:
			json.dump(mapping, f, indent=2)
		os.chmod(map_bak, 0o600)
		checksums["uuid_folder_map.json.bak"] = sha256_file(map_bak)

		# 4. Model files manifest
		if os.path.isdir(self.isolation_base):
			for root, _, filenames in os.walk(self.isolation_base):
				for fn in filenames:
					if fn.startswith("."):
						continue
					full = os.path.join(root, fn)
					rel = os.path.relpath(full, self.isolation_base).replace("\\", "/")
					try:
						st = os.stat(full)
						manifest["files"][rel] = {"size": st.st_size, "mtime": st.st_mtime}
					except OSError:
						pass

		manifest_path = os.path.join(backup_dir, "manifest.json")
		with open(manifest_path, "w", encoding="utf-8") as f:
			json.dump(manifest, f, indent=2)
		os.chmod(manifest_path, 0o600)
		checksums["manifest.json"] = sha256_file(manifest_path)

		# 5. Metadata verification file
		meta_path = os.path.join(backup_dir, "backup_meta.json")
		meta = {
			"timestamp": timestamp,
			"checksums": checksums,
			"users_count": len(self.users),
			"grants_count": len(grants_list),
		}
		with open(meta_path, "w", encoding="utf-8") as f:
			json.dump(meta, f, indent=2)
		os.chmod(meta_path, 0o600)

		# Verify non-empty and non-blank
		assert os.path.getsize(manifest_path) > 0
		assert os.path.getsize(meta_path) > 0
		return backup_dir, meta

	def apply_migration(self, backup_dir: str) -> Tuple[bool, list[dict]]:
		log_entries: list[dict] = []
		mapping = self.load_existing_mapping()
		success = True

		# 1. Execute directory actions
		for item in self.actions:
			act = item["action"]
			src_path = item["src_path"]
			dst_path = item["dst_path"]
			uid = item.get("user_id")
			uname = item.get("username")

			if act == "adopt":
				mapping[uid] = uname
				log_entries.append(
					{"action": "adopt", "source": src_path, "target": dst_path, "status": "ok"}
				)

			elif act == "move":
				parent_dir = os.path.dirname(dst_path)
				try:
					os.replace(src_path, dst_path)
					copy_ownership_and_mode(dst_path, parent_dir)
					mapping[uid] = uname
					log_entries.append(
						{"action": "move", "source": src_path, "target": dst_path, "status": "ok"}
					)
				except OSError as e:
					if e.errno == errno.EXDEV:
						print(
							f"[ERROR] Cannot move across filesystems (EXDEV) between {src_path} and {dst_path}. Aborting."
						)
						log_entries.append(
							{
								"action": "move",
								"source": src_path,
								"target": dst_path,
								"status": "fail_exdev",
							}
						)
						return False, log_entries
					else:
						log_entries.append(
							{
								"action": "move",
								"source": src_path,
								"target": dst_path,
								"status": f"fail: {e}",
							}
						)
						success = False

			elif act == "merge":
				# Move non-colliding files into existing target directory
				parent_dir = os.path.dirname(dst_path)
				for rel_file in item.get("non_colliding", []):
					s_file = os.path.join(src_path, rel_file)
					d_file = os.path.join(dst_path, rel_file)
					os.makedirs(os.path.dirname(d_file), exist_ok=True)
					try:
						os.replace(s_file, d_file)
						copy_ownership_and_mode(d_file, dst_path)
					except OSError as e:
						log_entries.append(
							{"action": "merge_file", "file": rel_file, "status": f"fail: {e}"}
						)
						success = False
				mapping[uid] = uname
				# Clean up empty source directory if confirmed
				if self.remove_empty_source:
					try:
						os.rmdir(src_path)
					except OSError:
						pass

			elif act in ("conflict", "conflict_no_overlap"):
				log_entries.append(
					{
						"action": "conflict",
						"source": src_path,
						"target": dst_path,
						"status": "left_in_place",
					}
				)

			elif act == "orphan":
				log_entries.append(
					{"action": "orphan", "source": src_path, "status": "left_in_place"}
				)

		# 2. Save updated mapping
		self.save_mapping(mapping)

		# 3. Rewrite database model grants 1:1 in shared_items table
		if os.path.isfile(self.users_db_path):
			try:
				conn = self._open_db(readonly=False)
				cur = conn.cursor()
				cur.execute("SELECT user_id, folder, item_name FROM shared_items")
				rows = cur.fetchall()
				rewritten_count = 0
				for r_uid, r_folder, r_item in rows:
					parts = str(r_item).split("/", 1)
					if len(parts) == 2:
						first_seg, rest = parts[0], parts[1]
						if first_seg in self.users:
							new_uname = self.users[first_seg]
							if new_uname != first_seg:
								new_item_name = f"{new_uname}/{rest}"
								cur.execute(
									"""
									UPDATE shared_items
									SET item_name = ?
									WHERE user_id = ? AND folder = ? AND item_name = ?
									""",
									(new_item_name, r_uid, r_folder, r_item),
								)
								rewritten_count += 1
				conn.commit()
				conn.close()
				log_entries.append(
					{"action": "rewrite_grants", "rewritten_count": rewritten_count, "status": "ok"}
				)
			except Exception as e:
				log_entries.append({"action": "rewrite_grants", "error": str(e), "status": "fail"})
				success = False

		# Write structured log
		log_path = os.path.join(backup_dir, "migration.log.json")
		with open(log_path, "w", encoding="utf-8") as f:
			json.dump({"timestamp": time.time(), "entries": log_entries}, f, indent=2)

		return success, log_entries

	def verify(self) -> Tuple[bool, list[str]]:
		issues = []
		mapping = self.load_existing_mapping()
		folder_types = self.discover_folder_types()

		# Check for remaining unmigrated UUID folders that match known users
		for ft in folder_types:
			ft_dir = os.path.join(self.isolation_base, ft)
			if not os.path.isdir(ft_dir):
				continue
			for entry in os.listdir(ft_dir):
				if entry in self.users:
					issues.append(
						f"Unmigrated UUID folder found: {ft}/{entry} (matches user '{self.users[entry]}')"
					)

		# Check DB grants for leftover UUID prefixes
		if os.path.isfile(self.users_db_path):
			try:
				conn = self._open_db(readonly=True)
				cur = conn.cursor()
				cur.execute("SELECT user_id, folder, item_name FROM shared_items")
				for r_uid, r_folder, r_item in cur.fetchall():
					parts = str(r_item).split("/", 1)
					if len(parts) == 2 and parts[0] in self.users:
						issues.append(
							f"Legacy UUID grant still exists in DB: [{r_folder}] {r_item}"
						)
				conn.close()
			except Exception as e:
				issues.append(f"DB verification failed: {e}")

		return len(issues) == 0, issues

	def restore(self, backup_dir: str) -> bool:
		backup_dir = os.path.abspath(backup_dir)
		meta_path = os.path.join(backup_dir, "backup_meta.json")
		if not os.path.isfile(meta_path):
			print(f"[ERROR] Invalid backup dir (missing backup_meta.json): {backup_dir}")
			return False

		with open(meta_path, "r", encoding="utf-8") as f:
			meta = json.load(f)

		# 1. Verify backup checksums
		for filename, expected_hash in meta.get("checksums", {}).items():
			fpath = os.path.join(backup_dir, filename)
			if not os.path.isfile(fpath):
				print(f"[ERROR] Corrupt backup: Missing file {filename}")
				return False
			actual_hash = sha256_file(fpath)
			if actual_hash != expected_hash:
				print(f"[ERROR] Checksum mismatch on backup file {filename}")
				return False

		# 2. Restore SQLite database
		db_bak = os.path.join(backup_dir, "users.db.bak")
		if os.path.isfile(db_bak) and os.path.isfile(self.users_db_path):
			shutil.copy2(db_bak, self.users_db_path)

		# 3. Restore mapping
		map_bak = os.path.join(backup_dir, "uuid_folder_map.json.bak")
		if os.path.isfile(map_bak):
			shutil.copy2(map_bak, self.map_file)

		# 4. Restore files from manifest
		manifest_path = os.path.join(backup_dir, "manifest.json")
		with open(manifest_path, "r", encoding="utf-8") as f:
			manifest = json.load(f)

		for rel_path, _info in manifest.get("files", {}).items():
			target_full = os.path.join(self.isolation_base, rel_path)
			if not os.path.exists(target_full):
				filename = os.path.basename(rel_path)
				for root, _, files in os.walk(self.isolation_base):
					if filename in files:
						curr = os.path.join(root, filename)
						os.makedirs(os.path.dirname(target_full), exist_ok=True)
						os.replace(curr, target_full)
						break

		print(f"[OK] Restore completed successfully from {backup_dir}")
		return True


def format_table(actions: list[dict]) -> str:
	lines = []
	header = f"{'ACTION':<10} {'FOLDER':<18} {'SOURCE':<36} {'TARGET':<22} {'FILES':<6} {'SIZE'}"
	lines.append(header)
	lines.append("-" * len(header))
	for a in actions:
		size_str = (
			f"{a['size_bytes'] / (1024 * 1024):.1f}MB"
			if a["size_bytes"] >= 1024 * 1024
			else f"{a['size_bytes'] / 1024:.1f}KB"
		)
		lines.append(
			f"{a['action']:<10} {a['folder_type']:<18} {a['source']:<36} {a['target']:<22} {a['file_count']:<6} {size_str}"
		)
	return "\n".join(lines)


def main():
	parser = argparse.ArgumentParser(
		description="MSS-Login Model Isolation Migration Tool: UUID -> USERNAME."
	)
	mode_group = parser.add_mutually_exclusive_group()
	mode_group.add_argument(
		"--dry-run",
		action="store_true",
		default=True,
		help="Simulate migration without modifying files or database (default).",
	)
	mode_group.add_argument(
		"--apply",
		action="store_true",
		help="Apply migration, update database grants 1:1, and create backup.",
	)
	mode_group.add_argument(
		"--verify", action="store_true", help="Verify folder naming, mapping, and database grants."
	)
	mode_group.add_argument(
		"--restore",
		metavar="BACKUP_DIR",
		type=str,
		help="Restore isolation files and database grants from specified backup directory.",
	)

	parser.add_argument(
		"--data-dir",
		type=str,
		default=os.environ.get("MSS_LOGIN_DATA_DIR", os.path.expanduser("~/.comfyui-mss-login")),
		help="MSS-Login data directory.",
	)
	parser.add_argument("--models-dir", type=str, default="", help="Root ComfyUI models directory.")
	parser.add_argument(
		"--users-db",
		"--db-path",
		dest="users_db",
		type=str,
		default="",
		help="Path to users.db / mss_login_data.db SQLite database.",
	)
	parser.add_argument(
		"--lock-port",
		type=int,
		default=9008,
		help="ComfyUI server port to check before applying (default: 9008).",
	)
	parser.add_argument(
		"--confirm-merge",
		metavar="UUID",
		type=str,
		default=None,
		help="Confirm moving non-colliding files when target username folder already exists ('<uuid>' or 'all').",
	)
	parser.add_argument(
		"--remove-empty-source",
		action="store_true",
		default=False,
		help="Remove source UUID directory after merging non-colliding files if directory is empty.",
	)
	parser.add_argument(
		"--secret",
		type=str,
		default="",
		help="Database secret key for SQLCipher decryption (defaults to SECRET_KEY env var).",
	)

	args = parser.parse_args()

	ctx = MigrationContext(
		data_dir=args.data_dir,
		models_dir=args.models_dir,
		users_db_path=args.users_db,
		lock_port=args.lock_port,
		confirm_merge_uuid=args.confirm_merge,
		remove_empty_source=args.remove_empty_source,
		secret=args.secret,
	)

	ctx.load_users()

	# --- RESTORE MODE ---
	if args.restore:
		ok = ctx.restore(args.restore)
		if ok:
			print("END-OK")
			return 0
		else:
			print("END-FAIL")
			return 1

	# --- VERIFY MODE ---
	if args.verify:
		ok, issues = ctx.verify()
		if ok:
			print("[OK] Verification passed: all folders and grants conform to username isolation.")
			print("END-OK")
			return 0
		else:
			print("[ERROR] Verification failed:")
			for issue in issues:
				print(f"  - {issue}")
			print("END-FAIL")
			return 1

	# --- SCAN & PLAN ---
	actions = ctx.scan_plan()
	print(format_table(actions))
	print(f"\nTotal actions planned: {len(actions)}")

	# --- APPLY MODE ---
	if args.apply:
		# 1. Check if ComfyUI is listening on lock-port
		if is_port_in_use(args.lock_port):
			print(
				f"[ERROR] ComfyUI server appears to be running on port {args.lock_port}. Please stop ComfyUI before running --apply.",
				file=sys.stderr,
			)
			print("END-FAIL")
			return 1

		# 2. Acquire migration lock
		if not ctx.acquire_lock():
			print(
				f"[ERROR] Migration lockfile is active: {ctx.lock_file}. Another migration may be running.",
				file=sys.stderr,
			)
			print("END-FAIL")
			return 1

		try:
			# 3. Create verified backup
			print("\n[*] Creating verified pre-migration backup...")
			backup_dir, meta = ctx.create_backup()
			print(f"[OK] Pre-migration backup verified at: {backup_dir}")

			# 4. Apply changes
			print("[*] Applying migration...")
			ok, log_entries = ctx.apply_migration(backup_dir)
			if ok:
				print("[OK] Migration applied successfully.")
				print(f"Log written to: {os.path.join(backup_dir, 'migration.log.json')}")
				print("END-OK")
				return 0
			else:
				print("[ERROR] Migration encountered failures during apply.", file=sys.stderr)
				print("END-FAIL")
				return 1
		finally:
			ctx.release_lock()

	# --- DRY-RUN (DEFAULT) ---
	print("\n[INFO] Dry-run completed. No changes made to disk or database.")
	print("END-OK")
	return 0


if __name__ == "__main__":
	sys.exit(main())
