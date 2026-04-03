# ---------------------------------------------------------------
# OpenJiuWen CLI — One-key installer for Windows (PowerShell)
#
# Usage:
#   irm https://gitcode.com/openJiuwen/agent-core/raw/main/openjiuwen/harness/cli/install.ps1 | iex
#
# Or run locally:
#   powershell -ExecutionPolicy Bypass -File openjiuwen\harness\cli\install.ps1
# ---------------------------------------------------------------

$ErrorActionPreference = "Stop"

# --- Helpers ---------------------------------------------------
function Write-Info  { Write-Host "[INFO]  $args" -ForegroundColor Cyan }
function Write-Ok    { Write-Host "[OK]    $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN]  $args" -ForegroundColor Yellow }
function Write-Err   { Write-Host "[ERROR] $args" -ForegroundColor Red }

# --- Platform detection ----------------------------------------
Write-Info "Detected OS: Windows ($([System.Environment]::OSVersion.Version))"
Write-Info "Architecture: $env:PROCESSOR_ARCHITECTURE"

# --- Check Python version -------------------------------------
$MinMajor = 3
$MinMinor = 11

$PythonCmd = $null
$PythonCandidates = @("python3.13", "python3.12", "python3.11", "python3", "python", "py")

foreach ($cmd in $PythonCandidates) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) {
            $parts = $ver.Split(".")
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -ge $MinMajor -and $minor -ge $MinMinor) {
                $PythonCmd = $cmd
                break
            }
        }
    } catch {
        continue
    }
}

# Try 'py' launcher with version flag (Windows-specific)
if (-not $PythonCmd) {
    foreach ($pyVer in @("-3.13", "-3.12", "-3.11")) {
        try {
            $ver = & py $pyVer -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver) {
                $parts = $ver.Split(".")
                $major = [int]$parts[0]
                $minor = [int]$parts[1]
                if ($major -ge $MinMajor -and $minor -ge $MinMinor) {
                    $PythonCmd = "py $pyVer"
                    break
                }
            }
        } catch {
            continue
        }
    }
}

if (-not $PythonCmd) {
    Write-Err "Python ${MinMajor}.${MinMinor}+ is required but not found."
    Write-Host ""
    Write-Host "Install Python from: https://www.python.org/downloads/"
    Write-Host "  - Check 'Add python.exe to PATH' during installation"
    Write-Host "  - Or install via winget: winget install Python.Python.3.11"
    Write-Host "  - Or install via scoop:  scoop install python"
    exit 1
}

$PyVersion = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
Write-Ok "Found Python $PyVersion ($PythonCmd)"

# --- Check / install pip --------------------------------------
try {
    $pipVer = & $PythonCmd -m pip --version 2>$null
    Write-Ok "pip: $pipVer"
} catch {
    Write-Warn "pip not found. Attempting to install..."
    try {
        & $PythonCmd -m ensurepip --upgrade 2>$null
        Write-Ok "pip installed successfully."
    } catch {
        Write-Err "Could not install pip. Please install it manually:"
        Write-Err "  $PythonCmd -m ensurepip --upgrade"
        exit 1
    }
}

# --- Install openjiuwen[cli] ----------------------------------
Write-Info "Installing openjiuwen[cli]..."
Write-Host ""

try {
    & $PythonCmd -m pip install -U "openjiuwen[cli]"
    Write-Ok "openjiuwen[cli] installed successfully."
} catch {
    Write-Warn "Global install failed. Trying with --user flag..."
    try {
        & $PythonCmd -m pip install -U --user "openjiuwen[cli]"
        Write-Ok "openjiuwen[cli] installed with --user flag."

        # Ensure user Scripts directory is in PATH
        $UserScripts = & $PythonCmd -c "import site; print(site.getusersitepackages().replace('site-packages', 'Scripts'))" 2>$null
        if ($UserScripts -and ($env:PATH -notlike "*$UserScripts*")) {
            Write-Warn "Adding $UserScripts to user PATH..."
            $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
            if ($currentPath -notlike "*$UserScripts*") {
                [Environment]::SetEnvironmentVariable(
                    "PATH",
                    "$UserScripts;$currentPath",
                    "User"
                )
                $env:PATH = "$UserScripts;$env:PATH"
                Write-Ok "Added to user PATH. You may need to restart your terminal."
            }
        }
    } catch {
        Write-Err "Installation failed. Please check the error messages above."
        exit 1
    }
}

# --- Verify installation --------------------------------------
Write-Host ""
try {
    $installedVersion = & openjiuwen --version 2>$null
    Write-Ok "openjiuwen is ready: $installedVersion"
} catch {
    try {
        $scriptPath = & $PythonCmd -c "import shutil; p = shutil.which('openjiuwen'); print(p or '')" 2>$null
        if ($scriptPath) {
            Write-Ok "openjiuwen installed at: $scriptPath"
            Write-Warn "You may need to open a new terminal for the 'openjiuwen' command to be available."
        } else {
            Write-Warn "openjiuwen installed but not found in PATH."
            Write-Warn "Try opening a new terminal, or run: $PythonCmd -m openjiuwen.harness.cli"
        }
    } catch {
        Write-Warn "openjiuwen installed. Open a new terminal to use it."
    }
}

# --- Print next steps -----------------------------------------
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Run 'openjiuwen' to start (interactive setup on first launch)"
Write-Host "  2. Or configure manually: ~\.openjiuwen\settings.json"
Write-Host ""
Write-Host "  Example settings.json:" -ForegroundColor Cyan
Write-Host '  {'
Write-Host '    "provider": "OpenAI",'
Write-Host '    "model": "gpt-4o",'
Write-Host '    "apiKey": "sk-...",'
Write-Host '    "apiBase": "https://api.openai.com/v1"'
Write-Host '  }'
Write-Host ""
Write-Host "Happy coding with OpenJiuWen!" -ForegroundColor Green
