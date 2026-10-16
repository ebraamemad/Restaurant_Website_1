"""Definitive audit: classes used in ALL templates vs classes defined in CSS."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path("F:\\المشروع")
TPL_DIR = ROOT / "app" / "templates"
CSS_FILES = [ROOT / "app" / "static" / "css" / "app.css", ROOT / "app" / "static" / "css" / "tailwind.css"]

used: set[str] = set()
for p in TPL_DIR.rglob("*.html"):
    text = p.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\{[{%][\s\S]*?[%}]\}", " ", text)  # strip Jinja
    for m in re.finditer(r'class=(["\'])(.*?)\1', text, re.S):
        for cls in m.group(2).split():
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-\[\]/:]*", cls) and not cls.startswith("x-"):
                used.add(cls)

defined: set[str] = set()
for f in CSS_FILES:
    text = f.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    # capture selector tokens starting with dot (including escaped chars)
    for m in re.finditer(r"\.((?:\\[^ ]|[A-Za-z0-9_\-\[\])/:])+)", text):
        defined.add(m.group(1).replace("\\", ""))
    # also attribute-ish like .[hash]? nothing else needed

missing = sorted(cls for cls in used if cls not in defined)
print(f"used={len(used)} defined={len(defined)} missing={len(missing)}")
for c in missing:
    print(" ", c)