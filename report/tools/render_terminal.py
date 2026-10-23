#!/usr/bin/env python3
"""Render captured terminal output as PNG figures for the report."""
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"
IMG = ROOT / "images"
IMG.mkdir(parents=True, exist_ok=True)

FONT_REG = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"
SIZE = 19
LINE_H = 28
MAX_COLS = 110
PAD_X = 26
PAD_Y = 22
TITLE_H = 46

BG = "#0d1117"
HEADER = "#161b22"
BORDER = "#30363d"
NORMAL = "#c9d1d9"
MUTED = "#8b949e"
GREEN = "#3fb950"
RED = "#f85149"
CYAN = "#56d4dd"
YELLOW = "#d29922"
BLUE = "#79c0ff"
ORANGE = "#ffa657"

font = ImageFont.truetype(FONT_REG, SIZE)
font_bold = ImageFont.truetype(FONT_BOLD, SIZE)


def clean(text: str, default_prompt: str | None = None) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
    lines: list[str] = []
    pending = None
    for raw in text.split("\n"):
        line = raw.rstrip().replace("\t", "    ")
        if re.fullmatch(r"(PC_[A-D]|R\d)", line.strip()):
            pending = line.strip()
            continue
        if pending:
            line = f"{pending}> {line.strip()}" if line.startswith(" ") else f"{pending}>"
            pending = None
        lines.append(line)
    if pending:
        lines.append(f"{pending}>")
    if default_prompt:
        for i, line in enumerate(lines):
            if line.startswith(" ") and line.strip():
                lines[i] = f"{default_prompt}> {line.strip()}"
                break
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return out


def wrap_line(line: str, width: int = MAX_COLS) -> list[str]:
    if len(line) <= width:
        return [line]
    indent = re.match(r"\s*", line).group()
    chunks: list[str] = []
    cur = line
    while len(cur) > width:
        cut = cur.rfind(" ", max(len(indent) + 8, 0), width)
        if cut <= len(indent):
            cut = width
        chunks.append(cur[:cut].rstrip())
        cur = indent + "  " + cur[cut:].lstrip()
    chunks.append(cur)
    return chunks


def line_style(line: str):
    s = line.strip()
    if not s:
        return NORMAL, font
    if s.startswith("[PASS]"):
        return GREEN, font_bold
    if s.startswith("[FAIL]") or "ERROR" in s or "Traceback" in s:
        return RED, font_bold
    if s.startswith("===") or s.startswith("---") or s.startswith("###"):
        return CYAN, font_bold
    if s.startswith("[INFO]") or s.startswith("[WARN]"):
        return YELLOW, font_bold
    if re.match(r"^[CRS]>\*", line):
        return {"C": BLUE, "R": GREEN, "S": YELLOW}[line[0]], font
    if s.startswith("Codes:") or s.startswith("> - selected"):
        return MUTED, font
    if re.match(r"^(NAME|IP/MASK|GATEWAY|DNS|MAC|LPORT|RHOST:PORT|MTU)\s*:", line):
        return CYAN, font
    if re.match(r"^DHCP (SERVER|LEASE)\s*:", line):
        return GREEN, font_bold
    if "bytes from" in line:
        return GREEN, font
    if "no reply" in line.lower() or "100% packet loss" in line.lower():
        return RED, font
    return NORMAL, font


def render(lines: list[str], title: str, outfile: str, footer: str | None = None):
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(wrap_line(line))

    maxw = max(font.getlength(l) for l in wrapped) if wrapped else 600
    width = 1320
    body_h = len(wrapped) * LINE_H + PAD_Y * 2
    height = TITLE_H + body_h + (26 if footer else 0)

    im = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, width - 1, height - 1], radius=10, fill=BG, outline=BORDER, width=1)
    d.rectangle([0, 0, width, TITLE_H], fill=HEADER)
    d.line([(0, TITLE_H), (width, TITLE_H)], fill=BORDER)

    for i, col in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        cx = 22 + i * 20
        d.ellipse([cx - 6, TITLE_H // 2 - 6, cx + 6, TITLE_H // 2 + 6], fill=col)
    d.text((94, (TITLE_H - SIZE) / 2 - 1), title, font=font_bold, fill="#d7dee8")
    right = "traphan@gns3-lab"
    rw = font.getlength(right)
    d.text((width - rw - 18, (TITLE_H - SIZE) / 2 - 1), right, font=font, fill=MUTED)

    y = TITLE_H + PAD_Y
    for line in wrapped:
        m = re.match(r"^([A-Za-z0-9_.\-]+[#>])\s(.*)$", line)
        if m and not line.strip().startswith("###"):
            d.text((PAD_X, y), m.group(1), font=font_bold, fill=BLUE)
            off = font.getlength(m.group(1) + " ")
            d.text((PAD_X + off, y), m.group(2), font=font, fill=ORANGE)
        else:
            color, fnt = line_style(line)
            d.text((PAD_X, y), line, font=fnt, fill=color)
        y += LINE_H

    if footer:
        d.line([(0, height - 26), (width, height - 26)], fill=BORDER)
        d.text((PAD_X, height - 22), footer, font=font, fill=MUTED)

    im.save(IMG / outfile)
    print(f"{outfile}: {width}x{height}, {len(wrapped)} lines")


def main():
    render(clean((ART / "doctor.txt").read_text(encoding="utf-8")),
           "doctor.sh — kiểm tra môi trường", "term-doctor.png")
    render(clean((ART / "setup.txt").read_text(encoding="utf-8")),
           "setup.sh — dựng topology trên GNS3", "term-setup.png")
    render(clean((ART / "test.txt").read_text(encoding="utf-8")),
           "test.sh — kiểm thử tự động", "term-test.png")

    raw = (ART / "routes-r0.txt").read_text(encoding="utf-8")
    raw = raw.replace("### show ip route", "R0# show ip route").replace(
        "### show ip rip status", "R0# show ip rip status")
    cut = raw.find("R0# show ip rip status")
    render(clean(raw[:cut]), "R0 — bảng định tuyến RIPv2", "term-routes.png")
    render(clean(raw[cut:]), "R0 — trạng thái tiến trình RIP", "term-rip.png")

    pca = clean((ART / "pc-a.txt").read_text(encoding="utf-8"), "PC_A")
    cut = next((i for i, l in enumerate(pca) if l.startswith("PC_A> ping")), len(pca))
    render(pca[:cut], "PC_A — nhận cấu hình từ DHCP", "term-dhcp.png")

    selected, skip = [], False
    for line in pca[cut:]:
        if line.startswith("PC_A> ping"):
            skip = "192.168.30.1" in line
        if not skip:
            selected.append(line)
    pcd = clean((ART / "pc-d.txt").read_text(encoding="utf-8"), "PC_D")
    cut_d = next((i for i, l in enumerate(pcd) if l.startswith("PC_D> ping")), len(pcd))
    render(selected + [""] + pcd[cut_d:],
           "PC_A / PC_D — kiểm tra kết nối liên mạng", "term-ping.png")

    cont = (ART / "containers.txt").read_text(encoding="utf-8").replace("GNS3.", "")
    render(clean(cont), "docker ps — container của lab", "term-containers.png")


if __name__ == "__main__":
    main()
