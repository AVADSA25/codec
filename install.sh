#!/bin/bash
# CODEC Installer — One command to set up everything
set -e

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

# Check Python 3.10+
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found. Install from python.org"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅ Python $PY_VERSION"
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo "❌ Python 3.10+ required. Found $PY_VERSION"
    exit 1
fi

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "⚠️  CODEC currently supports macOS only. Linux port coming soon."
    exit 1
fi
echo "✅ macOS detected"

# Install sox
if ! command -v sox &>/dev/null; then
    echo "📦 Installing sox..."
    brew install sox
else
    echo "✅ sox installed"
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip3 install -r requirements.txt --break-system-packages 2>/dev/null || pip3 install -r requirements.txt

# Create config directory
mkdir -p ~/.codec/skills

# Backup existing skills before overwrite (preserves user customizations)
if [ -d ~/.codec/skills ] && [ "$(ls -A ~/.codec/skills/*.py 2>/dev/null)" ]; then
    BACKUP_DIR="$HOME/.codec/skills_backup_$(date +%Y%m%d_%H%M%S)"
    echo "  Backing up existing skills to $BACKUP_DIR"
    cp -r ~/.codec/skills "$BACKUP_DIR"
fi

# Copy skills
echo "📦 Installing 50+ skills..."
cp skills/*.py ~/.codec/skills/ 2>/dev/null

# Run setup wizard
echo ""
echo "🚀 Starting CODEC Setup Wizard..."
echo ""
python3 setup_codec.py

echo ""
echo "✅ CODEC installed! Start with: python3 codec.py"
echo "📖 Full docs: https://opencodec.org"
echo ""
