"""اختبارات سلامة سلسلة الـmigrations.

السبب: `e7f2a1b3c4d5` كان بيعمل `add_column` لعمودين `c32c9e6af771` كان قد
ضافهم قبلها بساعتين. أي بيئة جديدة بتتبني من السلسلة كانت بتموت بـ
`DuplicateColumn` في نص الطريق. محدش واخد باله لأن داتابيز التطوير اتعملت
بـ`create_all()` من الـmodels وبعدين اتـstamp، فالسلسلة عمرها ما اشتغلت من
الأول للآخر.

ودي regresions على الطبقة دي تحديدًا:
- السلسلة ماشية من base لـhead على داتابيز فاضية.
- مفيش عمود بيتزاد مرتين ولا جدول بيتعمل مرتين.
- الـschema المبني من الـmigrations مطابق للـmodels.

المرجع: `tools/migration_conflicts.py` و`tools/schema_diff.py` وسلسلة
`alembic/versions/`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect


class TestChainStructure:
    def test_chain_is_linear(self) -> None:
        """مفيش تشعّب ولا migration يتيم — تشعّب كده بيسيب جداول على
        فرع تانية ما بتتطبقش، و`upgrade head` بيمشي على واحد بس فيهم."""
        from tools.migration_report import load, order_chain

        names, down, _ = load()
        chain, breaks = order_chain(down)

        assert breaks == [], f"migration chain branches: {breaks}"
        assert len(chain) == len(names), (
            f"{len(names) - len(chain)} migration(s) are not on the main chain"
        )

    def test_every_revision_id_is_unique(self) -> None:
        from tools.migration_report import load

        names, _, _ = load()
        assert len(names) == len(set(names))

    def test_the_new_login_attempts_migration_is_on_the_chain(self) -> None:
        from tools.migration_report import load, order_chain

        names, down, _ = load()
        chain, _ = order_chain(down)
        assert "c1d2e3f4a5b6" in chain

    def test_the_schema_alignment_migration_is_on_the_chain(self) -> None:
        from tools.migration_report import load, order_chain

        names, down, _ = load()
        chain, _ = order_chain(down)
        assert "b2c3d4e5f6a7" in chain
        assert names["b2c3d4e5f6a7"] == "b2c3d4e5f6a7_align_schema_with_models.py"

    def test_the_phase1_branches_migration_is_on_the_chain(self) -> None:
        from tools.migration_report import load, order_chain

        names, down, _ = load()
        chain, _ = order_chain(down)
        # Phase 2 أضافت migration فوق Phase 1، بس Phase 1 لازم تفضل
        # متربّعة عليها: لو الـdown_revision اتغيّر، كل الـdeployments
        # القديمة اللي وصلت لـ`c3d4e5f6a7b8` بقى عندها مسار تاني.
        assert "c3d4e5f6a7b8" in chain
        assert names["c3d4e5f6a7b8"] == "c3d4e5f6a7b8_phase1_branches.py"
        assert down["c3d4e5f6a7b8"] == "b2c3d4e5f6a7"

    def test_the_head_follows_the_latest_phase(self) -> None:
        """الـhead بيتغيّر مع كل مرحلة — الـassertion الثابت بيكسر.

        **ليه مش هنمسك الـhead دايمًا:** كل phase جديدة بتضيف
        migration فوق، فلو الاختبار Pins revision معيّن زي
        `d4e5f6a7b8c9` هيفشل مع كل مرحلة جديدة.

        اللي بيتحقق منه فعلاً ويفضل صحيح:
        1. الـPhase 2 لسه في السلسلة، ومربّعة على Phase 1.
        2. آخر phase (الـhead الحالي) مبنية **مباشرة** على اللي قبلها.
        3. الـdown_revision بتاع الـhead بيودّي لنفس السلسلة.
        """
        from tools.migration_report import load, order_chain

        names, down, _ = load()
        chain, _ = order_chain(down)

        # (1) دي الـinvariant الحقيقية: لو الـdown_revision بتاع Phase 2
        #     اتغيّر، الـdeployments القديمة بقى عندها مسار تاني.
        assert "d4e5f6a7b8c9" in chain
        assert names["d4e5f6a7b8c9"] == "d4e5f6a7b8c9_phase2_payments_tracking.py"
        assert down["d4e5f6a7b8c9"] == "c3d4e5f6a7b8"

        # (2) و(3) الـhead بيتتبع آخر phase مضافة (Phase 5 — واتساب).
        # لو الملف اتغيّر، الاختبار هيفصل هنا والرسالة هتبين.
        assert chain[-1] == "b8c9d0e1f2a3"
        assert names[chain[-1]] == "b8c9d0e1f2a3_phase5_whatsapp_sending.py"
        assert down["b8c9d0e1f2a3"] == "a7b8c9d0e1f2"


class TestNoDuplicateDdl:
    @staticmethod
    def _body(source: str, name: str) -> str:
        """جسم الدالة بس — من غير الـdocstring، عشان الكلمة المفتاحية
        بتظهر في الشرح كمان."""
        if name == "upgrade":
            return source.split("def upgrade", 1)[-1].split("def downgrade", 1)[0]
        return source.split("def downgrade", 1)[1]

    def test_no_column_is_added_twice(self) -> None:
        """ده اللي كان بيقتل البناء من الصفر."""
        from tools.migration_conflicts import main

        assert main() == 0, "duplicate add_column/create_table in the chain"

    def test_the_known_bug_is_actually_fixed(self) -> None:
        """فحص موجّه: العمودين اللي كانوا بيتزادوا مرتين.

        `e7f2a1b3c4d5` كان بيضيف أعمدة `c32c9e6af771` كان قد زادها.
        لازم Uses alter_column، مش add_column.
        """
        from pathlib import Path

        from tools.migration_report import load

        names, _, _ = load()
        source = (Path("alembic/versions") / names["e7f2a1b3c4d5"]).read_text(encoding="utf-8")
        upgrade = self._body(source, "upgrade")

        assert "op.add_column" not in upgrade
        assert "op.alter_column" in upgrade

    def test_downgrades_do_not_drop_columns_they_do_not_own(self) -> None:
        """`e7f2a1b3c4d5` ما بتضيفش الأعمدة، فـdowngrade بتاعها ما ينفعش
        يمسحها — كده الـdowngrade لحد `c32c9e6af771` بيفقد بيانات."""
        from pathlib import Path

        from tools.migration_report import load

        names, _, _ = load()
        source = (Path("alembic/versions") / names["e7f2a1b3c4d5"]).read_text(encoding="utf-8")
        assert "op.drop_column" not in self._body(source, "downgrade")


class TestSchemaMatchesModels:
    def test_no_drift(self, test_database_url: str) -> None:
        """الـschema المبني من الـmigrations لازم يطابق الـmodels بالظبط.

        ده اللي كان هيلقط `orders.payment_method`: العمود كان في الـmodel
        ومفيش migration في السلسلة عامله.
        """
        from app.database import Base
        from app import models  # noqa: F401
        from tools.schema_diff import compare

        engine = create_engine(test_database_url)
        try:
            problems = compare(Base.metadata, inspect(engine))
        finally:
            engine.dispose()

        assert problems == [], "\n".join(problems)

    def test_payment_method_exists_after_a_fresh_build(self, test_database_url: str) -> None:
        """العمود اللي مفيش migration كان بيضيفه — لازم يكون موجود دلوقتي."""
        engine = create_engine(test_database_url)
        try:
            columns = {c["name"] for c in inspect(engine).get_columns("orders")}
        finally:
            engine.dispose()
        assert "payment_method" in columns

    def test_order_items_product_id_is_indexed(self, test_database_url: str) -> None:
        engine = create_engine(test_database_url)
        try:
            indexes = {i["name"] for i in inspect(engine).get_indexes("order_items")}
        finally:
            engine.dispose()
        assert "ix_order_items_product_id" in indexes

    def test_dead_is_pickup_column_is_gone(self, test_database_url: str) -> None:
        """`delivery_type` حل محل `is_pickup` — مينفعش الاتنين مع بعض."""
        engine = create_engine(test_database_url)
        try:
            columns = {c["name"] for c in inspect(engine).get_columns("delivery_options")}
        finally:
            engine.dispose()
        assert "delivery_type" in columns
        assert "is_pickup" not in columns

    def test_phone_is_unique(self, test_database_url: str) -> None:
        """الـdedup على التليفون بيعتمد على uniqueness.

        ملاحظة: بعد تنضيف الـindex المكرر، الـmodel بيصرّح بـ
        `unique=True, index=True` وده SQLAlchemy بيسلّقه في index واحد
        اسمه `ix_customers_phone` بـunique=true. فالـconstraint المسمّى
        مش موجود، والـuniqueness متحققة بالـindex.
        """
        engine = create_engine(test_database_url)
        try:
            insp = inspect(engine)
            unique_constraint_columns = {
                col
                for u in insp.get_unique_constraints("customers")
                for col in (u.get("column_names") or [])
            }
            unique_index_columns = {
                col
                for i in insp.get_indexes("customers")
                if i.get("unique")
                for col in i["column_names"]
            }
        finally:
            engine.dispose()

        assert "phone" in unique_constraint_columns | unique_index_columns

    def test_no_duplicate_unique_index_on_phone(self, test_database_url: str) -> None:
        """كان في indexين unique على نفس العمود — واحد فيهم زيادة."""
        engine = create_engine(test_database_url)
        try:
            insp = inspect(engine)
            uniques = {
                u.get("name") for u in insp.get_unique_constraints("customers") if u.get("name")
            }
            unique_indexes = {
                i["name"] for i in insp.get_indexes("customers") if i.get("unique")
            }
        finally:
            engine.dispose()
        # الـindex الحامي للـconstraint مش زيادة
        assert len(unique_indexes - uniques) == 1


class TestLoginAttemptsTable:
    def test_table_exists_with_the_expected_columns(self, test_database_url: str) -> None:
        engine = create_engine(test_database_url)
        try:
            columns = {
                c["name"]: c
                for c in inspect(engine).get_columns("admin_login_attempts")
            }
        finally:
            engine.dispose()

        assert set(columns) == {"id", "ip_hash", "key_hash", "attempted_at"}
        assert columns["ip_hash"]["nullable"] is False
        assert columns["key_hash"]["nullable"] is False
        assert columns["attempted_at"]["nullable"] is False

    def test_hash_columns_are_varchar_64(self, test_database_url: str) -> None:
        """64 hex chars = SHA-256. لو بقى أقل، التجزئة بتتقص."""
        engine = create_engine(test_database_url)
        try:
            columns = {
                c["name"]: c
                for c in inspect(engine).get_columns("admin_login_attempts")
            }
        finally:
            engine.dispose()

        assert columns["ip_hash"]["type"].length == 64
        assert columns["key_hash"]["type"].length == 64

    def test_composite_indexes_exist_for_the_counter_queries(
        self, test_database_url: str
    ) -> None:
        engine = create_engine(test_database_url)
        try:
            indexes = {
                i["name"]: i["column_names"]
                for i in inspect(engine).get_indexes("admin_login_attempts")
            }
        finally:
            engine.dispose()

        assert indexes.get("ix_admin_login_attempts_key_time") == ["key_hash", "attempted_at"]
        assert indexes.get("ix_admin_login_attempts_ip_time") == ["ip_hash", "attempted_at"]

    def test_no_unique_constraint_on_the_hashes(self, test_database_url: str) -> None:
        """الـhash مش unique — نفس الـIP بيسجل أكتر من محاولة."""
        engine = create_engine(test_database_url)
        try:
            uniques = inspect(engine).get_unique_constraints("admin_login_attempts")
        finally:
            engine.dispose()
        assert uniques == []
