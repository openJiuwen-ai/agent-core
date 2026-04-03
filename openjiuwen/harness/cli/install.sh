#!/usr/bin/env bash
# ---------------------------------------------------------------
# OpenJiuWen CLI — One-key installer for Linux / macOS
#
# Usage:
#   curl -fsSL https://gitcode.com/openJiuwen/agent-core/raw/main/openjiuwen/harness/cli/install.sh | bash
#
# Or run locally:
#   bash openjiuwen/harness/cli/install.sh
# ---------------------------------------------------------------
set -euo pipefail

# --- Colours ---------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Colour

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
err()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

# --- Platform detection ----------------------------------------
detect_os() {
    case "$(uname -s)" in
        Linux*)   echo "linux"  ;;
        Darwin*)  echo "macos"  ;;
        CYGWIN*|MINGW*|MSYS*) echo "windows" ;;
        *)        echo "unknown" ;;
    esac
}

OS="$(detect_os)"
ARCH="$(uname -m)"

info "Detected OS: ${OS} (${ARCH})"

if [ "$OS" = "unknown" ]; then
    err "Unsupported operating system. Please install manually:"
    err "  pip install -U \"openjiuwen[cli]\""
    exit 1
fi

if [ "$OS" = "windows" ]; then
    warn "For Windows, please use the PowerShell installer instead:"
    warn "  irm https://gitcode.com/openJiuwen/agent-core/raw/main/openjiuwen/harness/cli/install.ps1 | iex"
    exit 1
fi

# --- Check Python version -------------------------------------
MIN_PY_MAJOR=3
MIN_PY_MINOR=11

find_python() {
    # Try common Python 3.11+ commands
    for cmd in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver="$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || continue
            local major minor
            major="${ver%%.*}"
            minor="${ver#*.}"
            if [ "$major" -ge "$MIN_PY_MAJOR" ] && [ "$minor" -ge "$MIN_PY_MINOR" ]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_CMD=""
if ! PYTHON_CMD="$(find_python)"; then
    err "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ is required but not found."
    echo ""
    echo "Install Python:"
    if [ "$OS" = "macos" ]; then
        echo "  brew install python@3.11"
    else
        echo "  # Ubuntu / Debian"
        echo "  sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip"
        echo ""
        echo "  # Fedora / RHEL"
        echo "  sudo dnf install -y python3.11"
        echo ""
        echo "  # Arch Linux"
        echo "  sudo pacman -S python"
    fi
    exit 1
fi

PY_VERSION="$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
ok "Found Python ${PY_VERSION} (${PYTHON_CMD})"

# --- Check / install pip --------------------------------------
if ! "$PYTHON_CMD" -m pip --version &>/dev/null; then
    warn "pip not found. Attempting to install..."
    if [ "$OS" = "macos" ]; then
        "$PYTHON_CMD" -m ensurepip --upgrade 2>/dev/null || {
            err "Could not install pip. Please install it manually:"
            err "  $PYTHON_CMD -m ensurepip --upgrade"
            exit 1
        }
    else
        "$PYTHON_CMD" -m ensurepip --upgrade 2>/dev/null || {
            warn "ensurepip failed. Trying get-pip.py..."
            curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$PYTHON_CMD" || {
                err "Could not install pip. Please install it manually:"
                err "  sudo apt install python3-pip  # Debian/Ubuntu"
                err "  sudo dnf install python3-pip  # Fedora/RHEL"
                exit 1
            }
        }
    fi
    ok "pip installed successfully."
fi

PIP_VERSION="$("$PYTHON_CMD" -m pip --version 2>/dev/null | head -1)"
ok "pip: ${PIP_VERSION}"

# --- Install openjiuwen[cli] ----------------------------------
info "Installing openjiuwen[cli]..."
echo ""

if "$PYTHON_CMD" -m pip install -U "openjiuwen[cli]" 2>&1; then
    ok "openjiuwen[cli] installed successfully."
else
    warn "Global install failed. Trying with --user flag..."
    if "$PYTHON_CMD" -m pip install -U --user "openjiuwen[cli]" 2>&1; then
        ok "openjiuwen[cli] installed with --user flag."

        # Ensure user bin directory is in PATH
        USER_BIN="$("$PYTHON_CMD" -m site --user-base)/bin"
        if [[ ":$PATH:" != *":$USER_BIN:"* ]]; then
            warn "Adding ${USER_BIN} to PATH..."

            SHELL_NAME="$(basename "${SHELL:-/bin/bash}")"
            RC_FILE=""
            case "$SHELL_NAME" in
                zsh)  RC_FILE="$HOME/.zshrc" ;;
                bash)
                    if [ -f "$HOME/.bash_profile" ]; then
                        RC_FILE="$HOME/.bash_profile"
                    else
                        RC_FILE="$HOME/.bashrc"
                    fi
                    ;;
                fish) RC_FILE="$HOME/.config/fish/config.fish" ;;
                *)    RC_FILE="$HOME/.profile" ;;
            esac

            PATH_LINE="export PATH=\"${USER_BIN}:\$PATH\""
            if [ "$SHELL_NAME" = "fish" ]; then
                PATH_LINE="set -gx PATH ${USER_BIN} \$PATH"
            fi

            if [ -n "$RC_FILE" ]; then
                if ! grep -qF "$USER_BIN" "$RC_FILE" 2>/dev/null; then
                    echo "" >> "$RC_FILE"
                    echo "# Added by openjiuwen installer" >> "$RC_FILE"
                    echo "$PATH_LINE" >> "$RC_FILE"
                    ok "Added to ${RC_FILE}"
                    warn "Run 'source ${RC_FILE}' or open a new terminal to use openjiuwen."
                fi
            fi

            export PATH="${USER_BIN}:$PATH"
        fi
    else
        err "Installation failed. Please check the error messages above."
        exit 1
    fi
fi

# --- Verify installation --------------------------------------
echo ""
if command -v openjiuwen &>/dev/null; then
    INSTALLED_VERSION="$(openjiuwen --version 2>/dev/null || echo "unknown")"
    ok "openjiuwen is ready: ${INSTALLED_VERSION}"
else
    # Binary might not be in PATH yet for this shell session
    SCRIPT_PATH="$("$PYTHON_CMD" -c 'import shutil; p = shutil.which("openjiuwen"); print(p or "")' 2>/dev/null)"
    if [ -n "$SCRIPT_PATH" ]; then
        INSTALLED_VERSION="$("$SCRIPT_PATH" --version 2>/dev/null || echo "unknown")"
        ok "openjiuwen installed at: ${SCRIPT_PATH} (${INSTALLED_VERSION})"
        warn "You may need to open a new terminal for the 'openjiuwen' command to be available."
    else
        warn "openjiuwen installed but not found in PATH."
        warn "Try opening a new terminal, or run: $PYTHON_CMD -m openjiuwen.harness.cli"
    fi
fi

# --- Print next steps -----------------------------------------
echo ""
echo -e "${BOLD}Next steps:${NC}"
echo "  1. Run 'openjiuwen' to start (interactive setup on first launch)"
echo "  2. Or configure manually: ~/.openjiuwen/settings.json"
echo ""
echo -e "  ${CYAN}Example settings.json:${NC}"
echo '  {'
echo '    "provider": "OpenAI",'
echo '    "model": "gpt-4o",'
echo '    "apiKey": "sk-...",'
echo '    "apiBase": "https://api.openai.com/v1"'
echo '  }'
echo ""
echo -e "${GREEN}Happy coding with OpenJiuWen!${NC}"
