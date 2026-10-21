"""Neon TLS validation and read-only schema gates. No automatic migrations."""
import re
from importlib import import_module

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

EXPECTED_REVISION = "0003_form_approval"


def neon_url(dsn: str, expected_database: str) -> URL:
    try:
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://"):]
        url = make_url(dsn)
        if (url.drivername not in {"postgresql", "postgresql+psycopg"}
                or not url.host or not url.host.endswith(".neon.tech") or "-pooler" in url.host
                or url.port not in {None, 5432} or not url.username or not url.password
                or url.database != expected_database or "synthetic" not in expected_database
                or url.query.get("sslmode") not in {"require", "verify-full"}
                or url.query.get("channel_binding") != "require"
                or set(url.query) - {"sslmode", "channel_binding", "connect_timeout"}):
            raise ValueError
        return url.set(drivername="postgresql+psycopg")
    except Exception:
        raise ValueError("DIRECT_NEON_SYNTHETIC_TLS_DSN_REQUIRED") from None


def create_cloud_engine(dsn: str, expected_database: str) -> Engine:
    return create_engine(neon_url(dsn, expected_database), echo=False, hide_parameters=True,
                         pool_size=2, max_overflow=0, pool_timeout=10, pool_pre_ping=True,
                         connect_args={"connect_timeout": 10,
                                       "options": "-c statement_timeout=15000 -c lock_timeout=5000"})


def _normalized(value: str) -> str:
    return re.sub(r"[\s()\"]|::text|::bigint|::integer", "", value)


CRITICAL_CHECKS = {
    ("business_documents", "ck_business_source_size"): "CHECK (octet_length(source_bytes) > 0 AND octet_length(source_bytes) <= 20971520)",
    ("business_documents", "ck_business_review_token"): "CHECK (length(review_token) >= 32)",
    ("business_documents", "ck_business_source_hash"): "CHECK (source_sha256 ~ '^[0-9a-f]{64}$')",
    ("business_records", "ck_business_record_revision"): "CHECK (ordinal >= 0 AND revision >= 0)",
    ("business_records", "ck_business_projection_hash"): "CHECK (projection_sha256 ~ '^[0-9a-f]{64}$')",
    ("business_review_events", "ck_business_event_revision"): "CHECK (previous_revision >= 0 AND revision = previous_revision + 1)",
    ("business_review_events", "ck_business_event_field"): "CHECK (field_name = ANY (ARRAY['first_name','last_name','israeli_id','central_numbers','sample_receipt_date','sender','commitment_numbers','__form__']))",
    ("business_review_events", "ck_business_event_hash"): "CHECK (event_sha256 ~ '^[0-9a-f]{64}$')",
}

KEY_CONSTRAINTS = {
    ("business_documents", "business_documents_pkey"): "PRIMARY KEY (document_id)",
    ("business_records", "business_records_pkey"): "PRIMARY KEY (document_id, form_id)",
    ("business_records", "uq_business_form_ordinal"): "UNIQUE (document_id, ordinal)",
    ("business_records", "business_records_document_id_fkey"): "FOREIGN KEY (document_id) REFERENCES business_documents(document_id) ON DELETE RESTRICT",
    ("business_review_events", "business_review_events_pkey"): "PRIMARY KEY (document_id, form_id, decision_id)",
    ("business_review_events", "uq_business_event_revision"): "UNIQUE (document_id, form_id, revision)",
    ("business_review_events", "fk_business_event_record"): "FOREIGN KEY (document_id, form_id) REFERENCES business_records(document_id, form_id) ON DELETE RESTRICT",
}


def check_business_schema(engine: Engine, database: str, *, schema: str = "public") -> None:
    """Read-only exact revision/check/trigger validation, also used before each write."""
    try:
        with engine.connect() as conn:
            identity = conn.execute(text("SELECT current_database(), current_schema()")).one()
            if tuple(identity) != (database, schema):
                raise ValueError
            if conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [EXPECTED_REVISION]:
                raise ValueError
            checks = conn.execute(text("SELECT t.relname, c.conname, c.convalidated, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON c.conrelid=t.oid JOIN pg_namespace n ON t.relnamespace=n.oid "
                "WHERE n.nspname=:schema"), {"schema": schema}).all()
            actual = {(table, name): _normalized(definition) for table, name, valid, definition in checks if valid}
            if any(actual.get(key) != _normalized(expected) for key, expected in (CRITICAL_CHECKS | KEY_CONSTRAINTS).items()):
                raise ValueError
            guards = conn.execute(text("SELECT t.relname, g.tgname, g.tgenabled, g.tgtype, p.proname, p.prosrc "
                "FROM pg_trigger g JOIN pg_class t ON g.tgrelid=t.oid JOIN pg_namespace n ON t.relnamespace=n.oid "
                "JOIN pg_proc p ON g.tgfoid=p.oid WHERE n.nspname=:schema AND NOT g.tgisinternal"), {"schema": schema}).all()
            expected = {("business_documents", "business_documents_immutable", "O", 27, "business_documents_guard"),
                        ("business_records", "business_records_revision", "O", 27, "business_records_guard"),
                        ("business_review_events", "business_events_append_only", "O", 27, "business_events_guard")}
            if not expected.issubset({tuple(row[:5]) for row in guards}):
                raise ValueError
            frozen = import_module("migrations.versions.0002_business_persistence")
            bodies = {re.search(r"CREATE FUNCTION (\w+)", sql).group(1): _normalized(sql.split("$$")[1])
                      for sql in frozen.GUARDS if sql.startswith("CREATE FUNCTION")}
            if any(_normalized(row[5]) != bodies[row[4]] for row in guards if row[4] in bodies):
                raise ValueError
    except Exception:
        raise RuntimeError("CLOUD_DATABASE_NOT_READY") from None
