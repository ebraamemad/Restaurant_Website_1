"""أداة تشخيص: يطبع سلسلة الـmigrations ويشوف لو فيه تعارضات.

    python -m tools.migration_report
"""

from __future__ import annotations

import pathlib
import re
from typing import Dict, List, Optional, Tuple

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def load() -> Tuple[Dict[str, str], Dict[str, Optional[str]], Dict[str, str]]:
    names: Dict[str, str] = {}
    down: Dict[str, Optional[str]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)", text, re.M)
        drev = re.search(r"^down_revision(?::[^=]*)?\s*=\s*['\"]([^'\"]+)", text, re.M)
        if not rev:
            continue
        names[rev.group(1)] = path.name
        down[rev.group(1)] = drev.group(1) if drev else None
    return names, down, {}


def order_chain(down: Dict[str, Optional[str]]) -> Tuple[List[str], List[str]]:
    """بيرجّع (السلسلة بالترتيب، نقاط الانقسام)."""
    children: Dict[Optional[str], List[str]] = {}
    for rev, parent in down.items():
        children.setdefault(parent, []).append(rev)

    breaks = [
        f"{parent} -> {sorted(kids)}"
        for parent, kids in children.items()
        if len(kids) > 1
    ]

    chain: List[str] = []
    current: Optional[str] = None
    seen = set()
    while current in children:
        nxt = sorted(children[current])[0]
        if nxt in seen:
            break
        seen.add(nxt)
        chain.append(nxt)
        current = nxt
    return chain, breaks


if __name__ == "__main__":
    names, down, _ = load()
    chain, breaks = order_chain(down)
    print(f"migrations on disk : {len(names)}")
    print(f"chain length       : {len(chain)}")
    print()
    if breaks:
        print("BRANCHES (سلسلة متشعبة — واحدة منهم بس بتبقى في الـheads):")
        for b in breaks:
            print(f"  ! {b}")
        print()
    orphans = [r for r in names if r not in chain]
    if orphans:
        print("NOT on the main chain:")
        for r in orphans:
            print(f"  - {r}  ({names[r]})  down={down[r]}")
        print()
    print("main chain:")
    for i, rev in enumerate(chain, 1):
        print(f"  {i:2d}. {rev}  {names[rev]}")
