"""check constraint blank emails

Revision ID: cb67ce467141
Revises: 63cd716e7107
Create Date: 2023-10-08 14:33:09.338971+01:00

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "cb67ce467141"
down_revision = "63cd716e7107"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM metadata.user_emails WHERE email_address = ''")
    op.create_check_constraint(
        op.f("ck_user_emails_email_address_not_blank"),
        "user_emails",
        "email_address ~ '@'",
        schema="metadata",
    )


def downgrade():
    op.drop_constraint(
        op.f("ck_user_emails_email_address_not_blank"),
        "user_emails",
        "check",
        schema="metadata",
    )
