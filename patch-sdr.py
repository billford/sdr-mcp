import re

path = "/Users/billfordx/Documents/mcp-sdr/.venv/lib/python3.14/site-packages/rtlsdr/librtlsdr.py"

missing = {
    "rtlsdr_set_dithering",
    "rtlsdr_set_gpio_input",
    "rtlsdr_get_gpio_bit",
    "rtlsdr_set_gpio_byte",
    "rtlsdr_get_gpio_byte",
    "rtlsdr_set_gpio_status",
}

with open(path, "r") as fh:
    lines = fh.readlines()

out = []
i = 0
while i < len(lines):
    line = lines[i]
    m = re.match(r'^(f = librtlsdr\.)(\w+)(.*\n)', line)
    if m and m.group(2) in missing:
        next_line = lines[i+1] if i+1 < len(lines) else ""
        out.append("try:\n")
        out.append(f"    {line.rstrip()}\n")
        if next_line.strip().startswith("f.restype"):
            out.append(f"    {next_line.rstrip()}\n")
            i += 1
        out.append("except AttributeError:\n")
        out.append("    pass  # Not available in this librtlsdr build\n")
    else:
        out.append(line)
    i += 1

with open(path, "w") as fh:
    fh.writelines(out)

print("Patched successfully")
