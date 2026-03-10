"""
Copyright © 2026 Monster Spawned Studios
https://monsterspawned.studio/
All Rights Reserved.
"""

from argparse import ArgumentParser
from os import getcwd
from subprocess import CalledProcessError, run
from sys import stderr, stdout

parser = ArgumentParser(
    description="Publish a ComfyUI node to the ComfyUI registry"
)
parser.add_argument(
    "-t",
    "--registry-access-token",
    type=str,
    required=True,
    help="The access token for the ComfyUI registry",
)
parser.add_argument(
    "-n",
    "--node-name",
    type=str,
    required=True,
    help="The name of the ComfyUI node to publish",
)
parser.add_argument(
    "-p",
    "--node-path",
    type=str,
    required=False,
    default=getcwd(),  # Default to the current working directory
    help="The path to the ComfyUI node to publish",
)
parser.add_argument(
    "-v",
    "--verbose",
    action="store_true",
    help="Enable verbose output",
)
parser.add_argument(
    "-D",
    "--debug",
    action="store_true",
    help="Enable debug output",
)
parser.add_argument(
    "-q",
    "--quiet",
    action="store_true",
    help="Enable quiet output",
)
parser.add_argument(
    "-d",
    "--dry-run",
    action="store_true",
    help="Enable dry run output",
)

args = parser.parse_args()

if (
    args.debug
    and args.registry_access_token is not None
    and args.registry_access_token.startswith("pat")
):
    print(
        f"Publishing ComfyUI node {args.node_name} from {args.node_path} with registry access token '{args.registry_access_token}'"
    )
if (
    args.verbose
    and args.registry_access_token is not None
    and args.registry_access_token.startswith("pat")
):
    print(
        f"Publishing ComfyUI node {args.node_name} from {args.node_path} with valid registry access token..."
    )
if (
    args.dry_run
    and args.registry_access_token is not None
    and args.registry_access_token.startswith("pat")
):
    print(
        f"Dry run enabled. No changes will be made to the ComfyUI node {args.node_name} from {args.node_path} with registry access token '{args.registry_access_token}'"
    )


# Validate the node
def validate_node() -> bool:
    """Validate the node using the ComfyUI CLI"""
    print("Validating node...", file=stdout if args.verbose else stderr)
    try:
        return (
            run(
                ["comfy", "node", "validate"],
                check=True,
                stdout=None if args.quiet else stdout,
                stderr=None if args.quiet else stderr,
            ).returncode == 0
        )
    except CalledProcessError as e:
        print(f"Error validating node: {e}", file=stderr)
        return False


# Publish the node
def publish_node() -> bool:
    """Publish the node to the ComfyUI Registry"""
    if args.dry_run:
        print("Dry run enabled. No changes will be made.", file=stdout)
        return 0
    print("Publishing node...", file=stdout if args.verbose else stderr)
    try:
        return run(
            ["comfy", "node", "publish", "--token",
                f"{args.registry_access_token}"],
            check=True,
            stdout=None if args.quiet else stdout,
            stderr=None if args.quiet else stderr,
        ).returncode == 0
    except CalledProcessError as e:
        if args.debug:
            print(f"Error publishing node: {e.returncode}", file=stderr)
        elif not args.quiet:
            print(f"Error publishing node: {e.returncode}", file=stderr)
        return False


# Main function
def main() -> int:
    """Main function"""
    if not validate_node():
        print("Node validation failed.", file=stderr)
        return 1
    if not publish_node():
        print("Node publication failed.", file=stderr)
        return 1
    return 0


if __name__ == "__main__":
    if not args.quiet:  # Print success message to stdout if quiet is not enabled
        print("Node published successfully.", file=stdout)
    elif args.debug:  # Print success message to stdout if debug is enabled
        print(
            f"Node published successfully using paramaters: --registry-access-token '{args.registry_access_token}' --node-name '{args.node_name}' --node-path '{args.node_path}' --verbose '{args.verbose}' --debug '{args.debug}' --quiet '{args.quiet}' --dry-run '{args.dry_run}'.", file=stdout
        )
    elif args.dry_run:  # Print success message to stderr if dry run is not enabled
        print("Node publishing test executed successfully.", file=stderr)
    else:  # Print success message to stdout if quiet, debug, and dry run are enabled
        print("Node published successfully.", file=stdout)
    exit(main())
