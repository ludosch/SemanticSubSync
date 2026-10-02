#!/usr/bin/env bash
# PreToolUse hook (Bash): a commit that changes what users get (src/, Dockerfile, requirements.txt,
# integrations/) must carry its CHANGELOG.md entry under [Unreleased]. Otherwise the commit is
# refused with the reason; prefix the command with NO_CHANGELOG=1 for a change users do not see.
input=$(cat)
printf '%s' "$input" | grep -qE '"tool_name"[[:space:]]*:[[:space:]]*"Bash"' || exit 0
printf '%s' "$input" | grep -qE 'git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+commit' || exit 0
printf '%s' "$input" | grep -q 'NO_CHANGELOG=1' && exit 0
# only for commits in this repository (its path in the command, or the session's cwd inside it)
printf '%s' "$input" | grep -qE 'SemanticSubSync' || exit 0
cd "$(dirname "$0")/../.." || exit 0

files=$(git diff --cached --name-only)
# "git commit -a" / "-am" stages the tracked changes itself
printf '%s' "$input" | grep -qE 'commit[^"]*[[:space:]](-a|-am|-[a-z]*a[a-z]*|--all)([[:space:]]|")' \
  && files=$(printf '%s\n%s' "$files" "$(git diff --name-only)")
# "git add ... && git commit" in the same command: nothing is staged yet when this hook runs
printf '%s' "$input" | grep -qE 'git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+add' \
  && files=$(printf '%s\n%s\n%s' "$files" "$(git diff --name-only)" "$(git ls-files --others --exclude-standard)")
printf '%s\n' "$files" | grep -qE '^(src/|integrations/|Dockerfile$|requirements\.txt$)' || exit 0
printf '%s\n' "$files" | grep -qx 'CHANGELOG.md' && exit 0

cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
 "permissionDecisionReason": "This commit changes src/, integrations/, Dockerfile or requirements.txt without CHANGELOG.md. Add an entry under [Unreleased] (Added / Changed / Fixed, in the existing style) and stage it, or, if users cannot see the change (tests, CI, internal refactoring), run the same command prefixed with NO_CHANGELOG=1."}}
JSON
exit 0
