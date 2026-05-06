path = "/Users/billfordx/Documents/mcp-sdr/.venv/lib/python3.14/site-packages/rtlsdr/rtlsdr.py"
with open(path, "r") as f:
    content = f.read()

old = "result = librtlsdr.rtlsdr_set_dithering(self.dev_p, int(dithering_enabled))"
new = ("if hasattr(librtlsdr, 'rtlsdr_set_dithering'):\n"
       "                result = librtlsdr.rtlsdr_set_dithering(self.dev_p, int(dithering_enabled))")

if old in content:
    content = content.replace(old, new)
    with open(path, "w") as f:
        f.write(content)
    print("Patched")
else:
    print("Pattern not found - check the file manually")
EOF
