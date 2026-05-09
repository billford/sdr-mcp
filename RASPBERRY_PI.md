# Raspberry Pi Remote SDR Setup

Run the sdr-mcp server on a Raspberry Pi with the RTL-SDR dongle attached, then connect Claude Desktop on your MacBook over the local network (or anywhere via Tailscale).

## Architecture

```
RTL-SDR Dongle
      ↓ USB
Raspberry Pi  (sdr-mcp + dump1090 + AIS-catcher)
      ↓ SSH stdio
MacBook  (Claude Desktop)
```

Claude Desktop launches the server over SSH — no daemon or open port needed on the Pi side.

## Hardware

| Component | Notes |
|-----------|-------|
| Raspberry Pi 4 or 5 | 2 GB RAM minimum, 4 GB recommended |
| MicroSD card | 16 GB+ class 10 |
| Power supply | Use the official Pi PSU — cheap supplies cause USB drop-outs |
| RTL-SDR dongle | Plug into one of the Pi's USB-A ports |

Pi Zero 2 W works for ADS-B only. Pi 4+ recommended for ADS-B + AIS simultaneously.

## Pi Setup

### 1. Flash the OS

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Choose:

- **OS:** Raspberry Pi OS Lite (64-bit, no desktop)
- **Advanced settings:** enable SSH, set hostname `sdr-pi`, set username/password

### 2. Clone the repo and run the setup script

```bash
ssh billfordx@sdr-pi.local

git clone https://github.com/billford/sdr-mcp.git
cd sdr-mcp
chmod +x pi-setup.sh
./pi-setup.sh
```

The script installs all system dependencies, builds librtlsdr (RTL-SDR Blog fork), dump1090, and AIS-catcher from source, creates the Python venv, and installs the package. Takes about 30–45 minutes on a Pi 4.

### 3. Verify the dongle

```bash
rtl_test -s 2048000
```

Should run for 60+ seconds with no dropped samples. If it fails, check `/etc/modprobe.d/blacklist-rtl.conf` exists (the setup script creates it).

## SSH Key Setup (MacBook)

Do this once from the MacBook:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_sdr -C "sdr-pi"
ssh-copy-id -i ~/.ssh/id_ed25519_sdr.pub billfordx@sdr-pi.local
```

Test (should connect without a password prompt):

```bash
ssh -i ~/.ssh/id_ed25519_sdr billfordx@sdr-pi.local echo "connected"
```

Optionally add a shortcut to `~/.ssh/config`:

```
Host sdr-pi
    HostName sdr-pi.local
    User billfordx
    IdentityFile ~/.ssh/id_ed25519_sdr
    StrictHostKeyChecking no
```

## Claude Desktop Configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sdr": {
      "command": "ssh",
      "args": [
        "-i", "/Users/billfordx/.ssh/id_ed25519_sdr",
        "-o", "StrictHostKeyChecking=no",
        "billfordx@sdr-pi.local",
        "/home/billfordx/sdr-mcp/.venv/bin/python -m sdr_mcp.server"
      ]
    }
  }
}
```

If you kept a local config as a fallback, rename the key (e.g. `"sdr-local"`) to avoid conflicts.

Restart Claude Desktop. The server starts on demand each time Claude connects — no daemon needed.

## Remote Access via Tailscale

To reach the Pi from outside your home network:

```bash
# On the Pi
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Install Tailscale on the MacBook from [tailscale.com](https://tailscale.com). Both devices join your Tailscale network automatically.

Update the Claude Desktop config — replace `sdr-pi.local` with the Tailscale hostname:

```json
"args": [
  "-i", "/Users/billfordx/.ssh/id_ed25519_sdr",
  "billfordx@sdr-pi",
  "/home/billfordx/sdr-mcp/.venv/bin/python -m sdr_mcp.server"
]
```

The Pi's Tailscale hostname is shown in the Tailscale admin console. With this in place the setup works from anywhere.

## Optional: Systemd Auto-Start

This is only needed if you want the server running independently (e.g., to accept connections from multiple clients). Claude Desktop manages the process itself via SSH stdio and doesn't need a daemon.

```bash
sudo cp ~/sdr-mcp/deploy/sdr-mcp.service /etc/systemd/system/
sudo sed -i \
    "s|__USER__|$USER|g; s|__REPO_DIR__|$HOME/sdr-mcp|g" \
    /etc/systemd/system/sdr-mcp.service
sudo systemctl enable --now sdr-mcp
sudo systemctl status sdr-mcp
```

## Antenna Placement

With the Pi physically separate from the MacBook, antenna placement becomes straightforward:

- Place near a north-facing window for Lake Erie AIS improvement
- Elevate above roofline for ADS-B range (outdoor antenna typically reaches 200–300 miles vs ~50 miles indoors)
- Mount in a weatherproof enclosure outside — Pi 4 + RTL-SDR + PoE hat = permanent outdoor installation with no battery or cable management

## Testing Checklist

- [ ] `rtl_test -s 2048000` runs clean on Pi for 60+ seconds
- [ ] `ssh billfordx@sdr-pi.local` connects without a password prompt
- [ ] Claude Desktop connects and `sdr_status` returns `dongle_connected: True`
- [ ] `start_adsb_monitor` → `get_aircraft` returns aircraft
- [ ] `start_ais_monitor` → `get_vessels` returns vessels
- [ ] (Optional) Tailscale connection works from outside local network
