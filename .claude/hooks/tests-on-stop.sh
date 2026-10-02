#!/usr/bin/env bash
# Stop hook: when Claude is about to hand back, run the unit tests if Python code changed since the
# last green run. A failure blocks the stop once (Claude must fix it); a second failure in the same
# turn is only shown, to avoid a loop. The fingerprint of the last green run lives in .git/.
input=$(cat)
cd "$(dirname "$0")/../.." || exit 0
stamp_file=.git/claude-tests-ok
stamp=$( { git rev-parse HEAD
           git diff HEAD -- '*.py' pyproject.toml uv.lock
           git ls-files --others --exclude-standard -- '*.py' | while read -r f; do cat -- "$f"; done
         } | sha1sum | cut -d' ' -f1)
[ -f "$stamp_file" ] && [ "$(cat "$stamp_file")" = "$stamp" ] && exit 0

out=$(mise x -- uv run pytest -q -x 2>&1 < /dev/null)
if [ $? -eq 0 ]; then
  echo "$stamp" > "$stamp_file"
  exit 0
fi
# the last lines as a JSON string, without Python (it may be what fails)
tail=$(printf '%s\n' "$out" | tail -40 | tr -d '\r' \
       | awk 'BEGIN{ORS=""} {gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); gsub(/\t/,"\\t"); gsub(/[\001-\037]/,""); print $0 "\\n"}')
if printf '%s' "$input" | grep -qE '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
  printf '{"systemMessage": "Unit tests still failing (pytest -q -x):\\n%s"}\n' "$tail"
else
  printf '{"decision": "block", "reason": "Unit tests fail after your changes; fix them before finishing (mise x -- uv run pytest -q -x).\\n\\n%s"}\n' "$tail"
fi
exit 0
