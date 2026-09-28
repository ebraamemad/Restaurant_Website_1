"""أداة تشخيص صغير: يطبع قيود وفهارس جدول واحد من الـmodels ومن الداتابيز.

    python -m tools.show_table customers
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect

from app.config import settings
from app.database import Base
from app import models  # noqa: F401


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "customers"
    table = Base.metadata.tables[name]

    print(f"--- models: {name}")
    for constraint in sorted(table.constraints, key=lambda c: c.__class__.__name__):
        print(f"  {constraint.__class__.__name__}: {constraint}")
    for index in sorted(table.indexes, key=lambda i: i.name or ""):
        print(f"  Index: {index.name} unique={index.unique} cols={[c.name for c in index.columns]}")

    engine = create_engine(settings.database_url)
    insp = inspect(engine)
    print(f"--- database: {name}")
    for unique in insp.get_unique_constraints(name):
        print(f"  UniqueConstraint: {unique}")
    for index in insp.get_indexes(name):
        print(f"  Index: {index['name']} unique={index['unique']} cols={index['column_names']}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
