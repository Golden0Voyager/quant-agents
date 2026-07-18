# Atomic Commits — splitting a large mixed change

> Companion to `git-workflow.md`. That doc governs *where* you push and what
> you may force-push. This one governs *how to structure the commits* when a
> working tree has accumulated many unrelated changes that should not land as
> one giant "wip" commit.

## When to use this

You have `N` modified/untracked files on a feature branch (or `main`) that
actually represent several independent themes — e.g. dependency trimming,
error-handling fixes, a removed vendor, schema additions, and test churn all
mixed together. Squashing them into one commit buries the logic and makes
review and `git bisect` useless.

Goal: land them as a sequence of **self-contained, independently revertible**
commits, each with a single clear theme.

## Workflow

### 1. Inventory the diffs before touching anything

```bash
git status --porcelain                 # what changed
git diff HEAD --stat                   # size per file
```

Group files into themes. A theme is a change that makes sense on its own and
whose tests would pass in isolation at that commit's tree.

### 2. Find the dependency ordering

Commits must be ordered so that each commit's tree is **internally
consistent** — every symbol it references already exists in an earlier commit
(or in the same commit).

Practical checks:
- If file A imports a name defined in file B, B must be committed **no later
  than** A.
- Adding a new field/parameter with a backward-compatible default can land
  before the code that uses it (callers still work). The reverse (callers
  using a not-yet-added symbol) breaks the build.
- Tests that assert on new behavior must ship in the **same** commit as the
  behavior, or in a later one — never earlier.

### 3. Stage whole files when the file is fully yours

If a file's entire change belongs to one theme, just `git add <file>`.
Because earlier commits already changed parts of the file, `git diff HEAD`
auto-narrows to only the uncommitted remainder, so a whole-file stage is safe
and won't resurrect already-committed hunks.

### 4. Split a file across themes with `git add -p`

When one file serves two themes, stage hunks interactively:

```bash
git add -p tradingagents/dataflows/interface.py
# at each hunk: y (stage) / n (skip) / s (split a hunk further)
```

Rules that bit us in practice:
- Count hunks first (`git diff HEAD -U1 -- <file> | grep -c '^@@'`) so you
  know how many `y`/`n` answers to give. Under-answering silently stages
  nothing and the commit comes out empty.
- Stage the **definition** and its **first use** together when they span the
  same file but different hunks — otherwise an intermediate commit imports a
  name that doesn't exist yet (here: `interface.py` imported
  `get_smartmoney_pledge_ratio` at module top, so that function had to land in
  the same commit, not a later one).

### 5. Commit per theme, then verify the tree

```bash
git commit -m "feat(dataflows): harden vendor error handling"
# after each commit:
uv run python -m pytest -m unit --no-header -q   # keep the tree green
```

A clean per-commit test run is the real proof of atomicity. If a commit fails
its own tests, the split is wrong — reorder or regroup.

### 6. Mark the commit correctly

Follow the repo convention (see `git-workflow.md` and existing history):
`type(scope): summary`. Keep the body to *why*, not *what* (the diff is the
what).

## Known foot-gun: tests lagging their feature

Sometimes a test file references behavior from two themes (e.g. it both
asserts the new fallback marker **and** the new path-lock). If you commit the
file with the later theme, an intermediate commit's tree will have the old
assertion and fail `pytest` — even though the final tree is green.

Two acceptable remedies:
- **Preferred:** split the test file with `git add -p` so each hunk rides with
  its feature.
- **Acceptable for human-reviewed PRs:** ship the whole test file with the
  later commit. CI validates the merged PR, not every intermediate tree. Note
  it so reviewers know `git bisect` at that intermediate commit will be red for
  that one file. (This is what happened at commit 8 ↔ 9 in the
  `feat/cleanup-and-akshare-refactor` branch — `tests/test_memory_log.py`
  carried a commit-8 fallback assertion but was committed alongside the
  commit-9 lock change.)

## Recovery during the split

If you accidentally fold two commits together (e.g. `git commit --amend` when
you meant to keep them separate):

```bash
git reset --soft HEAD~1     # undo the amend, keep changes staged
git restore --staged <files-to-defer>
git commit                  # re-create the first commit
git add <deferred> && git commit   # create the second
```

`--soft` preserves the working tree; you then re-stage into the right buckets.

## Checklist

- [ ] Themes identified and ordered by dependency
- [ ] Each file fully staged, or split with `git add -p` where needed
- [ ] No commit imports a symbol defined in a later commit
- [ ] `pytest -m unit` green after each commit
- [ ] Commit messages follow `type(scope):` convention
- [ ] Any test-file lag noted for reviewers

## Related

- **`git-workflow.md`** — where you may push and the force-push tiers.
- **Real example** — branch `feat/cleanup-and-akshare-refactor` (PR #34): 11
  atomic commits split from ~70 mixed files; the `interface.py` import-closure
  and test-lag cases above are both drawn from it.
