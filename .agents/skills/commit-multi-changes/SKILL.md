---
name: commit-multi-changes
description: Splits large working-tree changes into topic-scoped multi-stage git commits. Use when the user invokes /commit-multi-changes, or asks to commit significant changes (about 500+ lines) as multiple digestible commits rather than one dump.
---

# Commit multi-changes

Invoke as **`/commit-multi-changes`**. Agent-agnostic project skill under `.agents/skills/`.

Splits significant working-tree changes into small, topic-scoped commits. Invoking this skill (or an explicit multi-commit ask) **is** permission to commit. Completing implementation work alone is **not**. Never push unless the user also asks to push.

## Hard constraints

- No `git config` changes; no force-push; no interactive git (`-i`)
- No amend unless the user explicitly asked and amend safety rules allow
- Never log `MSS_SSH_PASS` or private key material
- Exclude secrets (`.env`, credentials, private keys); warn and skip
- Do not stage `.venv`, `node_modules`, or other ignored junk
- Keep stages digestible; avoid one mega-commit for 500+ lines

Follow [AGENTS.md](../../../AGENTS.md) commit signing: try SSH signing with `MSS_SSH_PRIV_KEY` / `MSS_SSH_PASS` when available; else system SSH agent; else unsigned commit.

## Workflow

### Stage 0 — Inventory (read-only)

Run in parallel:

```bash
git status
git diff
git diff --cached
git diff --stat
git diff --cached --stat
git log -5 --oneline
```

Estimate **total changed lines** (insertions + deletions across staged, unstaged, and untracked files that will be included). Use `git diff --numstat` and `git diff --cached --numstat`; for untracked files, count lines when they will be added.

- **In scope:** ~500+ total changed lines, or clearly multi-topic sets under that threshold
- **Under ~500 and single-topic:** say so and offer a single commit instead of forcing many stages

### Stage 1 — Propose plan (wait for approval)

Group into topic-scoped commits. Typical buckets for this repo:

| Prefix | Use for |
|--------|---------|
| `fix(...)` / `feat(...)` | Behavior by area (auth UI, updater API, permissions) |
| `test(...)` | New/updated tests |
| `docs(...)` | Docs, README, changelog notes |
| `ci(...)` / `chore(ci)` | `.github` / `.gitea` workflows |
| `chore(...)` | Lockfiles, defaults, misc |

Grouping rules:

- Prefer independent stages that leave the tree sensible when possible
- Never mix unrelated areas in one commit
- Order dependencies first (e.g. permission helper before UI that calls it)

Present a numbered plan. For each stage: **files**, **commit title**, **one-line why**.

**Stop and wait** for user approval (or edits) before any `git add` / `git commit`.

### Stage 2 — Execute sequentially

For each approved stage:

1. `git add` only that stage’s pathspecs (never `git add -i` or blanket `git add .` unless the plan is exactly “all remaining files for this stage”)
2. Commit with a HEREDOC message (conventional, why-focused):

```bash
git commit -m "$(cat <<'EOF'
feat(area): short why-focused subject

EOF
)"
```

3. On hook failure: fix the issue and create a **new** commit (do not amend unless explicitly allowed)
4. After all stages: `git status` and list commit subjects/SHAs — **do not push**

## Checklist

```
Task progress:
- [ ] Stage 0: inventory + line estimate
- [ ] Stage 1: numbered plan shown; user approved
- [ ] Stage 2: each topic committed
- [ ] Final status summarized; push skipped unless asked
```

## Additional resources

- For concrete staging examples, see [examples.md](examples.md)
