#!/usr/bin/env bash
# NiiCE installer for Linux: ./install.sh   (./install.sh --help for options)
#
# 1. checks/installs system packages (asks before using sudo)
# 2. creates a Python environment and installs NiiCE into it
# 3. links the `niice` command into ~/.local/bin
# 4. optionally downloads the SynthStrip model weights
# 5. adds the right-click menu to your file manager
set -euo pipefail

VENV="${NIICE_VENV:-$HOME/.venvs/niice}"
BIN_DIR="$HOME/.local/bin"
SYNTHSTRIP=1
GPU=0
MENU=1
SYSDEPS=1
DEV=0
YES=0
ACTION=install
PURGE=0
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<EOF
Usage: ./install.sh [options]

  --venv PATH        where to create the Python environment (default: $VENV)
  --no-synthstrip    skip skull stripping support (no PyTorch, ~1 GB less)
  --gpu              install the CUDA build of PyTorch instead of the CPU-only one
  --no-menu          do not add the right-click menu
  --no-system-deps   do not check/install system packages
  --dev              editable install from this folder, with test tools
  -y, --yes          answer yes to every question (still asks for your sudo password)
  --uninstall        remove the menu, the environment and the niice command
  --purge            with --uninstall: also delete settings, plugins and weights (~/.config/niice)
  -h, --help         show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --venv) VENV="$2"; shift ;;
        --no-synthstrip) SYNTHSTRIP=0 ;;
        --gpu) GPU=1 ;;
        --no-menu) MENU=0 ;;
        --no-system-deps) SYSDEPS=0 ;;
        --dev) DEV=1 ;;
        -y|--yes) YES=1 ;;
        --uninstall) ACTION=uninstall ;;
        --purge) PURGE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 2 ;;
    esac
    shift
