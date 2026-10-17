"""
Copyright © 2026 Monster Spawned Studios
https://monsterspawned.studio/
All Rights Reserved.
"""

import os
import re
from argparse import ArgumentParser
from os import getcwd
from subprocess import CalledProcessError, run
from sys import stderr, stdout

# Safe node name: alphanumeric, dots, underscores, hyphens only (prevents injection)
_SAFE_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_COMFY_CLI_NON_INTERACTIVE_FLAGS = ["--skip-prompt", "--no-enable-telemetry"]

parser = ArgumentParser(description="Publish a ComfyUI node to the ComfyUI registry")
parser.add_argument(
	"-t",
	"--registry-access-token",
	type=str,
	default=None,
	help="The access token for the ComfyUI registry (or set REGISTRY_ACCESS_TOKEN)",
)
parser.add_argument(
	"-n",
	"--node-name",
	type=str,
	default=None,
	help="The name of the ComfyUI node to publish (or set COMFY_NODE_NAME, or use [project] name from pyproject.toml)",
)
parser.add_argument(
	"-p",
	"--node-path",
	type=str,
	default=None,
	help="The path to the ComfyUI node to publish (or set NODE_PATH or cwd)",
)
parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
parser.add_argument("-D", "--debug", action="store_true", help="Enable debug output")
parser.add_argument("-q", "--quiet", action="store_true", help="Enable quiet output")
parser.add_argument("-d", "--dry-run", action="store_true", help="Enable dry run output")

args = parser.parse_args()


def _get_project_name_from_pyproject(base_path: str) -> str | None:
	"""Read project name from pyproject.toml [project] section. Returns None on failure."""
	try:
		import tomllib
	except ImportError:
		return None
	path = os.path.join(base_path, "pyproject.toml")
	if not os.path.isfile(path):
		if args.debug or args.verbose:
			print(f"Pyproject.toml file not found in {base_path}.", file=stdout)
		return None
	if args.debug or args.verbose:
		print(f"Pyproject.toml file found in {base_path}.", file=stdout)
	try:
		with open(path, "rb") as f:
			data = tomllib.load(f)
		project = data.get("project")
		if isinstance(project, dict):
			name = project.get("name")
			if isinstance(name, str) and name.strip():
				if args.debug or args.verbose:
					print(f"Project name found in pyproject.toml: {name.strip()}.", file=stdout)
				return name.strip()
	except (OSError, ValueError) as e:
		print(f"Error reading project name from pyproject.toml: {e}", file=stderr)
		return None
	return None


def _is_safe_node_name(name: str) -> bool:
	"""Return True if name is safe (no injection risk)."""
	return bool(name and _SAFE_NODE_NAME_RE.match(name))


# Resolve from environment when not provided via CLI (e.g. GitHub Actions)
if args.registry_access_token is None:
	args.registry_access_token = os.environ.get("REGISTRY_ACCESS_TOKEN", "").strip() or None
if args.node_path is None:
	args.node_path = (
		os.environ.get("NODE_PATH", "").strip()
		or os.environ.get("GITHUB_WORKSPACE", "").strip()
		or getcwd()
	)
if args.node_name is None:
	args.node_name = os.environ.get("COMFY_NODE_NAME", "").strip() or None
if args.node_name is None:
	args.node_name = _get_project_name_from_pyproject(args.node_path)

if not args.registry_access_token:
	print(
		"Error: Registry access token required. Set REGISTRY_ACCESS_TOKEN or use --registry-access-token.",
		file=stderr,
	)
	exit(1)
if not args.node_name:
	print(
		"Error: Node name required. Set COMFY_NODE_NAME, use --node-name, or add [project] name in pyproject.toml.",
		file=stderr,
	)
	exit(1)
if not _is_safe_node_name(args.node_name):
	print(
		f"Error: Node name '{args.node_name}' contains invalid characters. Use only letters, numbers, dots, underscores, and hyphens.",
		file=stderr,
	)
	exit(1)


def _redact_token(token: str | None) -> str:
	"""Return a redacted token for diagnostic output (e.g. pat***abcd). Never logs the full secret."""
	if not token or not isinstance(token, str):
		return ""
	token = token.strip()
	if len(token) <= 7:
		return "***"
	return f"{token[:3]}***{token[-4:]}"


if args.debug:
	print(
		f"Publishing ComfyUI node {args.node_name} from {args.node_path} with registry access token '{_redact_token(args.registry_access_token)}'"
	)
if args.verbose:
	print(
		f"Publishing ComfyUI node {args.node_name} from {args.node_path} with valid registry access token..."
	)
if args.dry_run:
	print(
		f"Dry run enabled. No changes will be made to the ComfyUI node {args.node_name} from {args.node_path} with registry access token '{_redact_token(args.registry_access_token)}'"
	)


# Validate the node
def validate_node() -> bool:
	"""Validate the node using the ComfyUI CLI. Returns True on success, False on failure (non-fatal)."""
	print("Validating node...", file=stdout if args.verbose else stderr)
	try:
		result = run(
			["comfy", *_COMFY_CLI_NON_INTERACTIVE_FLAGS, "node", "validate"],
			check=False,
			stdout=None if args.quiet else stdout,
			stderr=None if args.quiet else stderr,
		)
		if result.returncode != 0:
			print(
				f"Warning: Node validation returned exit code {result.returncode}. Proceeding with publish.",
				file=stderr,
			)
			return False
		return True
	except (OSError, FileNotFoundError) as e:
		print(
			f"Warning: Node validation could not be completed: {e}. Proceeding with publish.",
			file=stderr,
		)
		return False


# Publish the node
def publish_node() -> bool:
	"""Publish the node to the ComfyUI Registry."""
	if args.dry_run:
		print("Dry run enabled. No changes will be made.", file=stdout)
		return True
	print("Publishing node...", file=stdout if args.verbose else stderr)
	try:
		return (
			run(
				[
					"comfy",
					*_COMFY_CLI_NON_INTERACTIVE_FLAGS,
					"node",
					"publish",
					"--token",
					args.registry_access_token,
				],
				check=True,
				stdout=None if args.quiet else stdout,
				stderr=None if args.quiet else stderr,
			).returncode
			== 0
		)
	except CalledProcessError as e:
		if args.debug or not args.quiet:
			print(f"Error publishing node: {e.returncode}", file=stderr)
		return False


# Main function
def main() -> int:
	"""Main function"""
	if not validate_node():
		print("Node validation failed (non-fatal). Proceeding with publish.", file=stderr)
	if not publish_node():
		print("Node publication failed.", file=stderr)
		return 1
	return 0


if __name__ == "__main__":
	result = main()
	if result == 0:
		if args.dry_run:  # Print dry-run success message to stderr
			print("Node publishing test executed successfully.", file=stderr)
		elif args.debug:  # Print detailed success message if debug is enabled
			print(
				f"Node published successfully using parameters: --registry-access-token '{_redact_token(args.registry_access_token)}' --node-name '{args.node_name}' --node-path '{args.node_path}' --verbose '{args.verbose}' --debug '{args.debug}' --quiet '{args.quiet}' --dry-run '{args.dry_run}'.",
				file=stdout,
			)
		elif not args.quiet:  # Print success message to stdout unless quiet
			print("Node published successfully.", file=stdout)
	exit(result)
