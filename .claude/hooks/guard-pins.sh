#!/bin/sh
# PreToolUse guard: block edits that touch an exact dependency pin in pyproject.toml.
#
# The pins for graphrag / llama-index / docling / sentence-transformers /
# FlagEmbedding / rapidocr / litellm are the interchange compatibility contract
# (.claude/rules/architecture.md) — recorded in the thesis and in every export
# manifest. Changing one is a deliberate event, never a tweak made in passing
# while fixing something else.
#
# Reads the tool call as JSON on stdin. Exit 2 blocks the call and shows stderr
# to Claude; exit 0 lets it through.

input=$(cat)
path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')

case "$path" in
	*/pyproject.toml | pyproject.toml) ;;
	*) exit 0 ;;
esac

# Only guard edits whose content carries an exact pin. Touching [tool.ruff],
# pytest markers, or adding a >= dependency is ordinary work and stays unblocked.
printf '%s' "$input" |
	jq -r '[.tool_input.old_string, .tool_input.new_string, .tool_input.content]
	       | map(select(. != null)) | join("\n")' |
	grep -q '==' || exit 0

cat >&2 <<'EOF'
Blocked: this edit touches an exact dependency pin in pyproject.toml.

Those pins are the interchange compatibility contract (.claude/rules/architecture.md):
they are recorded in the thesis and in every export manifest, and changing one means a
full re-index migration + manifest bump — not a tweak.

If the pin change is genuinely intended, record it with /decision first, or leave it
for Paul. Otherwise, make the edit without altering any `==` line.
EOF
exit 2
