"""
Single resolver for experimental model isolation: plain client names ↔ storage paths.

Resolution never fabricates a UUID prefix. Own isolation files shadow shared names.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

try:
	from ..constants import experimental_model_isolation_enabled
	from .model_isolation import (
		isolation_folder_root,
		isolation_user_folder,
		sanitize_user_segment,
		user_isolation_candidate_segments,
	)
	from .model_visibility_policy import normalize_model_name
except (ImportError, ValueError):
	from constants import experimental_model_isolation_enabled  # type: ignore
	from utils.model_isolation import (
		isolation_folder_root,
		isolation_user_folder,
		sanitize_user_segment,
		user_isolation_candidate_segments,
	)
	from utils.model_visibility_policy import normalize_model_name

# ComfyUI legacy folder aliases (bidirectional)
_FOLDER_ALIASES: dict[str, str] = {
	"unet": "diffusion_models",
	"diffusion_models": "unet",
	"clip": "text_encoders",
	"text_encoders": "clip",
}

_UUID_RE = re.compile(
	r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

Origin = Literal["user", "shared", "granted"]


@dataclass(frozen=True)
class ResolvedName:
	storage_name: str
	origin: Origin
	folder: str


def normalize_folder(folder: str) -> str:
	"""Map legacy folder names to the canonical ComfyUI folder when known."""
	f = (folder or "").strip()
	if f == "unet":
		return "diffusion_models"
	if f == "clip":
		return "text_encoders"
	return f


def folder_aliases(folder: str) -> list[str]:
	"""Return folder plus its legacy alias (if any), for dual-read lookups."""
	f = (folder or "").strip()
	if not f:
		return []
	alias = _FOLDER_ALIASES.get(f)
	if alias:
		return [f, alias]
	return [f]


def is_uuid_segment(segment: str) -> bool:
	return bool(segment and _UUID_RE.match(segment.strip()))


def _norm_path(name: str) -> str:
	return (name or "").replace("\\", "/").strip()


def _safe_relpath_under(root: str, candidate: str) -> str | None:
	"""
	Return relative path of candidate under root if it is a regular file contained
	in root after realpath resolution. Symlinks that escape root → None.
	"""
	if not root or not candidate:
		return None
	try:
		root_real = os.path.realpath(root)
		# Resolve through symlinks; reject if target escapes root
		cand_real = os.path.realpath(candidate)
		if not os.path.isfile(cand_real):
			return None
		# Also reject if the path itself is a symlink whose immediate target escapes
		# (realpath already follows; containment check is the gate)
		if cand_real != root_real and not cand_real.startswith(root_real + os.sep):
			return None
		rel = os.path.relpath(cand_real, root_real).replace("\\", "/")
		if not rel or rel.startswith(".."):
			return None
		return rel
	except OSError:
		return None


def _isolation_roots_for_folder(folder: str) -> set[str]:
	roots: set[str] = set()
	for f in folder_aliases(folder):
		root = isolation_folder_root(f)
		try:
			roots.add(os.path.realpath(root))
		except OSError:
			roots.add(os.path.abspath(root))
	return roots


def _get_folder_path_list(folder: str) -> list[str]:
	try:
		import folder_paths  # pyright: ignore[reportMissingImports]

		paths = folder_paths.get_folder_paths(folder)
		if isinstance(paths, list):
			return [p for p in paths if isinstance(p, str) and p]
	except Exception:
		pass
	return []


def _shared_roots(folder: str) -> list[str]:
	"""Non-isolation roots registered for folder (and alias)."""
	iso = _isolation_roots_for_folder(folder)
	out: list[str] = []
	seen: set[str] = set()
	for f in folder_aliases(folder) or [(folder or "").strip()]:
		if not f:
			continue
		for p in _get_folder_path_list(f):
			try:
				real = os.path.realpath(p)
			except OSError:
				real = os.path.abspath(p)
			if real in iso:
				continue
			if real in seen:
				continue
			seen.add(real)
			out.append(p)
	return out


def _file_exists_under_shared(folder: str, rel_name: str) -> bool:
	rel = _norm_path(rel_name)
	if not rel or rel.startswith("/") or ".." in rel.split("/"):
		return False
	for root in _shared_roots(folder):
		candidate = os.path.join(root, *rel.split("/"))
		try:
			root_real = os.path.realpath(root)
			cand_real = os.path.realpath(candidate)
			if not os.path.isfile(cand_real):
				continue
			if cand_real == root_real or cand_real.startswith(root_real + os.sep):
				return True
		except OSError:
			continue
	return False


def _own_isolation_match(
	folder: str, user_id: str | None, rel_name: str, username: str | None = None
) -> tuple[str, str] | None:
	"""If own isolation file exists safely, return (rel_path_under_user_dir, matched_user_segment)."""
	rel = _norm_path(rel_name)
	if not rel or rel.startswith("/") or ".." in rel.split("/"):
		return None
	segments = user_isolation_candidate_segments(user_id, username)
	for f in folder_aliases(folder) or [(folder or "").strip()]:
		if not f:
			continue
		root = isolation_folder_root(f)
		for seg in segments:
			user_dir = os.path.join(root, seg)
			candidate = os.path.join(user_dir, *rel.split("/"))
			safe = _safe_relpath_under(user_dir, candidate)
			if safe is not None:
				return safe, seg
	return None


def _own_isolation_rel(
	folder: str, user_id: str | None, rel_name: str, username: str | None = None
) -> str | None:
	"""If own isolation file exists safely, return relative path under user dir."""
	match = _own_isolation_match(folder, user_id, rel_name, username)
	return match[0] if match is not None else None


def _folder_matches(grant_folder: str, folder: str) -> bool:
	gf = (grant_folder or "").strip()
	f = (folder or "").strip()
	if not gf or not f:
		return True
	if gf == f:
		return True
	if normalize_folder(gf) == normalize_folder(f):
		return True
	if _FOLDER_ALIASES.get(gf) == f or _FOLDER_ALIASES.get(f) == gf:
		return True
	return False


def _grant_storage_for_suffix(
	folder: str, display_name: str, grants: list[dict] | None
) -> str | None:
	norm = normalize_model_name(display_name)
	for g in grants or []:
		if not _folder_matches(g.get("folder") or "", folder):
			continue
		item = _norm_path(g.get("item_name") or "")
		if not item:
			continue
		if normalize_model_name(item) == norm:
			return item
		if "/" in item:
			suffix = item.split("/", 1)[1]
			if normalize_model_name(suffix) == norm:
				return item
	return None


def foreign_uuid_prefix(name: str, user_id: str | None) -> bool:
	"""
	True when name starts with a UUID segment that is not the current user's segment.
	Used by middleware to reject with a generic MODEL_NOT_ALLOWED (no existence leak).
	"""
	path = _norm_path(name)
	if "/" not in path:
		return False
	seg, _rest = path.split("/", 1)
	if not is_uuid_segment(seg):
		return False
	candidate_segments = [s.lower() for s in user_isolation_candidate_segments(user_id)]
	return seg.lower() not in candidate_segments


def resolve_model_name(
	folder: str,
	name: str,
	user_id: str | None,
	*,
	grants: list[dict] | None = None,
	view_all: bool = False,
	username: str | None = None,
) -> ResolvedName | None:
	"""
	Map a client-facing model name to the storage name ComfyUI must load.

	Order for plain names: own isolation → grant suffix → shared library.
	Never fabricates a UUID prefix. Foreign UUID prefixes return None
	(callers should emit a generic denial without probing disk).
	"""
	_ = view_all  # list/build uses view_all; resolve treats grants the same for all
	if not experimental_model_isolation_enabled():
		n = _norm_path(name)
		if not n:
			return None
		return ResolvedName(storage_name=n, origin="shared", folder=normalize_folder(folder))

	folder_norm = normalize_folder(folder)
	raw = _norm_path(name)
	if not raw:
		return None

	candidate_segments = [s.lower() for s in user_isolation_candidate_segments(user_id, username)]

	# Prefixed inbound
	if "/" in raw:
		seg, rest = raw.split("/", 1)
		if seg.lower() in candidate_segments:
			# Own prefix (either UUID or username): resolve rest as own file
			match = _own_isolation_match(folder_norm, user_id, rest, username=username)
			if match is not None:
				own_rel, actual_seg = match
				return ResolvedName(
					storage_name=f"{actual_seg}/{own_rel}", origin="user", folder=folder_norm
				)
			# Own prefix but missing → None (generic miss)
			return None
		if is_uuid_segment(seg):
			# Foreign UUID prefix: denial
			return None
		# Non-UUID subfolder (e.g. SDXL/foo.safetensors): check own isolation first!
		match = _own_isolation_match(folder_norm, user_id, raw, username=username)
		if match is not None:
			own_rel, actual_seg = match
			return ResolvedName(
				storage_name=f"{actual_seg}/{own_rel}", origin="user", folder=folder_norm
			)
		grant = _grant_storage_for_suffix(folder_norm, raw, grants)
		if grant is not None:
			return ResolvedName(storage_name=grant, origin="granted", folder=folder_norm)
		if _file_exists_under_shared(folder_norm, raw):
			return ResolvedName(storage_name=raw, origin="shared", folder=folder_norm)
		return None

	# Plain name
	match = _own_isolation_match(folder_norm, user_id, raw, username=username)
	if match is not None:
		own_rel, actual_seg = match
		return ResolvedName(
			storage_name=f"{actual_seg}/{own_rel}", origin="user", folder=folder_norm
		)

	grant = _grant_storage_for_suffix(folder_norm, raw, grants)
	if grant is not None:
		return ResolvedName(storage_name=grant, origin="granted", folder=folder_norm)

	if _file_exists_under_shared(folder_norm, raw):
		return ResolvedName(storage_name=raw, origin="shared", folder=folder_norm)

	return None


def _walk_own_display_names(folder: str, user_id: str | None) -> list[str]:
	out: list[str] = []
	seen: set[str] = set()
	segments = user_isolation_candidate_segments(user_id)
	for f in folder_aliases(folder) or [(folder or "").strip()]:
		if not f:
			continue
		root = isolation_folder_root(f)
		for seg in segments:
			user_dir = os.path.join(root, seg)
			if not os.path.isdir(user_dir):
				continue
			try:
				for dirpath, _dirnames, filenames in os.walk(user_dir):
					for fn in filenames:
						if not fn or fn.startswith("."):
							continue
						full = os.path.join(dirpath, fn)
						rel = _safe_relpath_under(user_dir, full)
						if rel is None:
							continue
						key = rel.lower()
						if key in seen:
							continue
						seen.add(key)
						out.append(rel)
			except OSError:
				continue
	return out


def _list_shared_display_names(folder: str) -> list[str]:
	"""Relative filenames under shared (non-isolation) roots."""
	out: list[str] = []
	seen: set[str] = set()
	for root in _shared_roots(folder):
		if not os.path.isdir(root):
			continue
		try:
			for dirpath, _dirnames, filenames in os.walk(root):
				for fn in filenames:
					if not fn or fn.startswith("."):
						continue
					full = os.path.join(dirpath, fn)
					rel = _safe_relpath_under(root, full)
					if rel is None:
						continue
					key = normalize_model_name(rel)
					if key in seen:
						continue
					seen.add(key)
					out.append(rel)
		except OSError:
			continue
	return out


def _grant_display_suffixes(
	folder: str, grants: list[dict] | None, user_id: str | None
) -> list[str]:
	"""Plain suffixes for grants in this folder."""
	_ = user_id
	out: list[str] = []
	seen: set[str] = set()
	for g in grants or []:
		if not _folder_matches(g.get("folder") or "", folder):
			continue
		item = _norm_path(g.get("item_name") or "")
		if not item:
			continue
		display = item.split("/", 1)[1] if "/" in item else item
		key = normalize_model_name(display)
		if key in seen:
			continue
		seen.add(key)
		out.append(display)
	return out


def list_display_names(
	folder: str, user_id: str | None, *, grants: list[dict] | None = None, view_all: bool = False
) -> list[str]:
	"""
	User-facing model names for a folder: shared ∪ own relpaths ∪ granted suffixes.

	Own files shadow shared on the same relative name. Never includes other users'
	UUID prefixes. view_all includes the full shared library; non-view_all only
	includes shared names that appear as grants (flat or suffix) plus own + grants.
	"""
	if not experimental_model_isolation_enabled():
		try:
			from .isolation_folder_hooks import get_raw_filename_list

			return list(get_raw_filename_list(folder))
		except Exception:
			return []

	own = _walk_own_display_names(folder, user_id)
	granted = _grant_display_suffixes(folder, grants, user_id)

	if view_all:
		shared = _list_shared_display_names(folder)
	else:
		shared_all = _list_shared_display_names(folder)
		allowed_norms: set[str] = set()
		for g in grants or []:
			if not _folder_matches(g.get("folder") or "", folder):
				continue
			item = _norm_path(g.get("item_name") or "")
			if not item:
				continue
			allowed_norms.add(normalize_model_name(item))
			if "/" in item:
				allowed_norms.add(normalize_model_name(item.split("/", 1)[1]))
		shared = [s for s in shared_all if normalize_model_name(s) in allowed_norms]

	# Own first (shadows shared), then granted, then shared
	out: list[str] = []
	seen: set[str] = set()
	for name in own + granted + shared:
		key = normalize_model_name(name)
		if key in seen:
			continue
		seen.add(key)
		out.append(name)
	return out
