#!/bin/bash
# CODEC Installer — One command to set up everything
# Usage:
#   ./install.sh           # Fresh install
#   ./install.sh --update  # Pull latest, migrate, restart (preserves config & skills)
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CODEC_DIR="$HOME/.codec"
VENV_DIR="$CODEC_DIR/venv"
MIN_DISK_GB=2
MIN_PY_MAJOR=3
MIN_PY_MINOR=10

echo ""
echo "  ██████  ██████  ██████  ███████  ██████"
echo " ██      ██    ██ ██   ██ ██      ██"
echo " ██      ██    ██ ██   ██ █████   ██"
echo " ██      ██    ██ ██   ██ ██      ██"
echo "  ██████  ██████  ██████  ███████  ██████"
echo ""
echo "  Your Open-Source Intelligent Command Layer"
echo "  https://opencodec.org"
echo ""

# ── Update mode ─────────────────────────────────────────────────────────
if [ "$1" = "--update" ]; then
    echo "═══ CODEC Update Mode ═══"
    cd "$REPO_DIR"

    # Save rollback point
    ROLLBACK_COMMIT=$(git rev-parse HEAD)
    echo "  Rollback point: $ROLLBACK_COMMIT"

    # Pull latest
    echo "→ Pulling latest code..."
    if ! git pull --ff-only; then
        echo "❌ Pull failed — resolve conflicts first."
        exit 1
    fi

    # Activate venv if exists
    if [ -f "$VENV_DIR/bin/activate" ]; then
        source "$VENV_DIR/bin/activate"
        echo "→ Updating dependencies..."
        pip install -r requirements.txt -q
    fi

    # Deploy skills (preserves user customizations)
    if [ -f deploy_skills.sh ]; then
        echo "→ Deploying skills..."
        ./deploy_skills.sh
    fi

    # Sync PM2
    if [ -f sync_to_pm2.sh ]; then
        echo "→ Syncing PM2..."
        ./sync_to_pm2.sh
    fi

    # Smoke test
    echo "→ Running smoke test..."
    if python3 codec_smoke_test.py; then
        echo "✅ Update complete"
    else
        echo "⚠️  Some smoke checks failed. Rolling back..."
        git reset --hard "$ROLLBACK_COMMIT"
        echo "  Rolled back to $ROLLBACK_COMMIT"
        exit 1
    fi
    exit 0
fi

# ── Prerequisites ───────────────────────────────────────────────────────

echo "Checking prerequisites..."
ERRORS=0

# macOS check
if [[ "$(uname)" != "Darwin" ]]; then
    echo "❌ CODEC currently supports macOS only."
    exit 1
fi
echo "  ✅ macOS $(sw_vers -productVersion)"

# Disk space check
FREE_GB=$(df -g / | tail -1 | awk '{print $4}')
if [ "$FREE_GB" -lt "$MIN_DISK_GB" ]; then
    echo "  ❌ Need at least ${MIN_DISK_GB}GB free disk space (have ${FREE_GB}GB)"
    ERRORS=$((ERRORS + 1))
else
    echo "  ✅ Disk space: ${FREE_GB}GB free"
fi

# Homebrew check
if ! command -v brew &>/dev/null; then
    echo "  ❌ Homebrew not found. Install from https://brew.sh"
    ERRORS=$((ERRORS + 1))
else
    echo "  ✅ Homebrew $(brew --version | head -1 | awk '{print $2}')"
fi

# Python check
if ! command -v python3 &>/dev/null; then
    echo "  ❌ Python 3 not found. Install: brew install python@3.13"
    ERRORS=$((ERRORS + 1))
else
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -lt "$MIN_PY_MAJOR" ] || ([ "$PY_MAJOR" -eq "$MIN_PY_MAJOR" ] && [ "$PY_MINOR" -lt "$MIN_PY_MINOR" ]); then
        echo "  ❌ Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ required (have $PY_VERSION)"
        ERRORS=$((ERRORS + 1))
    else
        echo "  ✅ Python $PY_VERSION"
    fi
fi

# Node.js / PM2 check
if ! command -v node &>/dev/null; then
    echo "  ⚠️  Node.js not found — will install for PM2"
    if command -v brew &>/dev/null; then
        brew install node
    else
        echo "  ❌ Cannot install Node.js without Homebrew"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "  ✅ Node.js $(node --version)"
fi

if ! command -v pm2 &>/dev/null; then
    echo "  📦 Installing PM2..."
    npm install -g pm2
fi
echo "  ✅ PM2 $(pm2 --version 2>/dev/null || echo 'installed')"

# sox check
if ! command -v sox &>/dev/null; then
    echo "  📦 Installing sox (audio recording)..."
    brew install sox
else
    echo "  ✅ sox installed"
fi

if [ "$ERRORS" -gt 0 ]; then
    echo ""
    echo "❌ $ERRORS prerequisite(s) missing. Fix them and re-run."
    exit 1
fi

# ── Virtual Environment ─────────────────────────────────────────────────

echo ""
echo "Setting up Python virtual environment..."
mkdir -p "$CODEC_DIR"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created venv at $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo "  ✅ Activated venv ($(python3 --version))"

# Install dependencies into venv
echo "📦 Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r "$REPO_DIR/requirements.txt" -q
echo "  ✅ Dependencies installed"

# ── Config & Skills ─────────────────────────────────────────────────────

mkdir -p "$CODEC_DIR/skills" "$CODEC_DIR/skill_output" "$CODEC_DIR/backups"

# Backup existing skills before overwrite
if [ -d "$CODEC_DIR/skills" ] && ls "$CODEC_DIR/skills"/*.py &>/dev/null 2>&1; then
    BACKUP_DIR="$CODEC_DIR/skills_backup_$(date +%Y%m%d_%H%M%S)"
    echo "  Backing up existing skills to $BACKUP_DIR"
    cp -r "$CODEC_DIR/skills" "$BACKUP_DIR"
fi

# Deploy skills
echo "📦 Installing 50+ skills..."
cp "$REPO_DIR/skills"/*.py "$CODEC_DIR/skills/" 2>/dev/null || true

# ── PM2 Setup ───────────────────────────────────────────────────────────

echo ""
echo "Configuring PM2..."
cd "$REPO_DIR"
pm2 start ecosystem.config.js 2>/dev/null || pm2 restart ecosystem.config.js 2>/dev/null || true
pm2 save 2>/dev/null || true
echo "  ✅ PM2 processes configured"

# ── macOS Permissions Guidance ──────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════"
echo "  macOS Permissions Required"
echo "═══════════════════════════════════════"
echo ""
echo "  CODEC needs these permissions to work:"
echo "  1. Accessibility — for keyboard shortcuts"
echo "  2. Microphone — for voice commands"
echo "  3. Screen Recording — for screen reading"
echo ""
echo "  Go to: System Settings > Privacy & Security"
echo "  Add Terminal.app (or your terminal) to each."
echo ""

# ── Setup Wizard ────────────────────────────────────────────────────────

echo "🚀 Starting CODEC Setup Wizard..."
echo ""
python3 "$REPO_DIR/setup_codec.py"

echo ""
echo "═══════════════════════════════════════"
echo "  ✅ CODEC Installed Successfully!"
echo "═══════════════════════════════════════"
echo ""
echo "  Start:   pm2 start ecosystem.config.js"
echo "  Status:  pm2 status"
echo "  Logs:    pm2 logs codec"
echo "  Update:  ./install.sh --update"
echo ""
echo "  Dashboard: http://localhost:8090"
echo "  Docs:      https://opencodec.org"
echo ""
