#!/usr/bin/env bash
# pi-setup.sh — Automated sdr-mcp setup for Raspberry Pi
# Run as a regular user (not root). Uses sudo where needed.
# Tested on Raspberry Pi OS Lite 64-bit (Debian Bookworm).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$REPO_DIR/.venv"

info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ── 1. System dependencies ────────────────────────────────────────────────────

info "Updating package lists..."
sudo apt-get update -qq

info "Installing system dependencies..."
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git cmake build-essential \
    libusb-1.0-0-dev pkg-config \
    sox

# ── 2. RTL-SDR Blog librtlsdr ─────────────────────────────────────────────────

if ! command -v rtl_test &>/dev/null; then
    info "Building RTL-SDR Blog librtlsdr from source..."
    TMPDIR=$(mktemp -d)
    git clone --depth 1 https://github.com/rtlsdrblog/rtl-sdr-blog.git "$TMPDIR/rtl-sdr-blog"
    cmake -S "$TMPDIR/rtl-sdr-blog" -B "$TMPDIR/rtl-sdr-blog/build" -DINSTALL_UDEV_RULES=ON
    make -C "$TMPDIR/rtl-sdr-blog/build" -j"$(nproc)"
    sudo make -C "$TMPDIR/rtl-sdr-blog/build" install
    sudo ldconfig
    rm -rf "$TMPDIR"
else
    info "rtl_test already present — skipping librtlsdr build."
fi

# Blacklist the kernel DVB driver that conflicts with rtl-sdr
BLACKLIST=/etc/modprobe.d/blacklist-rtl.conf
if [ ! -f "$BLACKLIST" ]; then
    info "Blacklisting dvb_usb_rtl28xxu kernel module..."
    echo "blacklist dvb_usb_rtl28xxu" | sudo tee "$BLACKLIST" > /dev/null
    sudo modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
fi

# ── 3. dump1090 ───────────────────────────────────────────────────────────────

if ! command -v dump1090 &>/dev/null && ! command -v dump1090-fa &>/dev/null; then
    info "Installing dump1090-fa..."
    if apt-cache show dump1090-fa &>/dev/null 2>&1; then
        sudo apt-get install -y -qq dump1090-fa
    else
        info "dump1090-fa not in apt — building from source..."
        TMPDIR=$(mktemp -d)
        git clone --depth 1 https://github.com/flightaware/dump1090.git "$TMPDIR/dump1090"
        make -C "$TMPDIR/dump1090" -j"$(nproc)"
        sudo cp "$TMPDIR/dump1090/dump1090" /usr/local/bin/
        rm -rf "$TMPDIR"
    fi
else
    info "dump1090 already present — skipping."
fi

# ── 4. AIS-catcher ───────────────────────────────────────────────────────────

if ! command -v AIS-catcher &>/dev/null; then
    info "Building AIS-catcher from source..."
    TMPDIR=$(mktemp -d)
    git clone --depth 1 https://github.com/jvde-github/AIS-catcher.git "$TMPDIR/AIS-catcher"
    cmake -S "$TMPDIR/AIS-catcher" -B "$TMPDIR/AIS-catcher/build"
    make -C "$TMPDIR/AIS-catcher/build" -j"$(nproc)"
    sudo make -C "$TMPDIR/AIS-catcher/build" install
    rm -rf "$TMPDIR"
else
    info "AIS-catcher already present — skipping."
fi

# ── 5. Python virtual environment ─────────────────────────────────────────────

if [ ! -d "$VENV" ]; then
    info "Creating Python virtual environment..."
    python3 -m venv "$VENV"
fi

info "Installing Python dependencies..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$REPO_DIR"

# ── 6. Config file ────────────────────────────────────────────────────────────

if [ ! -f "$REPO_DIR/config.toml" ] && [ -f "$REPO_DIR/config.toml.example" ]; then
    info "Copying config.toml.example → config.toml"
    cp "$REPO_DIR/config.toml.example" "$REPO_DIR/config.toml"
fi

# ── 7. Verify dongle ─────────────────────────────────────────────────────────

echo ""
info "Setup complete. Testing RTL-SDR dongle (plug it in if not already)..."
echo ""
if rtl_test -t 2>&1 | grep -q "Found"; then
    info "Dongle detected successfully."
else
    echo "[WARN]  Dongle not found. Connect it and run:  rtl_test -s 2048000"
fi

# ── 8. Systemd service hint ───────────────────────────────────────────────────

echo ""
echo "To install the systemd service (optional):"
echo "  sudo cp $REPO_DIR/deploy/sdr-mcp.service /etc/systemd/system/"
echo "  sudo sed -i 's|__USER__|$USER|g; s|__REPO_DIR__|$REPO_DIR|g' /etc/systemd/system/sdr-mcp.service"
echo "  sudo systemctl enable --now sdr-mcp"
echo ""
echo "To connect from Claude Desktop, see RASPBERRY_PI.md."
