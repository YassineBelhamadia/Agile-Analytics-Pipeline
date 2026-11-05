# Workflow with Claude Code

How ETL sessions run in this repo. The commit plan they follow is [`etl-plan.md`](etl-plan.md). Standing project context is in [`CLAUDE.md`](../CLAUDE.md).

## Roles

- **Yassine** decides scope, confirms each session's plan, reviews the branch, merges and pushes.
- **Claude Code** implements one session at a time on its own branch. It commits each step after the step's gate passes, and never pushes.

## One session, one branch

1. **Before the session.** If the session's steps in `etl-plan.md` are still provisional, they are detailed (scope, files, gates, final messages) and confirmed first.
2. **Branch.** From an up-to-date `main`, Claude Code runs `git switch -c feat/etl-session-N`. All of that session's work goes on this branch.
3. **At most 4 commits per session.** One commit per plan step, in plan order.
4. **Gate first.** A step is committed only after its gate passes, and the gate's output goes in the session report. The commit message is the plan's message, verbatim.
5. **Never push.** Claude Code never pushes, amends, rebases, resets hard or switches to `main` ([permissions](#permissions)).
6. **Report.** At the end, a short report: what changed, gate results, assumptions, what was left out. Any use of a hook bypass is named in it.

### When a gate fails

- Claude Code may fix the problem and rerun the gate. If the gate fails a **second** time, it stops.
- It writes a short failure note: the step, the gate, the exact output, what was tried, and what it thinks is wrong. The note goes in `docs/etl/failure-notes/<step-id>.md`, left uncommitted, and is summarized in the report.
- It makes **no commit** for that step, and no "fix forward" commit (a later commit that patches a step committed with a failing gate).
- Work resumes only after Yassine decides.

### Review and merge

Yassine reviews the whole branch before merging:

```bash
git log -p main..feat/etl-session-N
```

Merging and pushing are done by hand. Merge messages written by git (`Merge branch ...`) pass the commit-msg hook's format check.

## Commit format

Conventional Commits:

```
<type>(<scope>): <summary>

<body, 1 to 3 lines>
```

- **Type** is one of `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `ci`. The scope is optional, lower case (e.g. `staging`, `dbt`).
- **Subject** is 72 characters or fewer.
- **Line 2** is blank. The body is 1–3 short lines.
- **Never** mention Claude, and never add `Co-Authored-By`, `Generated with` or `Claude-Session` lines.

Example, from step 2.2:

```
feat(staging): load issues, status transitions and sprint memberships

Derives transitions and memberships from changelog items and parses both timestamp formats to UTC.
```

## Hooks

Git hooks in `.githooks/` enforce the format and two branch rules. They are POSIX `sh` scripts with no dependencies.

### One-time setup (per clone)

`core.hooksPath` is local git config, which is not committed, so each clone needs this once:

```bash
git config core.hooksPath .githooks
git config --get core.hooksPath   # prints .githooks
```

Claude Code does not run this command, and its permissions deny `git config`.

### `commit-msg`

Rejects a message that:

- is not Conventional Commits with an allowed type;
- has a subject over 72 characters;
- has no blank line between the subject and the body;
- contains `Claude`, `Co-Authored-By`, `Generated with` or `Claude-Session`. The match ignores letter case, so a message cannot name `CLAUDE.md` either.

Comment lines and the `git commit -v` diff are ignored, as git strips them. Git's own merge messages skip the format checks but not the word check. The body's 1–3 line length is a convention, not enforced.

### `pre-commit`

- **On `main`:** refuses the commit. Bypass for Yassine's own use: `AAP_ALLOW_MAIN=1 git commit ...`.
- **Code without docs:** refuses a commit that stages files under `ingestion/`, `pipeline/`, `warehouse/` or `dbt/` with nothing staged under `docs/`. Bypass: `AAP_SKIP_DOCS_CHECK=1 git commit ...`. If Claude Code ever uses it, it says so in the report.

Both bypasses print a line when used. They are for Yassine only: Claude Code's permissions refuse any Bash command whose text contains either name. `git commit --no-verify` skips both hooks. Claude Code's permissions deny it.

### Testing the hooks

```bash
sh scripts/test_hooks.sh                 # from the repo root; expect "30 passed, 0 failed", exit 0
d=$(mktemp -d) && sh scripts/test_hooks.sh "$d"; rmdir "$d"   # sabotage: no hooks, 19 cases fail, exit 1
```

The script runs every case in a throwaway repo made with `mktemp`, deleted on exit. That repo ignores global and system git config. The cases cover:

- accepted and rejected messages for each commit-msg rule;
- commits on `main` with and without `AAP_ALLOW_MAIN=1`;
- the docs check for `ingestion/`, `pipeline/`, `warehouse/` and `dbt/`, with and without `AAP_SKIP_DOCS_CHECK=1`.

Run it after any change to `.githooks/`.

## Permissions

`.claude/settings.json` (committed) sets Claude Code's permission rules. Syntax: <https://code.claude.com/docs/en/permissions>. Deny rules are checked before allow rules, so a deny always wins.

- **Allowed without asking:** `git add`, `git commit`, `git status`, `git diff`, `git log`, `git switch -c`.
- **Denied:**
  - Publishing or rewriting history: `git push`, `git commit --amend`, `git reset --hard`, `git rebase`.
  - Skipping hooks or forcing: `--no-verify` and `git commit -n`, `--force*`, `-f` on any git command, `git branch -D`.
  - Leaving the feature branch: switching or checking out `main`, or `-`.
  - Getting around the rules: `git -c`, `git -C` and `git config`. The first two are documented ways around a `git push` deny, and `-c core.hooksPath=` or `git config` would turn the hooks off.
  - Deleting the Postgres volume: `docker compose ... down -v` / `--volumes`, `docker volume rm`, `docker volume prune`, `docker system prune --volumes`.
  - Hook bypasses: any command whose text contains `AAP_ALLOW_MAIN` or `AAP_SKIP_DOCS_CHECK` (`Bash(*AAP_ALLOW_MAIN*)`, `Bash(*AAP_SKIP_DOCS_CHECK*)`). This includes a leading assignment, `env`, `export`, and commands that only mention the name, such as a search for it. Checked live: `AAP_ALLOW_MAIN=1 true`, `true AAP_SKIP_DOCS_CHECK` and `env AAP_SKIP_DOCS_CHECK=1 true` were refused, and `AAP_ALLOW_MASTER=1 true` ran. `sh scripts/test_hooks.sh` is not affected: the names appear inside the script, not in the command Claude Code runs.

Limits, stated in the docs page above: these rules match the command text, so they cover the usual way of writing a command, not every way (for example `sh -c '...'` or a quoted `'push'`). They are a guardrail, not a security boundary. The hooks enforce the commit rules independently.

## Where things are recorded

- **Decisions,** with options considered and why: [`decisions.md`](decisions.md).
- **Per-stage docs** (purpose, contract, how to run and verify, limits): [`etl/`](etl/README.md). The pre-commit docs check exists so these pages change in the same commit as the code.
- **Plan and commit messages:** [`etl-plan.md`](etl-plan.md).
