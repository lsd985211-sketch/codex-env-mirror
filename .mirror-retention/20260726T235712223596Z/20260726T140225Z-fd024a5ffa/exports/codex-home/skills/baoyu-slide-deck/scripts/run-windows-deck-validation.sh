#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --pptx <file> --out <render-dir> --expected <count> [--replace] [--width <px>] [--height <px>]"
}

pptx=""
out=""
expected=""
width=1600
height=900
replace=0

while (($#)); do
  case "$1" in
    --pptx) pptx=${2:-}; shift 2 ;;
    --out) out=${2:-}; shift 2 ;;
    --expected) expected=${2:-}; shift 2 ;;
    --width) width=${2:-}; shift 2 ;;
    --height) height=${2:-}; shift 2 ;;
    --replace) replace=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$pptx" || -z "$out" || ! "$expected" =~ ^[1-9][0-9]*$ ]]; then
  usage >&2
  exit 2
fi
if [[ ! -f "$pptx" ]]; then
  echo "Presentation not found: $pptx" >&2
  exit 2
fi
for command in powershell.exe wslpath; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command is unavailable: $command" >&2
    exit 2
  fi
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
user_profile_win=$(powershell.exe -NoProfile -NonInteractive -Command '[Console]::Write($env:USERPROFILE)' | tr -d '\r')
user_profile=$(wslpath -u "$user_profile_win")
runtime="$user_profile/.cache/codex-runtimes/codex-primary-runtime/dependencies/node"
node="$runtime/bin/node.exe"
modules="$runtime/node_modules"
if [[ ! -x "$node" ]]; then
  echo "Bundled Node runtime not found: $node" >&2
  exit 2
fi

export NODE_PATH="$modules/.pnpm/node_modules:$modules"
export WSLENV="${WSLENV:+$WSLENV:}NODE_PATH/p"

ps_script=$(wslpath -w "$script_dir/verify-pptx-render.ps1")
js_script=$(wslpath -w "$script_dir/verify-rendered-slides.js")
pptx_win=$(wslpath -w "$pptx")
out_win=$(wslpath -w "$out")

ps_args=(
  -NoProfile -NonInteractive -ExecutionPolicy Bypass
  -File "$ps_script"
  -PresentationPath "$pptx_win"
  -OutputDirectory "$out_win"
  -ExpectedSlides "$expected"
  -Width "$width"
  -Height "$height"
)
if ((replace)); then ps_args+=(-ReplaceOutput); fi
powershell.exe "${ps_args[@]}"

"$node" "$js_script" \
  --dir "$out_win" \
  --expected "$expected" \
  --width "$width" \
  --height "$height" \
  --contact-sheet "$(wslpath -w "$out/contact-sheet.png")" \
  --receipt "$(wslpath -w "$out/validation-slide-images.json")"
