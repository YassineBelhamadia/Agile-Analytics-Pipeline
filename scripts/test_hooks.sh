#!/bin/sh
# Tests .githooks/commit-msg and .githooks/pre-commit in a throwaway repo.
#
# Run from the repo root:
#
#     sh scripts/test_hooks.sh               # test .githooks
#     sh scripts/test_hooks.sh <hooks_dir>   # test another hooks directory
#
# Sabotage check: point it at an empty directory and every "reject" case
# must fail (exit 1):
#
#     d=$(mktemp -d) && sh scripts/test_hooks.sh "$d"; rmdir "$d"
#
# The throwaway repo is created with mktemp and deleted on exit. It ignores
# the user's global and system git config, so signing settings or a global
# core.hooksPath cannot change the result. Set VERBOSE=1 to see hook output.
# Exits 0 only if every case behaves as expected.

if [ ! -d .githooks ] || [ ! -d .git ]; then
    echo "test_hooks.sh: run from the repo root (no .githooks/ or .git here)." >&2
    exit 2
fi
HOOKS=$(cd "${1:-.githooks}" && pwd) || exit 2

WORK=$(mktemp -d) || exit 2
trap 'rm -rf "$WORK"' EXIT INT TERM
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
cd "$WORK" || exit 2
git init -q -b main
git config user.name tester
git config user.email tester@example.invalid
git config core.hooksPath "$HOOKS"

pass=0
fail=0

# check <accept|reject> <label> <command...>: run a command, compare the outcome.
check() {
    expect=$1
    label=$2
    shift 2
    if out=$("$@" 2>&1); then got=accept; else got=reject; fi
    if [ "$got" = "$expect" ]; then
        pass=$((pass + 1))
        result=PASS
    else
        fail=$((fail + 1))
        result=FAIL
    fi
    printf '%-4s expected %-6s got %-6s %s\n' "$result" "$expect" "$got" "$label"
    if [ "$result" = FAIL ] || [ -n "${VERBOSE:-}" ]; then
        printf '%s\n' "$out" | sed 's/^/       | /'
    fi
    git reset -q 2>/dev/null
    return 0
}

n=0
# commit_file <message> <file...>: change and stage each file, then commit.
commit_file() {
    msg=$1
    shift
    for f in "$@"; do
        n=$((n + 1))
        mkdir -p "$(dirname "$f")"
        echo "$n" >> "$f"
        git add "$f"
    done
    printf '%s\n' "$msg" | git commit -q -F -
}

# The same, with one environment variable set for the commit.
commit_with_env() {
    assignment=$1
    shift
    (export "$assignment"; commit_file "$@")
}

BODY='

Body.'

echo "--- pre-commit: main branch"
check reject "commit on main" commit_file "chore: seed$BODY" README.md
check accept "commit on main with AAP_ALLOW_MAIN=1" commit_with_env AAP_ALLOW_MAIN=1 "chore: seed repo$BODY" README.md
check reject "AAP_ALLOW_MAIN=yes is not the bypass" commit_with_env AAP_ALLOW_MAIN=yes "chore: second$BODY" README.md
git switch -q -c feat/etl-session-2

echo "--- commit-msg: accepted"
check accept "good message with scope and body" commit_file "feat(staging): add staging schema and loader for reference entities

Loads teams, users, boards, sprints and capacity with typed columns." notes.txt
check accept "subject only" commit_file "ci: add GitHub Actions workflow" notes.txt
check accept "breaking-change marker" commit_file "refactor(staging)!: rename columns$BODY" notes.txt
check accept "editor-style comments and scissors are ignored" commit_file "docs: explain hooks

Body line.
# Please enter the commit message
# ------------------------ >8 ------------------------
diff mentioning Claude" notes.txt
check accept "subject of exactly 72 characters" commit_file "docs: $(printf '%066d' 0)" notes.txt
check accept "git merge message skips the format check" commit_file "Merge branch 'feat/x' into feat/etl-session-2" notes.txt

echo "--- commit-msg: rejected"
check reject "no type" commit_file "added the staging loader" notes.txt
check reject "type not allowed (style)" commit_file "style: reformat" notes.txt
check reject "capitalised type" commit_file "Feat: add loader" notes.txt
check reject "no space after the colon" commit_file "feat:add loader" notes.txt
check reject "subject of 73 characters" commit_file "docs: $(printf '%067d' 0)" notes.txt
check reject "no blank line before the body" commit_file "feat(staging): add loader
Loads teams." notes.txt
check reject "Co-Authored-By Claude trailer" commit_file "feat(staging): add loader$BODY

Co-Authored-By: Claude <noreply@anthropic.com>" notes.txt
check reject "co-authored-by, other name, lower case" commit_file "fix: x$BODY

co-authored-by: Someone <someone@example.invalid>" notes.txt
check reject "Generated with line" commit_file "docs: x

Generated with a tool." notes.txt
check reject "Claude-Session trailer" commit_file "docs: x$BODY

Claude-Session: abc123" notes.txt
check reject "lower-case claude in the subject" commit_file "docs: notes from claude" notes.txt
check reject "merge message still checked for forbidden words" commit_file "Merge branch 'feat/x'

Co-Authored-By: Claude" notes.txt

echo "--- pre-commit: docs check"
check reject "ingestion/ change without docs" commit_file "feat(staging): add loader$BODY" ingestion/load_staging.py
check reject "warehouse/ change without docs" commit_file "chore(warehouse): tweak$BODY" warehouse/docker-compose.yml
check reject "dbt/ change without docs" commit_file "feat(dbt): add model$BODY" dbt/models/x.sql
check accept "ingestion/ change with docs/ staged" commit_file "feat(staging): add loader$BODY" ingestion/load_staging.py docs/etl/02-staging.md
check accept "change outside the guarded folders" commit_file "test(staging): add test$BODY" tests/test_x.py
check accept "ingestion/ without docs, AAP_SKIP_DOCS_CHECK=1" commit_with_env AAP_SKIP_DOCS_CHECK=1 "fix(staging): x$BODY" ingestion/load_staging.py
check reject "deleting an ingestion/ file without docs" sh -c "git rm -q ingestion/load_staging.py && git commit -q -m 'refactor: remove loader$BODY'"

echo "--- $pass passed, $fail failed"
[ "$fail" -eq 0 ]
