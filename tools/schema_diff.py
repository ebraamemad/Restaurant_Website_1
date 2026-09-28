"""أداة تشخيص: يقارن الـmodels بالـschema اللي مبنية من الـmigrations.

بيطبع الفروقات اللي `alembic check` بيلاقيها، بس بشكل أوضح — بيقول
لكل واحد: ده موجود في الداتابيز ومش في الـmodels، ولا العكس.

    $env:DATABASE_URL = python -m tools.test_db_url
    python -m tools.schema_diff
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

from app.config import settings
from app.database import Base
from app import models  # noqa: F401  (بيسجّل كل الـmodels على Base.metadata)


def compare(metadata, inspector) -> list[str]:
    """يقارن الـmodels بالـschema الموجودة ويرجّع قائمة الفروقات.

    قابل لإعادة الاستخدام: الأداة دي بتطبعها، والاختبارات بتفحص إنها فاضية.
    """
    db_tables = set(inspector.get_table_names()) - {"alembic_version"}
    model_tables = set(metadata.tables)

    problems: list[str] = []

    for table in sorted(model_tables - db_tables):
        problems.append(f"model table missing from database: {table}")
    for table in sorted(db_tables - model_tables):
        problems.append(f"table in database but not in models: {table}")

    for table in sorted(model_tables & db_tables):
        model_table = metadata.tables[table]
        db_columns = {c["name"] for c in inspector.get_columns(table)}
        model_columns = set(model_table.columns.keys())

        for column in sorted(model_columns - db_columns):
            problems.append(f"MISSING COLUMN  {table}.{column}  (in models, not in database)")
        for column in sorted(db_columns - model_columns):
            problems.append(f"EXTRA COLUMN    {table}.{column}  (in database, not in models)")

        db_uniques = inspector.get_unique_constraints(table)
        # PostgreSQL backs every unique constraint with a same-named index.
        # That index is an implementation detail of the constraint, not a
        # separate index, so it must not be reported as an extra index.
        backing_indexes = {u.get("name") for u in db_uniques if u.get("name")}

        db_indexes = {i["name"] for i in inspector.get_indexes(table)} - backing_indexes
        model_indexes = {i.name for i in model_table.indexes}
        for index in sorted(model_indexes - db_indexes):
            problems.append(f"MISSING INDEX   {table}.{index}")
        for index in sorted(db_indexes - model_indexes):
            problems.append(f"EXTRA INDEX     {table}.{index}")

        # Unique constraints have to be compared by their column set, not by
        # name: PostgreSQL always names them (e.g. "coupons_code_key") while a
        # model that says `unique=True` inline leaves the constraint unnamed.
        # Comparing names alone reports those as missing.

        def _column_set(column_lists) -> set[frozenset[str]]:
            return {frozenset(names) for names in column_lists if names}

        db_unique_sets = _column_set([u.get("column_names") or [] for u in db_uniques])
        model_unique_sets = _column_set(
            [
                [col.name for col in c.columns]
                for c in model_table.constraints
                if c.__class__.__name__ == "UniqueConstraint"
            ]
        )
        for name in sorted(db_unique_sets - model_unique_sets):
            problems.append(f"EXTRA UNIQUE    {table}({', '.join(sorted(name))})")
        for name in sorted(model_unique_sets - db_unique_sets):
            problems.append(f"MISSING UNIQUE  {table}({', '.join(sorted(name))})")

    return problems


def main() -> int:
    engine = create_engine(settings.database_url)
    try:
        problems = compare(Base.metadata, inspect(engine))
    finally:
        engine.dispose()

    if not problems:
        print("models and database schema agree")
        return 0

    print(f"{len(problems)} difference(s):\n")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
