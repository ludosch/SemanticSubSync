#!/usr/bin/env bash
# PostToolUse hook (Edit|Write|Bash): README.md is the reference, README.fr.md its translation.
# After a change to the root README.md, remind Claude to carry it over to README.fr.md.
# Reads the hook JSON on stdin; no jq needed. Paths may use / or \ (Windows).
input=$(cat)
hit=0
# Edit / Write on the root README.md (not README.fr.md, not examples/*/README.md)
printf '%s' "$input" | grep -qE '"file_path"[[:space:]]*:[[:space:]]*"[^"]*SemanticSubSync[\/]+README\.md"' && hit=1
# Bash command that writes README.md: sed -i, a redirection, or a script that opens/writes it
if printf '%s' "$input" | grep -qE '"tool_name"[[:space:]]*:[[:space:]]*"Bash"' \
   && printf '%s' "$input" | grep -qE '(^|[^.a-zA-Z])README\.md' \
   && printf '%s' "$input" | grep -qE 'sed -i|>[[:space:]]*[^ ]*README\.md|\.write\(|open\(|mv |cp '; then
  hit=1
fi
if [ "$hit" = 1 ]; then
  cat <<'JSON'
{"systemMessage": "README.md changed: README.fr.md must be updated to match.",
 "hookSpecificOutput": {"hookEventName": "PostToolUse",
  "additionalContext": "README.md (English, reference) was just modified. Before finishing, apply the same change to README.fr.md (French translation, same structure and same facts), or tell the user explicitly why it is not needed."}}
JSON
fi
exit 0
