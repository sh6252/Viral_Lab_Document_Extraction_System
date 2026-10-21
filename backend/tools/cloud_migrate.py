"""Explicit Neon-only migration/check command; never invoked by web startup."""
import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from backend.persistence.cloud_database import check_business_schema, create_cloud_engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only readiness verification")
    parser.add_argument("--confirm-synthetic-database", help="Exact new synthetic database name; authorizes migration")
    args = parser.parse_args()
    name = os.environ.get("DEMO_DATABASE_NAME", "")
    if os.environ.get("DEMO_DATA_POLICY") != "synthetic-only" or (not args.check and args.confirm_synthetic_database != name):
        raise SystemExit("EXPLICIT_SYNTHETIC_DATABASE_CONFIRMATION_REQUIRED")
    engine = None
    try:
        engine = create_cloud_engine(os.environ.get("REVIEW_DATABASE_DSN", ""), name)
        if not args.check:
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            with engine.begin() as connection:
                if tuple(connection.execute(text("SELECT current_database(), current_schema()")).one()) != (name, "public"):
                    raise ValueError
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        check_business_schema(engine, name)
        print("CLOUD_SCHEMA_READY: 0003_form_approval; critical checks and guards verified")
    except Exception:
        raise SystemExit("CLOUD_SCHEMA_CHECK_OR_MIGRATION_FAILED") from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
