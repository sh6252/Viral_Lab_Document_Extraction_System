"""Allow one truthful form-level audit event; retain all original rows/triggers."""
from alembic import op
import sqlalchemy as sa

revision = "0003_form_approval"
down_revision = "0002_business_persistence"
branch_labels = None
depends_on = None

STATEMENTS = (
    "ALTER TABLE business_review_events DROP CONSTRAINT ck_business_event_field",
    "ALTER TABLE business_review_events ADD CONSTRAINT ck_business_event_field CHECK (field_name IN "
    "('first_name','last_name','israeli_id','central_numbers','sample_receipt_date','sender','commitment_numbers','__form__'))",
)


def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError("FORM_APPROVAL_DOWNGRADE_REQUIRES_EXPLICIT_ARCHIVAL_PLAN")
