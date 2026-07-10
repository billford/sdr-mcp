# Multi-Mac Split Setup (HackRF + RTL-SDR on Separate Machines)

Run tune/scan/AIS against a HackRF plugged into one Mac, and ADS-B against
an RTL-SDR plugged into a different Mac, both reachable from a single
Claude Desktop instance.

## Architecture

```
HackRF One                          RTL-SDR Dongle
      ↓ USB                               ↓ USB
lola  (sdr-mcp, HackRF)             wanderlust  (sdr-mcp, RTL-SDR)
      ↓ SSH stdio                         │ local stdio
      └──────────────→ Claude Desktop ←───┘
```

Claude Desktop runs **two separate MCP server entries** against the same
codebase: one launched locally (talks to whatever hardware is physically
attached to that Mac), one launched over SSH into the other Mac. Each
process only sees its own machine's USB devices, so each one correctly
reports the other device as absent — that's expected, not a bug.

## Why not just share one folder between the Macs?

The instinctive setup — put the repo in an iCloud-synced `Documents`
folder and let both Macs run out of the same copy — breaks in a specific
way: `.venv` bakes in absolute, user- and machine-specific paths
(`pyvenv.cfg`'s `command =` line points at the exact `python -m venv`
invocation, including the account home directory that created it). If
one Mac's account creates the venv and it syncs to the other Mac's
different account/home directory, every symlink inside `.venv/bin`
resolves to nothing on the second machine.

The fix: only **one** Mac (whichever you actually edit code on) keeps the
repo in a synced folder. Every other machine gets its own real git clone
in a non-synced location, with its own `.venv` built locally.

## One-Time Setup

### 1. SSH key (from the Claude Desktop machine)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_sdr_lola -C "sdr-lola"
ssh-copy-id -i ~/.ssh/id_ed25519_sdr_lola.pub <user>@lola.local
```

Verify passwordless login:

```bash
ssh -i ~/.ssh/id_ed25519_sdr_lola <user>@lola.local echo connected
```

### 2. Clone and build on the hardware Mac (`lola`)

```bash
ssh <user>@lola.local
git clone https://github.com/billford/sdr-mcp.git ~/mcp-sdr
cd ~/mcp-sdr
/opt/homebrew/bin/python3.13 -m venv .venv   # match whatever python3.1x brew has
.venv/bin/pip install -e .
brew install hackrf
hackrf_info   # should print "Found HackRF" and a serial number
```

Keep this clone **outside** any iCloud/Documents/Dropbox-synced path.

### 3. Rebuild the venv on the Claude Desktop Mac too

If your editing copy's `.venv` was ever synced from (or to) another
machine, don't trust it — rebuild it locally:

```bash
cd ~/Documents/mcp-sdr
rm -rf .venv
/opt/homebrew/bin/python3.1x -m venv .venv
.venv/bin/pip install -e .
```

### 4. Claude Desktop configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`.
Note the `PATH=` prefix on the remote command — non-interactive SSH
shells don't source `.zprofile`, so Homebrew's `hackrf_info`/
`hackrf_transfer` won't be found without it:

```json
{
  "mcpServers": {
    "sdr-hackrf-lola": {
      "command": "/usr/bin/ssh",
      "args": [
        "-i", "/Users/billfordx/.ssh/id_ed25519_sdr_lola",
        "billmathews@lola.local",
        "PATH=/opt/homebrew/bin:$PATH /Users/billmathews/mcp-sdr/.venv/bin/python -m sdr_mcp.server"
      ]
    },
    "sdr-adsb-local": {
      "command": "/Users/billfordx/Documents/mcp-sdr/.venv/bin/python",
      "args": ["-m", "sdr_mcp.server"],
      "env": {
        "PYTHONPATH": "/Users/billfordx/Documents/mcp-sdr"
      }
    }
  }
}
```

Restart Claude Desktop. Both servers start on demand — no daemon needed
on either machine.

## Keeping the Remote Clone Updated

Edit and commit on your normal (synced) working copy, push to `origin`,
then on the remote machine:

```bash
ssh <user>@lola.local
cd ~/mcp-sdr
git pull
.venv/bin/pip install -e .   # only if dependencies changed
```

## Testing Checklist

- [ ] `ssh -i ~/.ssh/id_ed25519_sdr_lola <user>@lola.local echo connected` — no password prompt
- [ ] `hackrf_info` on the HackRF Mac prints `Found HackRF` and a serial
- [ ] Claude Desktop shows both `sdr-hackrf-lola` and `sdr-adsb-local` connected in its MCP logs
- [ ] Hardware status via `sdr-hackrf-lola` reports `dongle_connected: true`
- [ ] Hardware status via `sdr-adsb-local` reports the RTL-SDR's state (connected only if actually plugged in)
- [ ] Neither `.venv` directory lives inside a synced/shared folder
