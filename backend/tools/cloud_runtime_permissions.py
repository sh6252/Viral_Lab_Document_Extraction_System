"""Explicit, transactional Neon runtime grants. Default mode is read-only preflight."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import getpass
from hashlib import sha256
import hmac
import json
import os
import re
from uuid import uuid4
import warnings

from psycopg import ClientCursor, sql as pgsql
from sqlalchemy import Connection, text

from backend.persistence.cloud_database import check_business_schema, create_cloud_engine, neon_url

DATABASE = "viral_lab_submission_synthetic"
OWNER = "neondb_owner"
RUNTIME = "viral_lab_demo_runtime"
APP_RUNTIME = "viral_lab_app_runtime"
RUNTIME_ROLES = (RUNTIME, APP_RUNTIME)
BUSINESS_TABLES = frozenset({"business_documents", "business_records", "business_review_events"})
UPDATE_COLUMNS = {
    "business_documents": frozenset({"status", "extraction_id", "canonical_snapshot_json",
                                    "canonical_snapshot_sha256", "failure_json", "failure_sha256"}),
    "business_records": frozenset({"revision", "review_status", "projection_json", "projection_sha256"}),
}
# Fixed identifiers only. Keep this list identical to the deployment runbook.
GRANTS = (
    f"GRANT CONNECT ON DATABASE {DATABASE} TO {RUNTIME}",
    "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
    f"GRANT USAGE ON SCHEMA public TO {RUNTIME}",
    f"GRANT SELECT ON TABLE public.alembic_version TO {RUNTIME}",
    f"GRANT SELECT, INSERT ON TABLE public.business_documents, public.business_records, public.business_review_events TO {RUNTIME}",
    f"GRANT UPDATE (status, extraction_id, canonical_snapshot_json, canonical_snapshot_sha256, failure_json, failure_sha256) ON TABLE public.business_documents TO {RUNTIME}",
    f"GRANT UPDATE (revision, review_status, projection_json, projection_sha256) ON TABLE public.business_records TO {RUNTIME}",
)


class PermissionSafetyError(ValueError):
    """Only fixed, credential-free error codes may escape the operator interface."""


class _StageFailure(PermissionSafetyError):
    """A code assembled exclusively from internal stage names and an allowlist."""


def _stage_failure(stage: str, error: BaseException) -> _StageFailure:
    # Never stringify a driver/SQLAlchemy exception or inspect its SQL/params,
    # detail, hint, context, or HTTP response. Even a verifier is sensitive.
    state = getattr(getattr(error, "orig", error), "sqlstate", None)
    reason = {
        "42501": "INSUFFICIENT_PRIVILEGE", "25001": "TRANSACTION_BLOCK_REJECTED",
        "25006": "READ_ONLY_TRANSACTION", "0A000": "FEATURE_UNSUPPORTED",
        "28P01": "AUTHENTICATION_FAILED", "42710": "DUPLICATE_OBJECT",
        "42601": "SYNTAX_REJECTED", "22023": "INVALID_PARAMETER",
        "XX000": "SERVER_INTERNAL_ERROR", "40001": "SERIALIZATION_FAILURE",
        "40P01": "DEADLOCK", "57014": "CANCELLED_OR_TIMEOUT",
    }.get(state, "FAILED") if isinstance(state, str) else "FAILED"
    if isinstance(state, str) and re.fullmatch(r"08[A-Z0-9]{3}", state):
        reason = "CONNECTION_FAILED"
    if isinstance(error, KeyboardInterrupt):
        reason = "INTERRUPTED"
    elif isinstance(error, PermissionSafetyError):
        reason = "SAFETY_CHECK_REJECTED"
    return _StageFailure(f"NEW_RUNTIME_{stage}_{reason}")


def validate_target(dsn: str, confirmation: str | None) -> None:
    if confirmation != DATABASE:
        raise PermissionSafetyError("EXACT_SYNTHETIC_DATABASE_CONFIRMATION_REQUIRED")
    try:
        url = neon_url(dsn, DATABASE)
        if url.username != OWNER or not re.fullmatch(
            r"ep-[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)*\.neon\.tech", url.host or "",
        ):
            raise ValueError
    except Exception:
        raise PermissionSafetyError("DIRECT_NEON_OWNER_TLS_DSN_REQUIRED") from None


class _BoundSchemaEngine:
    """Reuse the existing schema gate on this transaction, without a second connection."""

    def __init__(self, connection: Connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


def _rows(conn: Connection, query: str, runtime: str = RUNTIME) -> list:
    return conn.execute(text(query), {"runtime": runtime}).all()


def _role_grants(runtime: str) -> tuple[str, ...]:
    if runtime not in RUNTIME_ROLES:
        raise PermissionSafetyError("RUNTIME_ROLE_NOT_ALLOWLISTED")
    return tuple(statement.replace(" TO " + RUNTIME, " TO " + runtime) for statement in GRANTS)


def _client_tls_state(conn: Connection) -> bool | None:
    """Check the live client-to-Neon connection, not the proxy-to-backend hop.

    Psycopg exposes libpq's PQsslInUse as PGconn.ssl_in_use. Never inspect or
    serialize connection repr/info/DSN, and never infer TLS from URL flags alone.
    Unknown/unsupported driver state fails closed in the preflight gate.
    """
    try:
        state = conn.connection.driver_connection.pgconn.ssl_in_use
        return state if isinstance(state, bool) else None
    except Exception:
        return None


def _identity_diagnostics(conn: Connection) -> dict:
    """Return fixed codes and booleans only; no server-provided names or values."""
    identity = conn.execute(text("/* runtime:identity */ SELECT current_database(), current_user, session_user, "
        "(SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database WHERE datname=current_database()), "
        "(SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid())")).one()
    checks = {
        "database_matches": identity[0] == DATABASE,
        "current_user_matches": identity[1] == OWNER,
        "session_user_matches": identity[2] == OWNER,
        "database_owner_matches": identity[3] == OWNER,
        "client_tls_active": _client_tls_state(conn),
        "backend_tls_active": identity[4] if isinstance(identity[4], bool) else None,
    }
    failures = [code for key, code in (
        ("database_matches", "CONNECTED_DATABASE_MISMATCH"),
        ("current_user_matches", "CONNECTED_CURRENT_USER_MISMATCH"),
        ("session_user_matches", "CONNECTED_SESSION_USER_MISMATCH"),
        ("database_owner_matches", "DATABASE_OWNER_MISMATCH"),
    ) if not checks[key]]
    legacy = list(failures)
    if checks["backend_tls_active"] is not True:
        legacy.append("BACKEND_TLS_NOT_ACTIVE" if checks["backend_tls_active"] is False else "BACKEND_TLS_STATUS_UNAVAILABLE")
    if checks["client_tls_active"] is not True:
        failures.append("CLIENT_TLS_NOT_ACTIVE" if checks["client_tls_active"] is False else "CLIENT_TLS_STATUS_UNAVAILABLE")
    # This admin membership is expected on Neon; it is NOT a runtime-role exemption.
    admin_member = conn.execute(text("/* runtime:admin_membership */ SELECT EXISTS ("
        "SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r ON r.oid=m.roleid "
        "WHERE r.rolname='neon_superuser' AND m.member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))")).scalar_one()
    checks["admin_direct_neon_superuser_member"] = admin_member is True
    return {"code": "CLOUD_RUNTIME_IDENTITY_DIAGNOSTIC", "scope": "identity_only_no_permission_changes",
            "checks": checks, "blocking_codes": failures, "legacy_failure_codes": legacy}


def _preflight(conn: Connection, runtime: str = RUNTIME) -> None:
    report = _identity_diagnostics(conn)
    if report["blocking_codes"]:
        raise PermissionSafetyError(report["blocking_codes"][0])
    _assert_runtime_role(conn, runtime)
    _check_schema(conn)


def _assert_runtime_role(conn: Connection, runtime: str) -> None:
    role = _rows(conn, "/* runtime:role */ SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        "rolreplication, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=:runtime", runtime)
    if len(role) != 1 or tuple(role[0][1:]) != (True, False, False, False, False, False):
        raise PermissionSafetyError("RUNTIME_ROLE_MISSING_OR_PRIVILEGED")
    unsafe = conn.execute(text("/* runtime:authority */ SELECT "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime)) OR "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datdba=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime)) OR "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime)) OR "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime)) OR "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_proc WHERE proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime))"),
        {"runtime": runtime}).scalar_one()
    if unsafe:
        raise PermissionSafetyError("RUNTIME_MEMBERSHIP_OR_OWNERSHIP_REJECTED")


def _check_schema(conn: Connection) -> None:
    try:
        check_business_schema(_BoundSchemaEngine(conn), DATABASE)
    except Exception:
        raise PermissionSafetyError("CLOUD_SCHEMA_NOT_READY") from None


def _runtime_diagnostics(conn: Connection, runtime: str = RUNTIME) -> dict:
    """Role metadata/membership only: fixed codes and booleans, never role names.

    pg_has_role USAGE checks actual inheritance paths, not just rolinherit.
    Special role attributes are NOT inherited; membership and SET ROLE access
    are reported separately. This is not an audit of every object ACL.
    """
    rows = _rows(conn, "/* runtime:diagnostic_role */ SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        "rolreplication, rolbypassrls, rolinherit FROM pg_catalog.pg_roles WHERE rolname=:runtime", runtime)
    names = ("runtime_can_login", "runtime_is_superuser", "runtime_has_createdb", "runtime_has_createrole",
             "runtime_has_replication", "runtime_has_bypassrls", "runtime_inherit_attribute")
    checks = {name: False for name in names}
    checks.update({"runtime_role_exists": bool(rows), "runtime_has_any_role_membership": False,
        "runtime_direct_member_of_neon_superuser": False, "runtime_member_of_neon_superuser": False,
        "runtime_has_privileged_role_membership": False, "runtime_inherits_from_privileged_role": False,
        "runtime_can_set_privileged_role": False})
    if len(rows) > 1 or rows and (len(rows[0]) != len(names) or any(not isinstance(v, bool) for v in rows[0])):
        raise PermissionSafetyError("RUNTIME_ROLE_METADATA_UNAVAILABLE")
    if not rows:
        return {"code": "CLOUD_RUNTIME_ROLE_DIAGNOSTIC", "scope": "role_attributes_and_memberships_only",
                "checks": checks, "blocking_codes": ["RUNTIME_ROLE_MISSING"], "legacy_role_gate_failed": True}
    checks.update(zip(names, rows[0]))
    failures = [code for key, code, rejected in (
        ("runtime_can_login", "RUNTIME_ROLE_LOGIN_DISABLED", False),
        ("runtime_is_superuser", "RUNTIME_ROLE_SUPERUSER", True),
        ("runtime_has_createdb", "RUNTIME_ROLE_CREATEDB", True),
        ("runtime_has_createrole", "RUNTIME_ROLE_CREATEROLE", True),
        ("runtime_has_replication", "RUNTIME_ROLE_REPLICATION", True),
        ("runtime_has_bypassrls", "RUNTIME_ROLE_BYPASSRLS", True),
    ) if checks[key] is rejected]
    legacy_failed = bool(failures)
    version = int(conn.execute(text("/* runtime:version */ SHOW server_version_num")).scalar_one())
    # SET was added to pg_has_role in PostgreSQL 16. Earlier versions permit SET
    # through membership (a non-superuser still cannot SET ROLE to a superuser).
    set_access = "pg_catalog.pg_has_role(t.oid,p.oid,'SET')" if version >= 160000 else "(t.rolsuper OR NOT p.rolsuper)"
    memberships = _rows(conn, "/* runtime:diagnostic_memberships */ WITH RECURSIVE target AS ("
        "SELECT oid,rolsuper FROM pg_catalog.pg_roles WHERE rolname=:runtime), ancestors(roleid) AS ("
        "SELECT m.roleid FROM pg_catalog.pg_auth_members m JOIN target t ON m.member=t.oid UNION "
        "SELECT m.roleid FROM pg_catalog.pg_auth_members m JOIN ancestors a ON m.member=a.roleid) "
        "SELECT p.rolname='neon_superuser', "
        "(p.rolsuper OR p.rolcreatedb OR p.rolcreaterole OR p.rolreplication OR p.rolbypassrls OR p.rolname IN "
        "('neon_superuser','pg_read_all_data','pg_write_all_data','pg_read_server_files','pg_write_server_files',"
        "'pg_execute_server_program','pg_signal_backend','pg_checkpoint','pg_maintain','pg_create_subscription')), "
        "pg_catalog.pg_has_role(t.oid,p.oid,'USAGE'), " + set_access + ", "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members edge WHERE edge.member=t.oid AND edge.roleid=p.oid) "
        "FROM target t JOIN pg_catalog.pg_roles p ON p.oid IN (SELECT roleid FROM ancestors) WHERE p.oid<>t.oid", runtime)
    for row in memberships:
        if len(row) != 5 or any(not isinstance(v, bool) for v in row):
            raise PermissionSafetyError("RUNTIME_MEMBERSHIP_METADATA_UNAVAILABLE")
        neon, privileged, inherited, can_set, direct = row
        checks["runtime_has_any_role_membership"] = True
        checks["runtime_member_of_neon_superuser"] |= neon
        checks["runtime_direct_member_of_neon_superuser"] |= neon and direct
        checks["runtime_has_privileged_role_membership"] |= privileged
        checks["runtime_inherits_from_privileged_role"] |= privileged and inherited
        checks["runtime_can_set_privileged_role"] |= privileged and can_set
    for key, code in (
        ("runtime_member_of_neon_superuser", "RUNTIME_ROLE_NEON_SUPERUSER_MEMBER"),
        ("runtime_inherits_from_privileged_role", "RUNTIME_ROLE_INHERITS_PRIVILEGED_ROLE_PERMISSIONS"),
        ("runtime_can_set_privileged_role", "RUNTIME_ROLE_CAN_SET_PRIVILEGED_ROLE"),
        ("runtime_has_any_role_membership", "RUNTIME_ROLE_HAS_MEMBERSHIPS"),
    ):
        if checks[key]:
            failures.append(code)
    return {"code": "CLOUD_RUNTIME_ROLE_DIAGNOSTIC", "scope": "role_attributes_and_memberships_only",
            "checks": checks, "blocking_codes": failures, "legacy_role_gate_failed": legacy_failed}


def _verify_permissions(conn: Connection, *, complete: bool, runtime: str = RUNTIME) -> None:
    """Check effective rights (including PUBLIC), not merely direct ACL entries.

    Before grants, missing documented rights and PUBLIC's public-schema CREATE
    are permitted. Other excess rights must be fixed separately, never silently
    revoked. After grants all documented rights must exist and CREATE must not.
    Default PUBLIC database TEMP/CONNECT and function EXECUTE are not changed.
    """
    db = conn.execute(text("/* runtime:database_rights */ SELECT "
        "pg_catalog.has_database_privilege(:runtime,current_database(),'CONNECT'), "
        "pg_catalog.has_database_privilege(:runtime,current_database(),'CREATE'), "
        "pg_catalog.has_database_privilege(:runtime,current_database(),'CONNECT WITH GRANT OPTION')"),
        {"runtime": runtime}).one()
    if db[1] or db[2] or (complete and not db[0]):
        raise PermissionSafetyError("RUNTIME_DATABASE_PRIVILEGES_REJECTED")
    schemas = _rows(conn, "/* runtime:schema_rights */ SELECT nspname, "
        "pg_catalog.has_schema_privilege(:runtime,oid,'USAGE'), "
        "pg_catalog.has_schema_privilege(:runtime,oid,'CREATE'), "
        "pg_catalog.has_schema_privilege(:runtime,oid,'USAGE WITH GRANT OPTION'), "
        "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(nspacl,pg_catalog.acldefault('n',nspowner))) a "
        "WHERE a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime) AND a.privilege_type='CREATE') "
        "FROM pg_catalog.pg_namespace WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'", runtime)
    if not any(row[0] == "public" for row in schemas):
        raise PermissionSafetyError("PUBLIC_SCHEMA_REQUIRED")
    for name, usage, create, grantable, direct_create in schemas:
        if grantable or direct_create or (create and (complete or name != "public")) or (complete and name == "public" and not usage):
            raise PermissionSafetyError("RUNTIME_SCHEMA_PRIVILEGES_REJECTED")
    version = int(conn.execute(text("/* runtime:version */ SHOW server_version_num")).scalar_one())
    privileges = "('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER')"
    if version >= 170000:
        privileges += ",('MAINTAIN')"
    tables = _rows(conn, "/* runtime:table_rights */ SELECT n.nspname,c.relname,v.priv, "
        "pg_catalog.has_table_privilege(:runtime,c.oid,v.priv), "
        "pg_catalog.has_table_privilege(:runtime,c.oid,v.priv || ' WITH GRANT OPTION') "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        f"CROSS JOIN (VALUES {privileges}) v(priv) "
        "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','v','m','f')", runtime)
    found = set()
    for schema, table, privilege, allowed, grantable in tables:
        found.add((schema, table))
        expected = schema == "public" and (table in BUSINESS_TABLES and privilege in {"SELECT", "INSERT"}
                                           or table == "alembic_version" and privilege == "SELECT")
        if grantable or (allowed and not expected) or (complete and expected and not allowed):
            raise PermissionSafetyError("RUNTIME_TABLE_PRIVILEGES_REJECTED")
    if not {("public", t) for t in BUSINESS_TABLES | {"alembic_version"}}.issubset(found):
        raise PermissionSafetyError("RUNTIME_TABLES_REQUIRED")
    columns = _rows(conn, "/* runtime:column_rights */ SELECT n.nspname,c.relname,a.attname,v.priv, "
        "pg_catalog.has_column_privilege(:runtime,c.oid,a.attnum,v.priv), "
        "pg_catalog.has_column_privilege(:runtime,c.oid,a.attnum,v.priv || ' WITH GRANT OPTION') "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('REFERENCES')) v(priv) "
        "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
        "AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped", runtime)
    seen_updates = set()
    for schema, table, column, privilege, allowed, grantable in columns:
        expected = schema == "public" and (
            table in BUSINESS_TABLES and privilege in {"SELECT", "INSERT"}
            or table == "alembic_version" and privilege == "SELECT"
            or privilege == "UPDATE" and column in UPDATE_COLUMNS.get(table, ()))
        if expected and privilege == "UPDATE":
            seen_updates.add((table, column))
        if grantable or (allowed and not expected) or (complete and expected and not allowed):
            raise PermissionSafetyError("RUNTIME_COLUMN_PRIVILEGES_REJECTED")
    if seen_updates != {(t, c) for t, columns in UPDATE_COLUMNS.items() for c in columns}:
        raise PermissionSafetyError("RUNTIME_UPDATE_COLUMNS_REQUIRED")
    sequences = _rows(conn, "/* runtime:sequence_rights */ SELECT "
        "pg_catalog.has_sequence_privilege(:runtime,c.oid,'USAGE,SELECT,UPDATE') "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind='S' AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'", runtime)
    if any(row[0] for row in sequences):
        raise PermissionSafetyError("RUNTIME_SEQUENCE_PRIVILEGES_REJECTED")


def _provisioning_diagnostics(conn: Connection) -> dict:
    """Catalog-only checks, never a CREATE/GRANT/rollback experiment.

    Native CREATEROLE and Neon admin membership are separate observations:
    managed-service hooks may authorize operations differently from core PG.
    A passing ACL check cannot test Neon's external PRE_COMMIT callback.
    """
    _check_schema(conn)
    row = conn.execute(text("/* runtime:provisioning_actor */ SELECT "
        "r.rolsuper, r.rolcreaterole, "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_roles n WHERE n.rolname='neon_superuser' "
        "AND pg_catalog.pg_has_role(r.oid,n.oid,'USAGE')), "
        "current_setting('default_transaction_read_only')='on', pg_catalog.pg_is_in_recovery(), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=:runtime) "
        "FROM pg_catalog.pg_roles r WHERE r.rolname=current_user"), {"runtime": APP_RUNTIME}).one()
    names = ("owner_native_superuser", "owner_native_createrole", "owner_has_neon_admin_privileges",
             "default_transactions_read_only", "server_in_recovery", "new_runtime_role_exists")
    if len(row) != len(names) or any(not isinstance(value, bool) for value in row):
        raise PermissionSafetyError("PROVISIONING_ACTOR_METADATA_UNAVAILABLE")
    checks = dict(zip(names, row))
    acl = conn.execute(text("/* runtime:provisioning_acl */ SELECT "
        "pg_catalog.has_database_privilege(current_user,current_database(),'CONNECT WITH GRANT OPTION'), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='public' "
        "AND pg_catalog.pg_has_role(current_user,nspowner,'USAGE')), "
        "pg_catalog.has_schema_privilege(current_user,'public','USAGE WITH GRANT OPTION'), "
        "pg_catalog.has_table_privilege(current_user,'public.alembic_version','SELECT WITH GRANT OPTION'), "
        "(SELECT bool_and(pg_catalog.has_table_privilege(current_user,t,p)) FROM "
        "(VALUES ('public.business_documents'),('public.business_records'),('public.business_review_events')) AS tables(t) "
        "CROSS JOIN (VALUES ('SELECT WITH GRANT OPTION'),('INSERT WITH GRANT OPTION')) AS privileges(p))")).one()
    acl_names = ("owner_can_grant_connect", "owner_can_revoke_public_schema_create",
                 "owner_can_grant_schema_usage", "owner_can_grant_migration_select", "owner_can_grant_business_select_insert")
    if len(acl) != len(acl_names) or any(not isinstance(value, bool) for value in acl):
        raise PermissionSafetyError("PROVISIONING_ACL_METADATA_UNAVAILABLE")
    checks.update(zip(acl_names, acl))
    # Fixed source identifiers only; none are taken from server data or input.
    columns = ",".join(f"('public.{table}','{column}')" for table in sorted(UPDATE_COLUMNS)
                       for column in sorted(UPDATE_COLUMNS[table]))
    update = conn.execute(text("/* runtime:provisioning_update_acl */ SELECT bool_and("
        "pg_catalog.has_column_privilege(current_user,t,c,'UPDATE WITH GRANT OPTION')) "
        f"FROM (VALUES {columns}) AS required(t,c)")).scalar_one()
    if not isinstance(update, bool):
        raise PermissionSafetyError("PROVISIONING_ACL_METADATA_UNAVAILABLE")
    checks["owner_can_grant_required_column_updates"] = update
    checks["creation_uses_prehashed_password"] = False
    checks["neon_supports_prehashed_sql_password"] = False
    failures = []
    if not any(checks[key] for key in ("owner_native_superuser", "owner_native_createrole", "owner_has_neon_admin_privileges")):
        failures.append("OWNER_CREATE_ROLE_AUTHORITY_NOT_OBSERVED")
    for key, code in (
        ("default_transactions_read_only", "DEFAULT_TRANSACTIONS_READ_ONLY"),
        ("server_in_recovery", "SERVER_IN_RECOVERY"),
        ("new_runtime_role_exists", "NEW_RUNTIME_ALREADY_EXISTS_NO_CHANGES"),
    ):
        if checks[key]:
            failures.append(code)
    if not all(checks[key] for key in (*acl_names, "owner_can_grant_required_column_updates")):
        failures.append("OWNER_REQUIRED_GRANT_AUTHORITY_NOT_OBSERVED")
    return {"code": "CLOUD_RUNTIME_PROVISIONING_DIAGNOSTIC",
            "scope": "read_only_catalog_checks_not_create_or_commit_reproduction",
            "checks": checks, "blocking_codes": failures}


def _validate_password(password: str | None) -> None:
    if not isinstance(password, str) or not 32 <= len(password) <= 256 or any(not 33 <= ord(c) <= 126 for c in password):
        raise PermissionSafetyError("RUNTIME_PASSWORD_REQUIRES_32_TO_256_PRINTABLE_NONSPACE_ASCII_CHARACTERS")


def _read_password(*, confirm: bool) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Saved password-manager password for viral_lab_app_runtime (hidden): ")
            _validate_password(password)
            if confirm:
                repeated = getpass.getpass("Confirm new runtime password (hidden): ")
                _validate_password(repeated)
                if not hmac.compare_digest(password, repeated):
                    raise PermissionSafetyError("RUNTIME_PASSWORD_CONFIRMATION_MISMATCH")
            return password
    except PermissionSafetyError:
        raise
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise PermissionSafetyError("SECURE_INTERACTIVE_PASSWORD_INPUT_REQUIRED") from None
    except Exception:
        raise PermissionSafetyError("SECURE_INTERACTIVE_PASSWORD_INPUT_REQUIRED") from None


def _dispose(engine) -> None:
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            raise PermissionSafetyError("CLOUD_RUNTIME_ENGINE_DISPOSE_FAILED") from None


def _expect_denied(conn: Connection, statement: str, params: dict) -> None:
    savepoint = conn.begin_nested()
    denied = False
    try:
        conn.execute(text(statement), params)
    except Exception as error:
        if getattr(getattr(error, "orig", error), "sqlstate", None) != "42501":
            raise PermissionSafetyError("RUNTIME_PROBE_EXPECTED_PERMISSION_DENIAL") from None
        denied = True
    finally:
        savepoint.rollback()
    if not denied:
        raise PermissionSafetyError("RUNTIME_FORBIDDEN_OPERATION_SUCCEEDED")


def _permission_probe(conn: Connection) -> None:
    """Synthetic permission-only rows. Caller MUST roll back the entire transaction."""
    token = uuid4().hex
    source = b"%PDF-1.7 synthetic permission probe only; never an extraction"
    snapshot = '{"permission_probe":true}'
    failure = '{"code":"SYNTHETIC_PERMISSION_PROBE"}'
    values = {"id": "permission_probe_" + token, "source": source, "source_hash": sha256(source).hexdigest(),
              "snapshot": snapshot, "snapshot_hash": sha256(snapshot.encode()).hexdigest(),
              "failure": failure, "failure_hash": sha256(failure.encode()).hexdigest()}
    statements = (
        "INSERT INTO public.business_documents (document_id,review_token,source_bytes,source_sha256,mime_type,status,"
        "prompt_version,pipeline_version,created_at) VALUES (:id,:id,:source,:source_hash,'application/pdf','processing',"
        "'permission-probe-only','permission-probe-only',CURRENT_TIMESTAMP)",
        "INSERT INTO public.business_records (document_id,form_id,ordinal,revision,review_status,projection_json,projection_sha256) "
        "VALUES (:id,'probe',0,0,'unresolved',:snapshot,:snapshot_hash)",
        "UPDATE public.business_documents SET status='failed',failure_json=:failure,failure_sha256=:failure_hash WHERE document_id=:id",
        "UPDATE public.business_records SET revision=1,review_status='unresolved',projection_json=:snapshot,projection_sha256=:snapshot_hash WHERE document_id=:id",
        "INSERT INTO public.business_review_events (document_id,form_id,decision_id,field_name,previous_revision,revision,event_json,event_sha256,received_at) "
        "VALUES (:id,'probe','probe','first_name',0,1,:snapshot,:snapshot_hash,CURRENT_TIMESTAMP)",
    )
    for statement in statements:
        if conn.execute(text(statement), values).rowcount != 1:
            raise PermissionSafetyError("RUNTIME_REQUIRED_WRITE_PROBE_FAILED")
    for table in sorted(BUSINESS_TABLES):
        if conn.execute(text(f"SELECT count(*) FROM public.{table} WHERE document_id=:id"), values).scalar_one() != 1:
            raise PermissionSafetyError("RUNTIME_REQUIRED_READ_PROBE_FAILED")
    for statement in (
        f"CREATE SCHEMA permission_probe_{token}",
        f"CREATE TABLE public.permission_probe_{token} (probe integer)",
        *(f"DELETE FROM public.{table} WHERE document_id=:id" for table in sorted(BUSINESS_TABLES)),
        "UPDATE public.business_documents SET source_bytes=source_bytes WHERE document_id=:id",
        "UPDATE public.business_review_events SET event_json=event_json WHERE document_id=:id",
    ):
        _expect_denied(conn, statement, values)


class _ProbeRollback(Exception):
    """Internal success signal that deliberately rolls back all probe rows."""


def _verify_new_runtime(dsn: str, password: str) -> None:
    url = neon_url(dsn, DATABASE).set(username=APP_RUNTIME, password=password)
    engine = None
    stage = "VERIFY_ENGINE"
    try:
        # This is a NEW password-authenticated connection, never SET ROLE as admin.
        engine = create_cloud_engine(url.render_as_string(hide_password=False), DATABASE)
        stage = "VERIFY_LOGIN"
        try:
            with engine.begin() as conn:
                stage = "VERIFY_IDENTITY_TLS"
                conn.execute(text("SET LOCAL search_path = public"))
                identity = conn.execute(text("/* runtime:login_identity */ SELECT current_database(),current_user,session_user")).one()
                if tuple(identity) != (DATABASE, APP_RUNTIME, APP_RUNTIME) or _client_tls_state(conn) is not True:
                    raise PermissionSafetyError("NEW_RUNTIME_LOGIN_IDENTITY_OR_TLS_REJECTED")
                stage = "VERIFY_ROLE"
                _assert_runtime_role(conn, APP_RUNTIME)
                stage = "VERIFY_SCHEMA"
                _check_schema(conn)
                stage = "VERIFY_EFFECTIVE_PERMISSIONS"
                _verify_permissions(conn, complete=True, runtime=APP_RUNTIME)
                stage = "VERIFY_PROBE"
                _permission_probe(conn)
                stage = "VERIFY_PROBE_ROLLBACK"
                raise _ProbeRollback
        except _ProbeRollback:
            pass
    except (Exception, KeyboardInterrupt) as error:
        raise _stage_failure(stage, error) from None
    finally:
        _dispose(engine)


def _protect_password_statement(conn: Connection) -> None:
    """Fail closed if transaction-local SQL log suppression is not permitted.

    DDL cannot use PostgreSQL server-side parameters. ClientCursor safely binds
    it locally, so the resulting SQL must also stay out of server query logs.
    These settings expire on commit/rollback; no role/global configuration or
    runtime privileges change. Provider-side tracing remains outside our control.
    """
    for statement in (
        "SET LOCAL log_statement = 'none'",
        "SET LOCAL log_min_error_statement = 'panic'",
        "SET LOCAL log_min_messages = 'panic'",
        "SET LOCAL log_min_duration_statement = -1",
        "SET LOCAL log_min_duration_sample = -1",
        "SET LOCAL log_transaction_sample_rate = 0",
    ):
        try:
            conn.execute(text(statement))
        except (Exception, KeyboardInterrupt) as error:
            # Only fixed setting identifiers from this tuple enter the code.
            setting = statement.split()[2].upper()
            raise _stage_failure("PRECOMMIT_PASSWORD_LOGGING_GUARD_" + setting, error) from None
    # Extension settings exist only when loaded. Never install extensions or
    # change their global configuration. Disable optional SQL capture locally.
    for setting, value in (("pg_stat_statements.track", "none"), ("pgaudit.log", "none")):
        present = conn.execute(text("/* runtime:password_capture_setting */ SELECT "
            "pg_catalog.current_setting(:setting,true) IS NOT NULL"), {"setting": setting}).scalar_one()
        if present is not True and present is not False:
            raise PermissionSafetyError("PASSWORD_LOGGING_STATE_UNAVAILABLE")
        if present:
            try:
                conn.execute(text(f"SET LOCAL {setting} = '{value}'"))
            except (Exception, KeyboardInterrupt) as error:
                label = setting.upper().replace(".", "_")
                raise _stage_failure("PRECOMMIT_PASSWORD_LOGGING_GUARD_" + label, error) from None


def _provision_new_runtime(dsn: str, password: str, *, acknowledge_server_password_logging_risk: bool = False) -> None:
    engine = None
    stage = "PRECOMMIT_ENGINE"
    try:
        engine = create_cloud_engine(dsn, DATABASE)
        stage = "PRECOMMIT_CONNECT"
        try:
            with engine.begin() as conn:
                stage = "PRECOMMIT_IDENTITY_TLS"
                conn.execute(text("SET LOCAL search_path = public"))
                identity = _identity_diagnostics(conn)
                if identity["blocking_codes"]:
                    raise PermissionSafetyError(identity["blocking_codes"][0])
                stage = "PRECOMMIT_SCHEMA"
                _check_schema(conn)
                stage = "PRECOMMIT_ROLE_ABSENCE"
                if _rows(conn, "/* runtime:new_role_exists */ SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime", APP_RUNTIME):
                    raise PermissionSafetyError("NEW_RUNTIME_ALREADY_EXISTS_NO_CHANGES")
                stage = "PRECOMMIT_PASSWORD_LOGGING_GUARD"
                if not acknowledge_server_password_logging_risk:
                    _protect_password_statement(conn)
                # Neon-supported SQL role creation does not require changing
                # privileged logging GUCs. Choosing it explicitly accepts that
                # provider-side logs may retain password-bearing SQL. Never
                # fall back to it after a failed strict guard in this transaction.
                stage = "PRECOMMIT_PASSWORD_POLICY"
                conn.execute(text("SET LOCAL password_encryption = 'scram-sha-256'"))
                driver = conn.connection.driver_connection
                # PostgreSQL DDL does not accept server-side placeholders.
                # ClientCursor adapts %s safely on the client; Neon requires the
                # original password, not a precomputed verifier. Never mogrify,
                # stringify, log or persist the merged SQL. Bypass SQLAlchemy.
                stage = "PRECOMMIT_CREATE_ROLE"
                with ClientCursor(driver) as cursor:
                    cursor.execute(pgsql.SQL("CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD %s").format(
                            pgsql.Identifier(APP_RUNTIME)), (password,))
                stage = "PRECOMMIT_CREATED_ROLE_CHECK"
                oid = _rows(conn, "/* runtime:new_role_exists */ SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime", APP_RUNTIME)
                if len(oid) != 1:
                    raise PermissionSafetyError("NEW_RUNTIME_CREATION_NOT_CONFIRMED")
                created_oid = oid[0][0]
                _preflight(conn, APP_RUNTIME)
                for number, statement in enumerate(_role_grants(APP_RUNTIME), start=1):
                    stage = f"PRECOMMIT_GRANT_{number:02d}"
                    conn.execute(text(statement))
                stage = "PRECOMMIT_EFFECTIVE_PERMISSIONS"
                _preflight(conn, APP_RUNTIME)
                _verify_permissions(conn, complete=True, runtime=APP_RUNTIME)
                # engine.begin() commits on context exit. Neon can reject its
                # role control-plane synchronization in XACT_EVENT_PRE_COMMIT.
                # Never treat this as an ordinary CREATE failure or auto-retry.
                stage = "COMMIT_OUTCOME_UNCERTAIN"
        except PermissionSafetyError:
            raise
        except (Exception, KeyboardInterrupt) as error:
            raise _stage_failure(stage, error) from None
        try:
            _verify_new_runtime(dsn, password)
        except (Exception, KeyboardInterrupt) as error:
            cause = str(error) if isinstance(error, _StageFailure) else str(_stage_failure("VERIFY_UNEXPECTED", error))
            # Only the exact newly created role may be disabled. Never drop it,
            # revoke memberships, or touch the pre-existing demo runtime role.
            try:
                with engine.begin() as conn:
                    identity = _identity_diagnostics(conn)
                    if identity["blocking_codes"]:
                        raise ValueError
                    current = _rows(conn, "/* runtime:new_role_exists */ SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:runtime", APP_RUNTIME)
                    if len(current) != 1 or tuple(current[0]) != (created_oid,):
                        raise ValueError
                    conn.execute(text(f"ALTER ROLE {APP_RUNTIME} NOLOGIN"))
            except (Exception, KeyboardInterrupt):
                raise PermissionSafetyError("NEW_RUNTIME_VERIFY_FAILED_DISABLE_UNCONFIRMED: " + cause) from None
            raise PermissionSafetyError("NEW_RUNTIME_VERIFY_FAILED_ROLE_DISABLED: " + cause) from None
    except PermissionSafetyError:
        raise
    except (Exception, KeyboardInterrupt) as error:
        raise _stage_failure(stage, error) from None
    finally:
        _dispose(engine)


def run(dsn: str, confirmation: str | None, *, apply: bool = False, check: bool = False,
        diagnose: bool = False, diagnose_runtime: bool = False, runtime: str = RUNTIME,
        create_runtime: bool = False, verify_runtime: bool = False, password: str | None = None,
        diagnose_provisioning: bool = False, acknowledge_server_password_logging_risk: bool = False) -> str:
    validate_target(dsn, confirmation)
    if runtime not in RUNTIME_ROLES:
        raise PermissionSafetyError("RUNTIME_ROLE_NOT_ALLOWLISTED")
    if sum((apply, check, diagnose, diagnose_runtime, create_runtime, verify_runtime, diagnose_provisioning)) > 1:
        raise PermissionSafetyError("EXCLUSIVE_MODE_REQUIRED")
    if acknowledge_server_password_logging_risk and not create_runtime:
        raise PermissionSafetyError("SERVER_PASSWORD_LOGGING_ACK_REQUIRES_CREATE_RUNTIME")
    if diagnose_provisioning and runtime != APP_RUNTIME:
        raise PermissionSafetyError("EXPLICIT_NEW_RUNTIME_ROLE_REQUIRED")
    if create_runtime or verify_runtime:
        if runtime != APP_RUNTIME:
            raise PermissionSafetyError("EXPLICIT_NEW_RUNTIME_ROLE_REQUIRED")
        _validate_password(password)
        if create_runtime:
            _provision_new_runtime(dsn, password, acknowledge_server_password_logging_risk=acknowledge_server_password_logging_risk)
        else:
            _verify_new_runtime(dsn, password)
        return "CLOUD_NEW_RUNTIME_READY: authenticated probes passed; all probe rows rolled back"
    engine = None
    try:
        engine = create_cloud_engine(dsn, DATABASE)
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL search_path = public"))
            if not apply:
                conn.execute(text("SET TRANSACTION READ ONLY"))
            if diagnose:
                return json.dumps(_identity_diagnostics(conn), sort_keys=True)
            if diagnose_runtime or diagnose_provisioning:
                identity = _identity_diagnostics(conn)
                if identity["blocking_codes"]:
                    raise PermissionSafetyError(identity["blocking_codes"][0])
                if diagnose_provisioning:
                    return json.dumps(_provisioning_diagnostics(conn), sort_keys=True)
                return json.dumps(_runtime_diagnostics(conn, runtime), sort_keys=True)
            _preflight(conn, runtime)
            _verify_permissions(conn, complete=check, runtime=runtime)
            if apply:
                for sql in _role_grants(runtime):
                    conn.execute(text(sql))
                _preflight(conn, runtime)
                _verify_permissions(conn, complete=True, runtime=runtime)
        return "CLOUD_RUNTIME_PERMISSIONS_READY" if apply or check else "CLOUD_RUNTIME_PREFLIGHT_OK: no permissions changed"
    except PermissionSafetyError:
        raise
    except Exception:
        raise PermissionSafetyError("CLOUD_RUNTIME_PERMISSION_OPERATION_FAILED") from None
    finally:
        _dispose(engine)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-synthetic-database", help=f"Must be exactly {DATABASE}")
    parser.add_argument("--runtime-role", choices=RUNTIME_ROLES, default=RUNTIME)
    parser.add_argument("--acknowledge-server-password-logging-risk", action="store_true",
                        help="CREATE ONLY: explicitly accept possible server-side password SQL logging; do not alter server logging settings")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply the documented grants in one transaction")
    mode.add_argument("--check", action="store_true", help="Read-only verification of all effective runtime permissions")
    mode.add_argument("--diagnose", action="store_true", help="Read-only identity/TLS booleans and fixed codes only; not permission readiness")
    mode.add_argument("--diagnose-runtime", action="store_true", help="Read-only runtime role attributes/inherited membership diagnostics; never grants")
    mode.add_argument("--diagnose-provisioning", action="store_true", help="Read-only owner grant capabilities; no writes or password prompt")
    mode.add_argument("--create-runtime", action="store_true", help="Create only viral_lab_app_runtime using a hidden password, then grant and verify via an independent login")
    mode.add_argument("--verify-runtime", action="store_true", help="Authenticate as viral_lab_app_runtime; synthetic write probes ALWAYS roll back")
    args = parser.parse_args(argv)
    try:
        password = None
        if args.acknowledge_server_password_logging_risk and not args.create_runtime:
            raise PermissionSafetyError("SERVER_PASSWORD_LOGGING_ACK_REQUIRES_CREATE_RUNTIME")
        if args.create_runtime or args.verify_runtime:
            validate_target(os.environ.get("NEON_OWNER_DSN", ""), args.confirm_synthetic_database)
            if args.runtime_role != APP_RUNTIME:
                raise PermissionSafetyError("EXPLICIT_NEW_RUNTIME_ROLE_REQUIRED")
            if args.acknowledge_server_password_logging_risk:
                print("SERVER_PASSWORD_LOGGING_RISK_ACKNOWLEDGED: server/provider logs may retain the password-bearing SQL; logging settings will not be changed")
            password = _read_password(confirm=args.create_runtime)
        result = run(os.environ.get("NEON_OWNER_DSN", ""), args.confirm_synthetic_database,
                     apply=args.apply, check=args.check, diagnose=args.diagnose, diagnose_runtime=args.diagnose_runtime,
                     runtime=args.runtime_role, create_runtime=args.create_runtime, verify_runtime=args.verify_runtime, password=password,
                     diagnose_provisioning=args.diagnose_provisioning,
                     acknowledge_server_password_logging_risk=args.acknowledge_server_password_logging_risk)
    except PermissionSafetyError as error:
        print(str(error))
        return 2
    finally:
        password = None
    print(result)
    return 2 if (args.diagnose or args.diagnose_runtime or args.diagnose_provisioning) and json.loads(result)["blocking_codes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
