"""Definitive audit v2: robust substring check for each used class."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path("F:\\المشروع")
TPL_DIR = ROOT / "app" / "templates"
CSS_TEXT = ""
for f in [ROOT / "app" / "static" / "css" / "app.css", ROOT / "app" / "static" / "css" / "tailwind.css"]:
    CSS_TEXT += f.read_text(encoding="utf-8", errors="replace")

used: set[str] = set()
freq: dict[str, int] = {}
for p in TPL_DIR.rglob("*.html"):
    text = p.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\{[{%][\s\S]*?[%}]\}", " ", text)
    for m in re.finditer(r'class=(["\'])(.*?)\1', text, re.S):
        for cls in m.group(2).split():
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-\[\]/:]*", cls) and not cls.startswith("x-"):
                used.add(cls)
                freq[cls] = freq.get(cls, 0) + 1

# escaped forms like .hover\:bg-primary or .max-w-\[60\%\]
def escaped(cls: str) -> str:
    out = []
    for ch in cls:
        if ch in "\\:.[]%/":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)

missing = []
for cls in sorted(used):
    if cls in {"active:", "selected:", "group", "i"}:
        continue
    if cls in {"login-card", "login-shell", "topbar-brand-mobile"}:
        continue  # inline styles
    if cls in {"audienceType", "channel", "couponValid", "messageStepHasPreview", "scheduledMode",
               "scheduling", "orderChannel", "paymentMethods.length", "selectedAddressId",
               "selectedDeliveryId", "selectedPaymentMethodId", "method.id", "opt.id", "addr.id",
               "step"}:
        continue  # Alpine/JS tokens
    if "." in cls and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-\[\]]+\.[A-Za-z][A-Za-z0-9_.\-\[\]]+", cls):
        continue  # js property chains like addr.id
    if escaped(cls) not in CSS_TEXT:
        missing.append((cls, freq[cls]))

print(f"missing={len(missing)}")
for cls, n in missing:
    print(f"{n:>3}  {cls}")