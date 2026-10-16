"""Static analysis: every class used in admin templates vs classes defined in app.css + tailwind.css."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(r"F:\المشروع")
TEMPLATE_DIR = ROOT / "app" / "templates" / "admin"
CSS_FILES = [ROOT / "app" / "static" / "css" / "app.css", ROOT / "app" / "static" / "css" / "tailwind.css"]

used: set[str] = set()
for p in TEMPLATE_DIR.rglob("*.html"):
    text = p.read_text(encoding="utf-8", errors="replace")
    # strip Jinja blocks to avoid fake tokens
    text = re.sub(r"\{[{%][\s\S]*?[%}]\}", " ", text)
    for m in re.finditer(r'class=(["\'])(.*?)\1', text, re.S):
        for cls in m.group(2).split():
            if cls.startswith(("x-", "@", ":", "[")):
                continue
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-:/]*", cls):
                used.add(cls)

defined: set[str] = set()
variants = {"sm", "md", "lg", "xl", "2xl"}
for f in CSS_FILES:
    text = f.read_text(encoding="utf-8", errors="replace")
    # strip comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for m in re.finditer(r"([.#][\w\-.]+)(?=[,{])", text):
        token = m.group(1)
        if token.startswith("."):
            defined.add(token[1:])
    # tailwind responsive variants: .sm\:block etc.
    for m in re.finditer(r"\\([a-z0-9]+)\\:([\w\-.]+)", text):
        if m.group(1) in variants:
            defined.add(f"{m.group(1)}:{m.group(2)}")

# attribute selectors / pseudo appended classes handled loosely; also handle
# asterisks in compound like .divide-y > * (token .divide-y captured)
# composite selectors like .card h1 -> captured .card
missing = sorted(cls for cls in used if cls not in defined and not cls.startswith(("sm:", "md:", "lg:", "xl:")))
print(f"used={len(used)} defined={len(defined)}")
print("MISSING (used but never defined):")
for cls in missing:
    print(" ", cls)