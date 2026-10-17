# Install other custom-node deps (experimental)

## What it does

When enabled, MSS-Login may plan/auto-install dependencies declared by other custom nodes (subject to safety checks and platform backend detection).

## How to enable

- Master + `experimental.install_other_nodes_deps: true` or `EXPERIMENTAL_INSTALL_OTHER_NODES_DEPS=1`

## When it is required

Optional. Leave off unless you intentionally want automatic dependency installation for sibling custom nodes. Prefer ComfyUI Manager for normal installs.

## See also

- [Experimental Settings hub](index.md)
