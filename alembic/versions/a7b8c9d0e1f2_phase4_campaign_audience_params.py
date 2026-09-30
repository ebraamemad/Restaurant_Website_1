"""Phase 4b: store the audience params a campaign was previewed with

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-29 22:40:00.000000

## المشكلة

`segments` بتبني الجمهور من معاملات: `recently_active_days`,
`inactive_days`, `high_spending_minimum`. `Campaign` كان بيخزّن
`audience_type` و`segment_key` بس، **مش المعاملات نفسها**.

النتيجة: الأدمن بيفتح المعاينة، يحط "آخر 7 أيام"، بيشوف **180 عميل**،
يضغط "إرسال" — و`send_campaign` بيبني `AudienceParams()` بالـdefaults
(آخر 30 يوم) فيبعت لـ**520 عميل**. صامت تمامًا: مفيش exception،
والتقرير بيقول أرقام صح بس بتعلى شريحة مش اللي Approved.

الأسوأ إن **الأرقام في التقرير بتكون غلط بشكل غير مرئي** — مفيش
حد يقدر يعرف إن 520 ده مش 180 غير لما يشوف الأسماء.

## الحل

عمود `audience_params` JSON على `campaigns`. `create_campaign`
بيحفظ الـparams بالظبط، و`send_campaign` بيقرأها ويبني منها
`AudienceParams`.

`server_default='{}'` مش `NULL`: الحملات الموجودة (لو في production)
مالهاش قيمة، و`parse_params` بيرجّع الـdefaults على `{}` — فالسطر
القديم بيشتغل زي ما كان بالظبط. ده اللي `downgrade` بيرجّعه برضه.

## ليه `JSON` مش `JSONB`

`audience_params` بيتقري مرة واحدة وقت الإرسال وبيتكتب مرة واحدة
وقت الإنشاء. مفيش استعلام على محتواه (مفيش حاجة بتدوّر على
"الحملات اللي استخدمت 7 أيام"). `JSON` بيخزّن النص كما هو بالظبط —
أدق وأسرع في الكتابة، وبيتجنب إعادة ترتيب المفاتيح اللي `JSONB`
بيعملها. لو احتجنا استعلام على المعاملات بعدين، التحويل لـ`JSONB`
هو `ALTER TABLE ... USING` واحدة.

## `downgrade`

بيحذف العمود. الحملات اللي اتبعت بـparams مخصوصة هتفضل شغالة، بس
الإعادة للإرسال هترجع للـdefaults — وده بالظبط السلوك اللي كنا
بنحاول نصلّحه.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column(
            "audience_params",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "audience_params")
