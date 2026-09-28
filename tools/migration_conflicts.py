"""أداة تشخيص: بتدوّر على تعارضات بتبوّظ بناء داتابيز جديد من الصفر.

السبب إن الـdev database اتعملت بـcreate_all من الـmodels وبعدين اتـstamp،
فسلسلة الـmigrations عمرها ما اشتغلت من الأول لآخر. أي عمود بيتزاد
مرتين، أو جدول بيتعمل مرتين، بيبوّظ أول نشر على بيئة جديدة.

    python -m tools.migration_conflicts
"""

from __future__ import annotations

import pathlib
import re
from collections import defaultdict
from typing import Dict, List

from tools.migration_report import order_chain, load

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

# add_column("t", sa.Column("c", ...))  /  add_column('t', sa.Column('c', ...))
ADD_COLUMN = re.compile(r"""add_column\(\s*['"]([\w.]+)['"]\s*,\s*sa\.Column\(\s*['"]([\w]+)['"]""")
# create_table("t", ...)
CREATE_TABLE = re.compile(r"""create_table\(\s*['"]([\w.]+)['"]""")
# drop_column("t", "c")
DROP_COLUMN = re.compile(r"""drop_column\(\s*['"]([\w.]+)['"]\s*,\s*['"]([\w]+)['"]""")


def main() -> int:
    names, down, _ = load()
    chain, _breaks = order_chain(down)

    added: Dict[str, List[str]] = defaultdict(list)
    created: Dict[str, List[str]] = defaultdict(list)
    dropped: Dict[str, List[str]] = defaultdict(list)

    for rev in chain:
        text = (VERSIONS / names[rev]).read_text(encoding="utf-8")
        #Migration files have separate upgrade() and downgrade() bodies;
        # only the upgrade() part counts.
        body = text.split("def upgrade", 1)[-1].split("def downgrade", 1)[0]
        for table, column in ADD_COLUMN.findall(body):
            added[f"{table}.{column}"].append(rev)
        for table in CREATE_TABLE.findall(body):
            created[table].append(rev)
        for table, column in DROP_COLUMN.findall(body):
            dropped[f"{table}.{column}"].append(rev)

    problems: List[str] = []

    for name, revs in sorted(created.items()):
        if len(revs) > 1:
            problems.append(f"table created more than once: {name} in {revs}")

    for name, revs in sorted(added.items()):
        if len(revs) > 1:
            problems.append(
                f"add_column on the same column more than once: {name} in {revs} "
                f"(a fresh `upgrade head` fails with DuplicateColumn)"
            )

    if not problems:
        print("no conflicts found in the chain")
        return 0

    print(f"{len(problems)} conflict(s) that break a fresh `alembic upgrade head`:")
    print()
    for p in problems:
        print(f"  ! {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
