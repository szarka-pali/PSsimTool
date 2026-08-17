#!/usr/bin/env bash
# PostToolUse hook pre Edit|Write.
#
# Načo to je: deterministická záruba. Instrukcia v CLAUDE.md typu "vždy naformátuj"
# je len rada, ktorú model niekedy vynechá. Hook sa spustí VŽDY.
#
# Vstup: JSON na stdin, obsahuje .tool_input.file_path
# Výstup: exit 0 = pokračuj. Text na stdout ide Claudovi ako kontext.
# Exit 2 by akciu zablokoval, ale PostToolUse beží až PO editácii,
# takže tu len reportujeme nálezy, ktoré má Claude opraviť.

set -uo pipefail

payload=$(cat)
file=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')

[[ -z "$file" || ! -f "$file" ]] && exit 0

case "$file" in
  # ---- Python -------------------------------------------------------------
  *.py)
    # ruff je v projektovom venv, nie na PATH — preto `uv run`.
    if command -v ruff >/dev/null 2>&1; then
      ruff=(ruff)
    elif command -v uv >/dev/null 2>&1; then
      ruff=(uv run --quiet ruff)
    else
      exit 0
    fi
    "${ruff[@]}" format "$file" >/dev/null 2>&1
    if ! out=$("${ruff[@]}" check --fix "$file" 2>&1); then
      echo "Lint nálezy v $file (oprav ich prosím):"
      echo "$out"
    fi
    ;;

  # ---- TypeScript / JavaScript -------------------------------------------
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs)
    if command -v npx >/dev/null 2>&1; then
      npx --no-install prettier --write "$file" >/dev/null 2>&1
      if ! out=$(npx --no-install eslint --fix "$file" 2>&1); then
        echo "ESLint nálezy v $file (oprav ich prosím):"
        echo "$out"
      fi
    fi
    ;;

  # ---- Go ----------------------------------------------------------------
  *.go)
    command -v gofmt >/dev/null 2>&1 && gofmt -w "$file"
    ;;

  # ---- Rust --------------------------------------------------------------
  *.rs)
    command -v rustfmt >/dev/null 2>&1 && rustfmt "$file" 2>/dev/null
    ;;

  # ---- Konfigurácia: over, že je to platný súbor -------------------------
  *.json)
    if ! out=$(jq empty "$file" 2>&1); then
      echo "CHYBA: $file nie je platný JSON — oprav to pred pokračovaním:"
      echo "$out"
    fi
    ;;
  *.yaml|*.yml)
    if command -v python3 >/dev/null 2>&1; then
      if ! out=$(python3 -c 'import sys,yaml; yaml.safe_load(open(sys.argv[1]))' "$file" 2>&1); then
        echo "CHYBA: $file nie je platný YAML:"
        echo "$out"
      fi
    fi
    ;;
esac

exit 0