done

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }
ask() {  # ask "question" -> 0 for yes
    [[ $YES -eq 1 ]] && return 0
    local reply
    read -r -p "  $1 [Y/n] " reply || reply=n
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

# --------------------------------------------------------------------------- uninstall
if [[ $ACTION == uninstall ]]; then
    bold "Uninstalling NiiCE"
    if [[ -x "$VENV/bin/niice" ]]; then
        "$VENV/bin/niice" uninstall >/dev/null 2>&1 && info "Removed the right-click menu entries" || true
    fi
    if [[ -L "$BIN_DIR/niice" ]]; then rm -f "$BIN_DIR/niice"; info "Removed $BIN_DIR/niice"; fi
    if [[ -d "$VENV" ]]; then rm -rf "$VENV"; info "Removed $VENV"; fi
    if [[ $PURGE -eq 1 ]]; then
        rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/niice"
        info "Removed settings, plugins and weights"
    else
        info "Kept settings, plugins and weights in ${XDG_CONFIG_HOME:-$HOME/.config}/niice (--purge removes them)"
    fi
    echo
    info "Done. Restart your file manager (e.g. nautilus -q) to drop the old menu."
    exit 0
fi

[[ "$(uname -s)" == Linux ]] || warn "This script is written for Linux; on other systems follow the README."
[[ -f "$SRC/pyproject.toml" ]] || fail "Run install.sh from the NiiCE folder (pyproject.toml not found next to it)."
[[ $EUID -ne 0 ]] || warn "Running as root: NiiCE will be installed for root only. Usually you want to run this as yourself."

# --------------------------------------------------------------------------- 1. system packages
bold "1/5  System packages"
PY=""
for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
        PY="$(command -v "$cand")"; break
    fi
done
have() { command -v "$1" >/dev/null 2>&1; }
need=()
if have apt-get; then
    pm="sudo apt-get install -y"
    pkg_ok() { dpkg -s "$1" >/dev/null 2>&1; }
    [[ -n "$PY" ]] || need+=(python3)
    if [[ -n "$PY" ]] && ! "$PY" -c 'import ensurepip, venv' 2>/dev/null; then
        need+=("python3-venv")
    fi
    pkg_ok libxcb-cursor0 || need+=(libxcb-cursor0)
    have nautilus && ! pkg_ok python3-nautilus && need+=(python3-nautilus)
    have nemo && ! pkg_ok python-nemo && ! pkg_ok python3-nemo && need+=(python-nemo)
elif have dnf; then
    pm="sudo dnf install -y"
    pkg_ok() { rpm -q "$1" >/dev/null 2>&1; }
    [[ -n "$PY" ]] || need+=(python3)
    pkg_ok xcb-util-cursor || need+=(xcb-util-cursor)
    have nautilus && ! pkg_ok nautilus-python && need+=(nautilus-python)
    have nemo && ! pkg_ok nemo-python && need+=(nemo-python)
elif have pacman; then
    pm="sudo pacman -S --needed --noconfirm"
    pkg_ok() { pacman -Q "$1" >/dev/null 2>&1; }
    [[ -n "$PY" ]] || need+=(python)
    pkg_ok xcb-util-cursor || need+=(xcb-util-cursor)
    have nautilus && ! pkg_ok python-nautilus && need+=(python-nautilus)
    have nemo && ! pkg_ok nemo-python && need+=(nemo-python)
else
    pm=""
fi
if [[ $SYSDEPS -eq 0 ]]; then
    info "Skipped (--no-system-deps)"
elif [[ ${#need[@]} -eq 0 ]]; then
    info "Nothing missing"
elif [[ -z "$pm" ]]; then
    warn "Unknown package manager. You may need: Python >= 3.9 with venv, libxcb-cursor, nautilus-python"
else
    info "Missing: ${need[*]}"
    info "(python3-venv: Python environments; libxcb-cursor: Qt windows;"
    info " *nautilus*/*nemo*: lets NiiCE add a top-level right-click menu)"
    if ask "Install them now with: $pm ${need[*]} ?"; then
        $pm "${need[@]}" || warn "Package installation failed; continuing (see above)."
        PY="${PY:-$(command -v python3 || true)}"
    else
        warn "Skipped. Without them some parts may not work (see the list above)."
    fi
fi
[[ -n "$PY" ]] || fail "Python 3.9 or newer was not found. Install python3 and run this script again."
info "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# --------------------------------------------------------------------------- 2. environment
bold "2/5  Python environment ($VENV)"
if [[ -x "$VENV/bin/python" ]]; then
    info "Reusing the existing environment (updating NiiCE in it)"
else
    "$PY" -m venv "$VENV" || fail "Could not create the environment. On Ubuntu/Debian: sudo apt install python3-venv"
    info "Created"
fi
VPY="$VENV/bin/python"
"$VPY" -m pip install --quiet --upgrade pip
if [[ $SYNTHSTRIP -eq 1 ]]; then
    if "$VPY" -c 'import torch' 2>/dev/null; then
        info "PyTorch already installed"
    elif [[ $GPU -eq 1 ]]; then
        info "Installing PyTorch with CUDA (large download)..."
        "$VPY" -m pip install --quiet torch
    else
        info "Installing PyTorch, CPU-only build (~200 MB)..."
        "$VPY" -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu
    fi
fi
extras=()
[[ $SYNTHSTRIP -eq 1 ]] && extras+=(synthstrip)
[[ $DEV -eq 1 ]] && extras+=(test)
spec="$SRC"
[[ ${#extras[@]} -gt 0 ]] && spec="$SRC[$(IFS=,; echo "${extras[*]}")]"
info "Installing NiiCE and its dependencies..."
if [[ $DEV -eq 1 ]]; then
    "$VPY" -m pip install --quiet -e "$spec"
else
    "$VPY" -m pip install --quiet --upgrade "$spec"
fi
"$VENV/bin/niice" --version >/dev/null || fail "NiiCE did not install correctly (see the messages above)."
info "NiiCE $("$VENV/bin/niice" --version) installed"

# --------------------------------------------------------------------------- 3. command
bold "3/5  niice command"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/niice" "$BIN_DIR/niice"
info "Linked $BIN_DIR/niice"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH. Add this line to ~/.bashrc, then open a new terminal:"
       info "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# --------------------------------------------------------------------------- 4. weights
bold "4/5  SynthStrip model weights"
if [[ $SYNTHSTRIP -eq 0 ]]; then
    info "Skipped (--no-synthstrip)"
elif ask "Download the SynthStrip weights now (2 x 30 MB; otherwise on first use)?"; then
    "$VENV/bin/niice" synthstrip-weights || warn "Download failed; NiiCE will try again on first use."
else
    info "Will be downloaded on first use"
fi

# --------------------------------------------------------------------------- 5. menu
bold "5/5  Right-click menu"
if [[ $MENU -eq 0 ]]; then
    info "Skipped (--no-menu)"
else
    "$VENV/bin/niice" install || warn "No supported file manager found (see the README for others)."
    if have nautilus && pgrep -x nautilus >/dev/null 2>&1; then
        if ask "Restart Nautilus now so the menu appears (open Files windows will close)?"; then
            nautilus -q >/dev/null 2>&1 || true
            info "Restarted. Right-click a .nii or .nii.gz file."
        else
            info "Later, run: nautilus -q"
        fi
    fi
fi

bold "Done"
info "Try it:  niice demo ~/niice_demo && cd ~/niice_demo && niice info T1w.nii.gz T2w_shifted.nii.gz"
info "Settings: niice settings      Update: unzip a new version and run ./install.sh again"
info "Remove:  ./install.sh --uninstall"
