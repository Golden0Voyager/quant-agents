# Git Workflow Discipline

> **Required reading** before any push to `origin/*` or `upstream/*`. Defines hard limits on force-push and history rewrites; violations break collaborator trust irreversibly.

## TL;DR — three-tier remote / ref classification

| Tier | What it is | Force-push? | Recovery if force-pushed |
|------|-----------|-------------|-------------------------|
| 1️⃣ **Personal fork** | `origin` (e.g. `Golden0Voyager/Trading-Agents-A-Share`) | ✅ **Yes** — always use `--force-with-lease` instead of bare `--force` | Your own repo; reflog covers the next 90 days |
| 2️⃣ **Public upstream** | `upstream` (e.g. `TauricResearch/TradingAgents`) | 🚫 **Never, ever** | Catastrophic; breaks every downstream rebase across forks |
| 3️⃣ **Local-only refs** | local branches, backup tags, WIP branches | 🚫 No "primary" force; prefer `git reset` + `git tag` for backups | Backup tag deletes are reversible until reflog ages out (30–90 days; longer if any ref still points at the commit, shorter after `git gc`) |

## Tier 1 — `origin` (personal fork)

You own this remote. History rewrites are appropriate in narrow windows:

- Cleaning up commit messages / squashing fixups **before opening a PR**.
- Restructuring a feature branch into a cleaner commit series (the workflow `feat/data-missing-prevention` used).
- Reverting a polluted `origin/main` to a known-good commit (this is what happened on 2026-07-03: PR #13 was the recovery point).

**Mandatory:** always use `--force-with-lease` (never bare `--force`). Tag a backup first.

```bash
git fetch origin
git tag backup/main-pre-rewrite-$(date +%Y%m%d) origin/main
# ... do the local rewrite ...
git push origin main --force-with-lease   # origin-tier: allowed.
```

`--force-with-lease` aborts if the remote ref has moved since your last fetch. That single check is what makes this safe to automate.

## Tier 2 — `upstream` (public upstream) — ABSOLUTE FORCE-PUSH BAN

`upstream` is a shared source of truth. A single `--force` cascades through every other fork's `git pull --rebase` and PR chain.

```bash
# ❌ NEVER run any of these — and any combination thereof:
git push upstream main --force-with-lease
git push upstream main --force
git push --force upstream <any-branch>
git push upstream --mirror                  # rewrites every ref upstream-side
git push --force upstream --all             # pushes every local branch upstream
git push --force upstream --tags            # rewrites every annotated tag upstream
```

### Correct contribution back to upstream

```bash
git fetch upstream                                          # read-only sync
git checkout -b feat/your-contribution origin/main
# ... commit work ...
git push origin feat/your-contribution                       # push to YOUR fork
gh pr create --base upstream:<target-branch> --head origin:feat/your-contribution
```

If you disagree with upstream direction, **fork and propose** — never rewrite.

### Recovery if accidentally force-pushed upstream

1. **Stop.** Stop all further writes immediately.
2. Inform TauricResearch maintainers via issue/discussion. The faster they know, the higher the chance reflog is intact.
3. They can `git reflog` upstream to recover the lost commits — *only if* upstream's reflog has not been pruned.
4. Once `git gc --prune=now` runs upstream, the lost commits are gone forever.
5. Document the post-mortem in `docs/incidents/` (the directory does not exist yet — create on first use with `mkdir -p docs/incidents`). Capture what was lost, how it was noticed, and how to prevent recurrence.

## Tier 3 — local-only refs

Local branches and backup tags are bookkeeping. Force-push destroys history without a "destination" — there's nowhere for the old commits to go.

| Action | When it's safe |
|--------|----------------|
| `git branch -D <wip-branch>` | After merge + PR live for 1–2 weeks without regression reports |
| `git tag -d <backup-tag>` | Same window; reflog entries stay 30–90 days (longer while any ref points at the tagged commit, shorter after `git gc` for unreachable tags) |
| `git reset --hard <known-good>` | Only when REPLACING with a known-good sha; never to "throw away" work |
| `git branch -f <branch> <sha>` | Repointing a tracking branch, e.g. `git branch -f feat/x origin/feat/x` after fetch |
| `git checkout -B feat/x <sha>` | Recreating a branch FROM a sha, when you need a clean tip |

**Backup tags are NOT primary destinations for force-push.** If you find yourself tempted to "recover state by force-pushing to a backup tag", you are misusing the tier system — restore from reflog, or cherry-pick, or create a new branch.

## Consequence rule — `origin/main` rewrites invalidate shared feature branches

> ⚠️ **This is the foot-gun that triggered PR #13 / `feat/data-missing-prevention` rewrite.**

If anyone — collaborator or external fork — branched from `origin/main` and built `feat/their-work` off a commit you removed during a rewrite, **that branch is orphaned**. The next time they fetch:

```text
$ git fetch origin
From github.com:Golden0Voyager/Trading-Agents-A-Share
 ! [rejected]        feat/their-work -> feat/their-work (non-fast-forward)
```

They will need to `git rebase origin/main` before re-pushing. The rebase is clean unless their branch modified the same lines you removed.

### Mitigation for any `origin/main` rewrite

1. **Announce clearly** in CHANGELOG / team chat / the PR description.
2. **Audit open PRs**: `gh pr list --state open --base main` for any that branched off pre-rewrite `main`.
3. **Schedule availability**: prefer rewrites during low-collaboration windows (between releases).
4. **Take a backup tag** so the rewrite is reversible within the 30–90 day reflog window.

## Pre-flight checklist

Run before any push involving rewrite or force:

```bash
# 1. Confirm remote classification — what tier is each remote in?
git remote -v

# 2. Confirm location of HEAD — is this the commit you think it is?
git log --oneline -1

# 3. Confirm backup tag exists for any destructive write
git tag --list 'backup/*'

# 4. Confirm the ref you're pushing exists locally and matches what you fetched
git ls-remote <remote> <branch>

# 5. Push with the right flags
git push <remote> <branch> --force-with-lease   # OK only if remote is origin
# or, for non-force pushes:
git push <remote> <branch>
```

If any step surprises you, **stop and re-read this document** before continuing.

## Optional guardrail — `fpush` alias

Add to `~/.gitconfig`:

```ini
[alias]
  fpush = "!f() { \
    for arg in \"\$@\"; do \
      case \"\$arg\" in \
        *upstream*) \
          echo \"🚫 BLOCKED: refusing to force-push via upstream (\$arg)\"; \
          echo \"   use a PR through origin instead.\"; \
          return 1 ;; \
      esac; \
    done; \
    git push --force-with-lease \"\$@\"; \
  }; f"
```

The loop scans ALL positional arguments, not just `$1`, so `git fpush --all upstream` is also blocked. `--all`, `--mirror`, and `--tags` remain wide-blast-radius regardless of remote; treat them as exception-handled, never routine.

After installing, `git fpush origin main` works as expected; `git fpush upstream main` aborts with a clear message.

## Related

- **Project PR history** (#9 → #10 → #11 → #12 → #13):
  - **Pre-rewrite proper PRs (#9–#12)** — all merged cleanly through GitHub PR flow, no direct-to-`main` writes. PR #12 (`feat/smartmoney-db-local-financials`) is the most recent example.
  - **The slip** — six feature commits landed directly on `main` between PR #12 and PR #13, violating the Tier-1 convention above.
  - **PR #13** (`feat/data-missing-prevention`) — recovery PR: rebuilt the branch off `0e81563`, cherry-picked the 6 commits with a plan-doc commit on top, opened a PR, merged via `gh pr merge --merge --delete-branch` (merge commit `a42bc4a`). This document codifies the conventions that recovery made explicit.
- **Issue tracker**: see `docs/agents/issue-tracker.md` for how to file incidents if a rule here is accidentally violated.
- **Atomic commits**: see `atomic-commits.md` for how to split a large mixed working tree into self-contained, independently revertible commits (theme grouping, `git add -p` hunk splitting, dependency ordering, and the test-lag foot-gun).
- **Upstream etiquette**: <https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/allowing-changes-to-a-pull-request-branch-created-from-a-fork>
