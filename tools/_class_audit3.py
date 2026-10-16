"""Definitive audit v3: strict token-boundary presence check."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path("F:\\المشروع")
TPL_DIR = ROOT / "app" / "templates"
CSS_TEXT = ""
for f in [ROOT / "app" / "static" / "css" / "app.css", ROOT / "app" / "static" / "css" / "tailwind.css"]:
    CSS_TEXT += f.read_text(encoding="utf-8", errors="replace")

used: set[str] = set()
for p in TPL_DIR.rglob("*.html"):
    text = p.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\{[{%][\s\S]*?[%}]\}", " ", text)
    for m in re.finditer(r'class=(["\'])(.*?)\1', text, re.S):
        for cls in m.group(2).split():
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-\[\]/:]*", cls) and not cls.startswith("x-"):
                used.add(cls)


def escaped(cls: str) -> str:
    return "".join(("\\" + ch) if ch in "\\:.[]%/'" else ch for ch in cls)


SKIP = {"active:", "selected:", "group", "i", "login-card", "login-shell", "topbar-brand-mobile",
        "audienceType", "channel", "couponValid", "messageStepHasPreview", "scheduledMode",
        "scheduling", "orderChannel", "paymentMethods.length", "selectedAddressId",
        "selectedDeliveryId", "selectedPaymentMethodId", "method.id", "opt.id", "addr.id", "step"}

missing = []
for cls in sorted(used):
    if cls in SKIP or cls.endswith(".length"):
        continue
    pat = r"\." + escaped(cls) + r"(?=[{:,\s.>]|$)"
    if not re.search(pat, CSS_TEXT):
        # allow effective containment already handled by 'for' id selectors? no.
        missing.append(cls)

print(f"missing={len(missing)}")
for c in missing:
    print(" ", c)