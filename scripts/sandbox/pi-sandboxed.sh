#!/usr/bin/env bash
# =============================================================================
# Launch Pi inside a bubblewrap sandbox.
#
# Visible inside the sandbox:
#   - the project directory                          (read-write)
#   - ~/Public                                       (read-only)
#   - a private /tmp                                 (read-write, ephemeral)
#   - Pi's own agent directory ~/.pi/agent           (read-write)
#   - the toolchain, read-only: /usr /bin /lib /etc /sys,
#     ~/miniconda3, ~/.local/bin, ~/.local/share/uv
#
# Everything else in the home directory — Desktop, Downloads, Documents,
# .ssh, .config, .gnupg, keyrings, other projects — simply does not exist
# inside the sandbox. It is not "protected by a setting"; it is absent.
#
# This is the only mechanism that actually constrains file access: Pi itself
# has no built-in filesystem sandbox and its bash tool runs with the
# permissions of the launching user.
#
# Usage:
#   ./scripts/sandbox/pi-sandboxed.sh              # start Pi sandboxed
#   ./scripts/sandbox/pi-sandboxed.sh bash -lc ls  # run a command sandboxed
#   PI_SANDBOX_ALLOW_SSH=1 ./...                   # also expose ~/.ssh (git push)
# =============================================================================
set -euo pipefail

PROJECT="${PI_SANDBOX_PROJECT:-/home/shahriar/Documents/P-OCR-VL}"
PUBLIC="${PI_SANDBOX_PUBLIC:-$HOME/Public}"
HOME_DIR="$HOME"

if [[ ! -d "$PROJECT" ]]; then
  echo "pi-sandboxed: project directory not found: $PROJECT" >&2
  exit 1
fi
if ! command -v bwrap >/dev/null 2>&1; then
  echo "pi-sandboxed: bubblewrap (bwrap) is required: sudo apt install bubblewrap" >&2
  exit 1
fi

# --- Resolve credentials OUTSIDE the sandbox, pass them only as env ----------
# No token is ever written to a file. Nothing is exported into your shell
# profile; the value exists only in the environment of the sandboxed process.
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
if [[ -z "$GITHUB_TOKEN" ]] && command -v gh >/dev/null 2>&1; then
  GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
fi
HF_TOKEN="${HF_TOKEN:-}"
if [[ -z "$HF_TOKEN" && -f "$HOME_DIR/.cache/huggingface/token" ]]; then
  HF_TOKEN="$(tr -d '\n' < "$HOME_DIR/.cache/huggingface/token")"
fi

# --- Assemble the namespace ------------------------------------------------
args=(
  # Base operating system, read-only
  --ro-bind /usr /usr
  --ro-bind /etc /etc
  --ro-bind /sys /sys
  --symlink usr/bin /bin
  --symlink usr/sbin /sbin
  --symlink usr/lib /lib
  --symlink usr/lib64 /lib64
  --proc /proc
  --dev /dev

  # Ephemeral temp: a fresh private tmpfs, no host /tmp visible
  --tmpfs /tmp

  # Home directory is emptied, then only the permitted paths are re-exposed
  --tmpfs "$HOME_DIR"
  --dir "$HOME_DIR/.cache"

  # Permitted: project (read-write)
  --bind "$PROJECT" "$PROJECT"

  # Permitted: public folder (read-only)
  --ro-bind "$PUBLIC" "$PUBLIC"

  # Pi's own configuration, sessions, skills and installed npm packages
  --bind "$HOME_DIR/.pi/agent" "$HOME_DIR/.pi/agent"

  # Toolchain, read-only
  --ro-bind "$HOME_DIR/miniconda3" "$HOME_DIR/miniconda3"
  --ro-bind "$HOME_DIR/.local/bin" "$HOME_DIR/.local/bin"
  --ro-bind "$HOME_DIR/.local/share/uv" "$HOME_DIR/.local/share/uv"

  # Project-local npm cache so npx does not need a writable ~/.npm
  --dir "$PROJECT/.cache"
  --dir "$PROJECT/.cache/npm"

  --chdir "$PROJECT"
  --unshare-pid
  --unshare-ipc
  --new-session
  --die-with-parent

  --clearenv
  --setenv HOME "$HOME_DIR"
  --setenv USER "${USER:-$(id -un)}"
  --setenv LOGNAME "${LOGNAME:-$(id -un)}"
  --setenv SHELL /bin/bash
  --setenv TERM "${TERM:-xterm-256color}"
  --setenv LANG "${LANG:-C.UTF-8}"
  --setenv TZ "${TZ:-Asia/Tehran}"
  --setenv PATH "$HOME_DIR/.local/bin:$HOME_DIR/miniconda3/bin:/usr/local/bin:/usr/bin:/bin"
  --setenv NPM_CONFIG_CACHE "$PROJECT/.cache/npm"
  --setenv UV_CACHE_DIR "$PROJECT/.cache/uv"
  --setenv PYTHONDONTWRITEBYTECODE 1
)

# git identity, without exposing ~/.config or ~/.gitconfig
[[ -f "$HOME_DIR/.gitconfig" ]] && args+=(--ro-bind "$HOME_DIR/.gitconfig" "$HOME_DIR/.gitconfig")

# Optional: expose the SSH agent/keys when a sandboxed git push is required
if [[ "${PI_SANDBOX_ALLOW_SSH:-0}" == "1" && -d "$HOME_DIR/.ssh" ]]; then
  args+=(--ro-bind "$HOME_DIR/.ssh" "$HOME_DIR/.ssh")
fi

# Credentials and API keys, passed only when actually set
[[ -n "$GITHUB_TOKEN" ]] && args+=(--setenv GITHUB_TOKEN "$GITHUB_TOKEN")
[[ -n "$HF_TOKEN" ]] && args+=(--setenv HF_TOKEN "$HF_TOKEN")
for var in AI4SCHOLAR_API_KEY NVIDIA_API_KEY NVIDIA_BASE_URL MLFLOW_TRACKING_URI \
           EDITOR VISUAL PAGER HTTPS_PROXY HTTP_PROXY NO_PROXY; do
  [[ -n "${!var:-}" ]] && args+=(--setenv "$var" "${!var}")
done
# Numbered NVIDIA keys
while IFS= read -r name; do
  [[ -n "$name" ]] && args+=(--setenv "$name" "${!name}")
done < <(compgen -A variable NVIDIA_API_KEY_ || true)

# --- Exec ------------------------------------------------------------------
if [[ $# -gt 0 ]]; then
  exec bwrap "${args[@]}" "$@"
fi
exec bwrap "${args[@]}" "$HOME_DIR/.pi/agent/bin/pi"
