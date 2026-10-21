"""Deterministic permission gates/transactions: no sockets, database, or credentials."""
from contextlib import contextmanager
import json
import socket
from types import SimpleNamespace

import pytest

from backend.tools import cloud_runtime_permissions as tool

DSN = ("postgresql://neondb_owner:DO_NOT_PRINT@ep-demo.eu-central-1.aws.neon.tech/"
       "viral_lab_submission_synthetic?sslmode=require&channel_binding=require")


@pytest.fixture(autouse=True)
def no_external_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Permission utility tests must never connect to a database or network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(tool, "create_cloud_engine", forbidden)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def scalar_one(self):
        return self.one()[0]


class Connection:
    def __init__(self):
        self.statements = []
        self.applied = False
        self.public_create = True
        self.identity = (tool.DATABASE, tool.OWNER, tool.OWNER, tool.OWNER, True)
        self.connection = SimpleNamespace(driver_connection=SimpleNamespace(pgconn=SimpleNamespace(ssl_in_use=True)))
        self.admin_member = True
        self.role = [(42, True, False, False, False, False, False)]
        self.unsafe_authority = False
        self.diagnostic_memberships = []
        self.overrides = {}
        self.grant_failure = None
        self.post_failure = False
        self.server_version = "170005"

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if sql in tool.GRANTS:
            if sql == self.grant_failure:
                raise RuntimeError("DO_NOT_PRINT " + DSN)
            if sql.startswith("REVOKE"):
                self.public_create = False
            if sql == tool.GRANTS[-1]:
                self.applied = True
            return Result([])
        if sql.startswith("SET"):
            return Result([])
        kind = sql.split("runtime:", 1)[1].split(" ", 1)[0]
        if kind in self.overrides:
            return Result(self.overrides[kind])
        if kind == "identity":
            rows = [self.identity]
        elif kind == "admin_membership":
            rows = [(self.admin_member,)]
        elif kind == "role":
            rows = self.role
        elif kind == "diagnostic_role":
            rows = [tuple(row[1:]) + (True,) for row in self.role]
        elif kind == "diagnostic_memberships":
            rows = self.diagnostic_memberships
        elif kind == "authority":
            rows = [(self.unsafe_authority,)]
        elif kind == "database_rights":
            rows = [(self.applied, False, False)]
        elif kind == "schema_rights":
            rows = [("public", self.applied, self.public_create, False, False)]
        elif kind == "version":
            rows = [(self.server_version,)]
        elif kind == "table_rights":
            rows = []
            for table in [*sorted(tool.BUSINESS_TABLES), "alembic_version", "review_sessions"]:
                for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"):
                    expected = priv == "SELECT" and table != "review_sessions" or priv == "INSERT" and table in tool.BUSINESS_TABLES
                    allowed = self.applied and expected
                    if self.post_failure and self.applied and table == "business_documents" and priv == "INSERT":
                        allowed = False
                    rows.append(("public", table, priv, allowed, False))
        elif kind == "column_rights":
            rows = []
            for table in [*sorted(tool.BUSINESS_TABLES), "alembic_version", "review_sessions"]:
                for column in sorted(set(tool.UPDATE_COLUMNS.get(table, ())) | {"immutable_column"}):
                    for priv in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
                        expected = (priv == "SELECT" and table != "review_sessions"
                                    or priv == "INSERT" and table in tool.BUSINESS_TABLES
                                    or priv == "UPDATE" and column in tool.UPDATE_COLUMNS.get(table, ()))
                        rows.append(("public", table, column, priv, self.applied and expected, False))
        elif kind == "sequence_rights":
            rows = []
        else:
            raise AssertionError("Unexpected SQL category: " + kind)
        return Result(rows)


class Engine:
    def __init__(self):
        self.conn = Connection()
        self.commits = 0
        self.rollbacks = 0
        self.disposed = False

    @contextmanager
    def begin(self):
        before = (self.conn.applied, self.conn.public_create)
        try:
            yield self.conn
        except Exception:
            self.conn.applied, self.conn.public_create = before
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def dispose(self):
        self.disposed = True


@pytest.fixture
def engine(monkeypatch):
    engine = Engine()
    def create(dsn, database):
        assert dsn == DSN and database == tool.DATABASE
        return engine
    def schema_gate(bound, database):
        assert database == tool.DATABASE
        with bound.connect() as conn:
            assert conn is engine.conn
            if "SCHEMA_GATE_CHECKED" not in conn.statements:
                assert not any(sql in tool.GRANTS for sql in conn.statements)
            conn.statements.append("SCHEMA_GATE_CHECKED")
    monkeypatch.setattr(tool, "create_cloud_engine", create)
    monkeypatch.setattr(tool, "check_business_schema", schema_gate)
    return engine


@pytest.mark.parametrize("confirmation", [None, "", "viral_lab", "other_synthetic", "viral_lab_submission_synthetic "])
def test_exact_confirmation_required_before_engine(confirmation):
    with pytest.raises(tool.PermissionSafetyError, match="EXACT_SYNTHETIC_DATABASE_CONFIRMATION_REQUIRED"):
        tool.run(DSN, confirmation, apply=True)


@pytest.mark.parametrize("change", [
    lambda s: "", lambda s: s.replace("ep-demo.eu-central-1.aws.neon.tech", "127.0.0.1"),
    lambda s: s.replace("ep-demo.eu-central-1.aws.neon.tech", "ep-demo-pooler.aws.neon.tech"),
    lambda s: s.replace("ep-demo.eu-central-1.aws.neon.tech", "ep-demo.neon.tech.attacker.test"),
    lambda s: s.replace("ep-demo.eu-central-1.aws.neon.tech", ".neon.tech"),
    lambda s: s.replace("viral_lab_submission_synthetic", "viral_lab"),
    lambda s: s.replace("neondb_owner:", "viral_lab_demo_runtime:"),
    lambda s: s.replace("require&channel_binding", "disable&channel_binding"),
    lambda s: s.replace("channel_binding=require", "channel_binding=disable"),
    lambda s: s + "&host=127.0.0.1", lambda s: s + "&dbname=viral_lab",
    lambda s: s.replace("DO_NOT_PRINT", ""), lambda s: s.replace(".tech/", ".tech:5433/"),
])
def test_invalid_target_never_connects_or_echoes_dsn(change):
    with pytest.raises(tool.PermissionSafetyError, match="DIRECT_NEON_OWNER_TLS_DSN_REQUIRED") as caught:
        tool.run(change(DSN), tool.DATABASE, apply=True)
    assert "DO_NOT_PRINT" not in str(caught.value)


def test_valid_owner_tls_schemes_without_network():
    for scheme in ("postgresql", "postgres", "postgresql+psycopg"):
        for ssl in ("require", "verify-full"):
            tool.validate_target(DSN.replace("postgresql:", scheme + ":").replace("sslmode=require", "sslmode=" + ssl), tool.DATABASE)


def test_preflight_is_read_only_and_never_grants(engine):
    assert tool.run(DSN, tool.DATABASE).startswith("CLOUD_RUNTIME_PREFLIGHT_OK")
    assert "SET TRANSACTION READ ONLY" in engine.conn.statements
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)
    assert engine.disposed


def test_apply_exact_grants_only_after_gates_and_verify_then_commit(engine):
    assert tool.run(DSN, tool.DATABASE, apply=True) == "CLOUD_RUNTIME_PERMISSIONS_READY"
    assert [s for s in engine.conn.statements if s.startswith(("GRANT", "REVOKE"))] == list(tool.GRANTS)
    assert engine.conn.statements.index("SCHEMA_GATE_CHECKED") < engine.conn.statements.index(tool.GRANTS[0])
    assert "runtime:sequence_rights" in engine.conn.statements[-1]
    assert engine.commits == 1 and engine.rollbacks == 0 and engine.disposed


def test_check_verifies_already_granted_rights_without_changes(engine):
    engine.conn.applied = True
    engine.conn.public_create = False
    assert tool.run(DSN, tool.DATABASE, check=True) == "CLOUD_RUNTIME_PERMISSIONS_READY"
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


def test_check_rejects_missing_permissions_without_grant(engine):
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_DATABASE_PRIVILEGES_REJECTED"):
        tool.run(DSN, tool.DATABASE, check=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


@pytest.mark.parametrize("index,value,code", [
    (0, "viral_lab", "CONNECTED_DATABASE_MISMATCH"),
    (1, tool.RUNTIME, "CONNECTED_CURRENT_USER_MISMATCH"),
    (2, tool.RUNTIME, "CONNECTED_SESSION_USER_MISMATCH"),
    (3, tool.RUNTIME, "DATABASE_OWNER_MISMATCH"),
])
def test_wrong_connected_identity_stops_before_grants_with_specific_code(engine, index, value, code):
    identity = list(engine.conn.identity)
    identity[index] = value
    engine.conn.identity = tuple(identity)
    with pytest.raises(tool.PermissionSafetyError, match=code):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


@pytest.mark.parametrize("flag", range(1, 7))
def test_disabled_login_or_privileged_role_stops_before_grants(engine, flag):
    row = list(engine.conn.role[0])
    row[flag] = not row[flag]
    engine.conn.role = [tuple(row)]
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_ROLE_MISSING_OR_PRIVILEGED"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


def test_missing_runtime_role_stops_before_grants(engine):
    engine.conn.role = []
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_ROLE_MISSING_OR_PRIVILEGED"):
        tool.run(DSN, tool.DATABASE, apply=True)


def test_neon_superuser_membership_or_ownership_requires_separate_remediation(engine):
    engine.conn.unsafe_authority = True
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_MEMBERSHIP_OR_OWNERSHIP_REJECTED"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


def test_revision_or_constraint_failure_stops_before_any_acl_change(engine, monkeypatch):
    def fail(*args):
        raise RuntimeError("DO_NOT_PRINT " + DSN)
    monkeypatch.setattr(tool, "check_business_schema", fail)
    with pytest.raises(tool.PermissionSafetyError, match="CLOUD_SCHEMA_NOT_READY"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


@pytest.mark.parametrize("category,rows,code", [
    ("database_rights", [(False, True, False)], "DATABASE"),
    ("database_rights", [(True, False, True)], "DATABASE"),
    ("schema_rights", [("public", True, True, False, True)], "SCHEMA"),
    ("schema_rights", [("public", True, False, True, False)], "SCHEMA"),
    ("schema_rights", [("public", True, False, False, False), ("extra", True, True, False, False)], "SCHEMA"),
    ("table_rights", [("public", "business_documents", "DELETE", True, False)], "TABLE"),
    ("table_rights", [("public", "business_documents", "UPDATE", True, False)], "TABLE"),
    ("table_rights", [("public", "business_records", "SELECT", True, True)], "TABLE"),
    ("table_rights", [("public", "review_sessions", "SELECT", True, False)], "TABLE"),
    ("table_rights", [("public", "business_documents", "MAINTAIN", True, False)], "TABLE"),
    ("column_rights", [("public", "business_documents", "source_bytes", "UPDATE", True, False)], "COLUMN"),
    ("column_rights", [("public", "business_documents", "status", "UPDATE", True, True)], "COLUMN"),
    ("sequence_rights", [(True,)], "SEQUENCE"),
])
def test_effective_excess_rights_including_public_and_grant_options_rejected_before_changes(engine, category, rows, code):
    engine.conn.overrides[category] = rows
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_" + code + "_PRIVILEGES_REJECTED"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


@pytest.mark.parametrize("failure", ["grant", "verification"])
def test_grant_or_post_verification_failure_rolls_back_entire_transaction(engine, failure):
    if failure == "grant":
        engine.conn.grant_failure = tool.GRANTS[4]
    else:
        engine.conn.post_failure = True
    with pytest.raises(tool.PermissionSafetyError) as caught:
        tool.run(DSN, tool.DATABASE, apply=True)
    assert "DO_NOT_PRINT" not in str(caught.value)
    assert not engine.conn.applied and engine.conn.public_create
    assert engine.commits == 0 and engine.rollbacks == 1 and engine.disposed


def test_console_output_redacts_connection_failure(engine, monkeypatch, capsys):
    def failed_connection(*args):
        raise RuntimeError("DO_NOT_PRINT " + DSN)
    monkeypatch.setattr(tool, "create_cloud_engine", failed_connection)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--apply"]) == 2
    assert capsys.readouterr().out == "CLOUD_RUNTIME_PERMISSION_OPERATION_FAILED\n"


def test_cli_does_not_use_application_dsn_or_env_file(monkeypatch, capsys):
    monkeypatch.delenv("NEON_OWNER_DSN", raising=False)
    monkeypatch.setenv("REVIEW_DATABASE_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE]) == 2
    assert capsys.readouterr().out == "DIRECT_NEON_OWNER_TLS_DSN_REQUIRED\n"


def test_exact_runbook_sql_privileges_are_frozen():
    assert tool.GRANTS == (
        "GRANT CONNECT ON DATABASE viral_lab_submission_synthetic TO viral_lab_demo_runtime",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        "GRANT USAGE ON SCHEMA public TO viral_lab_demo_runtime",
        "GRANT SELECT ON TABLE public.alembic_version TO viral_lab_demo_runtime",
        "GRANT SELECT, INSERT ON TABLE public.business_documents, public.business_records, public.business_review_events TO viral_lab_demo_runtime",
        "GRANT UPDATE (status, extraction_id, canonical_snapshot_json, canonical_snapshot_sha256, failure_json, failure_sha256) ON TABLE public.business_documents TO viral_lab_demo_runtime",
        "GRANT UPDATE (revision, review_status, projection_json, projection_sha256) ON TABLE public.business_records TO viral_lab_demo_runtime",
    )


def test_public_schema_create_is_not_accepted_after_grants(engine):
    engine.conn.applied = True
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_SCHEMA_PRIVILEGES_REJECTED"):
        tool.run(DSN, tool.DATABASE, check=True)


def test_pre_pg17_does_not_query_unsupported_maintain_privilege(engine):
    engine.conn.server_version = "160006"
    tool.run(DSN, tool.DATABASE)
    sql = next(s for s in engine.conn.statements if "runtime:table_rights" in s)
    assert "MAINTAIN" not in sql


def test_modes_cannot_be_combined_before_connecting():
    with pytest.raises(tool.PermissionSafetyError, match="EXCLUSIVE_MODE_REQUIRED"):
        tool.run(DSN, tool.DATABASE, apply=True, check=True)


def test_dispose_errors_do_not_leak_credentials(engine, monkeypatch, capsys):
    def fail():
        raise RuntimeError("DO_NOT_PRINT " + DSN)
    monkeypatch.setattr(engine, "dispose", fail)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE]) == 2
    assert capsys.readouterr().out == "CLOUD_RUNTIME_ENGINE_DISPOSE_FAILED\n"


def test_role_authority_is_checked_again_after_grants(engine, monkeypatch):
    execute = engine.conn.execute
    def changed(statement, params=None):
        if engine.conn.applied:
            engine.conn.unsafe_authority = True
        return execute(statement, params)
    monkeypatch.setattr(engine.conn, "execute", changed)
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_MEMBERSHIP_OR_OWNERSHIP_REJECTED"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert engine.rollbacks == 1 and engine.commits == 0


@pytest.mark.parametrize("backend_tls", [False, None])
def test_live_client_tls_is_valid_even_when_backend_hop_does_not_report_tls(engine, backend_tls):
    engine.conn.identity = (*engine.conn.identity[:4], backend_tls)
    report = json.loads(tool.run(DSN, tool.DATABASE, diagnose=True))
    assert report["checks"]["client_tls_active"] is True
    assert report["checks"]["backend_tls_active"] is backend_tls
    assert report["blocking_codes"] == []
    assert report["legacy_failure_codes"] == ["BACKEND_TLS_NOT_ACTIVE" if backend_tls is False else "BACKEND_TLS_STATUS_UNAVAILABLE"]
    assert tool.run(DSN, tool.DATABASE).startswith("CLOUD_RUNTIME_PREFLIGHT_OK")
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


@pytest.mark.parametrize("client_tls,code", [(False, "CLIENT_TLS_NOT_ACTIVE"), (None, "CLIENT_TLS_STATUS_UNAVAILABLE"),
                                         (1, "CLIENT_TLS_STATUS_UNAVAILABLE"), ("true", "CLIENT_TLS_STATUS_UNAVAILABLE")])
def test_backend_tls_true_cannot_replace_live_client_tls_validation(engine, client_tls, code):
    engine.conn.connection.driver_connection.pgconn.ssl_in_use = client_tls
    with pytest.raises(tool.PermissionSafetyError, match=code):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


def test_unsupported_driver_fails_closed_with_safe_tls_code(engine):
    engine.conn.connection.driver_connection = object()
    with pytest.raises(tool.PermissionSafetyError, match="CLIENT_TLS_STATUS_UNAVAILABLE"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


def test_expected_neon_admin_membership_is_permitted_but_runtime_membership_is_rejected(engine):
    assert engine.conn.admin_member
    report = json.loads(tool.run(DSN, tool.DATABASE, diagnose=True))
    assert report["checks"]["admin_direct_neon_superuser_member"] is True
    assert report["blocking_codes"] == []
    engine.conn.unsafe_authority = True
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_MEMBERSHIP_OR_OWNERSHIP_REJECTED"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


def test_admin_membership_does_not_bypass_database_ownership_requirement(engine):
    engine.conn.identity = (*engine.conn.identity[:3], "another_owner", True)
    assert engine.conn.admin_member
    with pytest.raises(tool.PermissionSafetyError, match="DATABASE_OWNER_MISMATCH"):
        tool.run(DSN, tool.DATABASE, apply=True)
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


def test_diagnostic_json_contains_only_fixed_fields_booleans_and_codes_not_actual_values(engine, monkeypatch, capsys):
    engine.conn.identity = (DSN, "SECRET_CURRENT_USER", "SECRET_SESSION_USER", "SECRET_OWNER", False)
    engine.conn.connection.driver_connection.pgconn.ssl_in_use = False
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--diagnose"]) == 2
    output = capsys.readouterr().out
    assert "SECRET" not in output and "DO_NOT_PRINT" not in output and DSN not in output
    report = json.loads(output)
    assert report["code"] == "CLOUD_RUNTIME_IDENTITY_DIAGNOSTIC"
    assert report["scope"] == "identity_only_no_permission_changes"
    assert all(isinstance(value, bool) or value is None for value in report["checks"].values())
    assert report["blocking_codes"] == ["CONNECTED_DATABASE_MISMATCH", "CONNECTED_CURRENT_USER_MISMATCH",
        "CONNECTED_SESSION_USER_MISMATCH", "DATABASE_OWNER_MISMATCH", "CLIENT_TLS_NOT_ACTIVE"]
    assert report["legacy_failure_codes"][-1] == "BACKEND_TLS_NOT_ACTIVE"
    assert "SET TRANSACTION READ ONLY" in engine.conn.statements
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)
    assert not any("runtime:role" in sql or sql == "SCHEMA_GATE_CHECKED" for sql in engine.conn.statements)


def test_identity_diagnostic_does_not_claim_runtime_permissions_are_ready(engine, monkeypatch, capsys):
    engine.conn.unsafe_authority = True
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--diagnose"]) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "identity_only_no_permission_changes"
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


@pytest.mark.parametrize("other_mode", ["apply", "check"])
def test_diagnose_cannot_be_combined_with_any_permission_mode(other_mode):
    with pytest.raises(tool.PermissionSafetyError, match="EXCLUSIVE_MODE_REQUIRED"):
        tool.run(DSN, tool.DATABASE, diagnose=True, **{other_mode: True})


def test_missing_tls_driver_attribute_exception_does_not_leak(engine, monkeypatch, capsys):
    class FailingPGconn:
        @property
        def ssl_in_use(self):
            raise RuntimeError("SECRET " + DSN)
    engine.conn.connection.driver_connection.pgconn = FailingPGconn()
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE]) == 2
    assert capsys.readouterr().out == "CLIENT_TLS_STATUS_UNAVAILABLE\n"


def runtime_report(engine):
    report = json.loads(tool.run(DSN, tool.DATABASE, diagnose_runtime=True))
    assert report["code"] == "CLOUD_RUNTIME_ROLE_DIAGNOSTIC"
    assert report["scope"] == "role_attributes_and_memberships_only"
    assert all(isinstance(value, bool) for value in report["checks"].values())
    assert isinstance(report["legacy_role_gate_failed"], bool)
    assert "SET TRANSACTION READ ONLY" in engine.conn.statements
    assert not any(sql.startswith(("GRANT", "REVOKE", "ALTER", "SET ROLE")) for sql in engine.conn.statements)
    assert "SCHEMA_GATE_CHECKED" not in engine.conn.statements
    return report


def test_runtime_diagnostic_healthy_login_with_inherit_alone_is_not_privileged(engine):
    report = runtime_report(engine)
    assert report["checks"]["runtime_role_exists"]
    assert report["checks"]["runtime_inherit_attribute"]
    assert not report["checks"]["runtime_inherits_from_privileged_role"]
    assert not report["legacy_role_gate_failed"]
    assert report["blocking_codes"] == []


def test_runtime_diagnostic_missing_role_is_distinct_and_does_not_query_membership(engine):
    engine.conn.role = []
    report = runtime_report(engine)
    assert not report["checks"]["runtime_role_exists"]
    assert report["blocking_codes"] == ["RUNTIME_ROLE_MISSING"]
    assert report["legacy_role_gate_failed"]
    assert not any("runtime:diagnostic_memberships" in sql for sql in engine.conn.statements)


@pytest.mark.parametrize("index,code", [(1, "RUNTIME_ROLE_LOGIN_DISABLED"), (2, "RUNTIME_ROLE_SUPERUSER"),
    (3, "RUNTIME_ROLE_CREATEDB"), (4, "RUNTIME_ROLE_CREATEROLE"), (5, "RUNTIME_ROLE_REPLICATION"), (6, "RUNTIME_ROLE_BYPASSRLS")])
def test_runtime_diagnostic_identifies_each_legacy_gate_attribute(engine, index, code):
    row = list(engine.conn.role[0])
    row[index] = not row[index]
    engine.conn.role = [tuple(row)]
    report = runtime_report(engine)
    assert report["checks"]["runtime_role_exists"]
    assert report["blocking_codes"] == [code]
    assert report["legacy_role_gate_failed"]


@pytest.mark.parametrize("direct", [True, False])
def test_runtime_diagnostic_neon_membership_direct_or_recursive_without_inheritance(engine, direct):
    engine.conn.diagnostic_memberships = [(True, True, False, False, direct)]
    report = runtime_report(engine)
    assert report["checks"]["runtime_member_of_neon_superuser"]
    assert report["checks"]["runtime_direct_member_of_neon_superuser"] is direct
    assert not report["checks"]["runtime_inherits_from_privileged_role"]
    assert not report["legacy_role_gate_failed"]
    assert report["blocking_codes"] == ["RUNTIME_ROLE_NEON_SUPERUSER_MEMBER", "RUNTIME_ROLE_HAS_MEMBERSHIPS"]


def test_runtime_diagnostic_distinguishes_inherited_permissions_from_special_role_attributes(engine):
    engine.conn.diagnostic_memberships = [(True, True, True, True, True)]
    report = runtime_report(engine)
    assert report["checks"]["runtime_inherits_from_privileged_role"]
    assert report["checks"]["runtime_can_set_privileged_role"]
    assert not report["checks"]["runtime_has_createdb"]
    assert not report["checks"]["runtime_has_createrole"]
    assert not report["checks"]["runtime_has_bypassrls"]
    assert "RUNTIME_ROLE_INHERITS_PRIVILEGED_ROLE_PERMISSIONS" in report["blocking_codes"]
    assert "RUNTIME_ROLE_CAN_SET_PRIVILEGED_ROLE" in report["blocking_codes"]


def test_runtime_diagnostic_uses_actual_usage_paths_not_only_rolinherit(engine):
    engine.conn.overrides["diagnostic_role"] = [(True, False, False, False, False, False, False)]
    engine.conn.diagnostic_memberships = [(False, True, True, False, False)]
    report = runtime_report(engine)
    assert not report["checks"]["runtime_inherit_attribute"]
    assert report["checks"]["runtime_inherits_from_privileged_role"]
    sql = next(s for s in engine.conn.statements if "runtime:diagnostic_memberships" in s)
    assert "WITH RECURSIVE" in sql and " UNION " in sql and "UNION ALL" not in sql
    assert "pg_has_role(t.oid,p.oid,'USAGE')" in sql
    assert "pg_has_role(t.oid,p.oid,'SET')" in sql


def test_runtime_diagnostic_benign_membership_still_explains_strict_membership_gate(engine):
    engine.conn.diagnostic_memberships = [(False, False, True, True, True)]
    report = runtime_report(engine)
    assert not report["checks"]["runtime_has_privileged_role_membership"]
    assert report["blocking_codes"] == ["RUNTIME_ROLE_HAS_MEMBERSHIPS"]


def test_runtime_diagnostic_multiple_rejection_causes_are_all_reported_without_names(engine, monkeypatch, capsys):
    engine.conn.role = [(42, True, True, True, True, False, True)]
    engine.conn.diagnostic_memberships = [(True, True, True, True, True)]
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--diagnose-runtime"]) == 2
    output = capsys.readouterr().out
    assert DSN not in output and "DO_NOT_PRINT" not in output and "neondb_owner" not in output
    report = json.loads(output)
    assert {"RUNTIME_ROLE_SUPERUSER", "RUNTIME_ROLE_CREATEDB", "RUNTIME_ROLE_CREATEROLE", "RUNTIME_ROLE_BYPASSRLS",
            "RUNTIME_ROLE_NEON_SUPERUSER_MEMBER", "RUNTIME_ROLE_INHERITS_PRIVILEGED_ROLE_PERMISSIONS"}.issubset(report["blocking_codes"])
    assert not any(sql in tool.GRANTS for sql in engine.conn.statements)


@pytest.mark.parametrize("other_mode", ["apply", "check", "diagnose"])
def test_runtime_diagnostic_is_mutually_exclusive_before_connection(other_mode):
    with pytest.raises(tool.PermissionSafetyError, match="EXCLUSIVE_MODE_REQUIRED"):
        tool.run(DSN, tool.DATABASE, diagnose_runtime=True, **{other_mode: True})


def test_runtime_diagnostic_requires_valid_live_tls_before_role_queries(engine):
    engine.conn.connection.driver_connection.pgconn.ssl_in_use = False
    with pytest.raises(tool.PermissionSafetyError, match="CLIENT_TLS_NOT_ACTIVE"):
        tool.run(DSN, tool.DATABASE, diagnose_runtime=True)
    assert not any("runtime:diagnostic_role" in sql for sql in engine.conn.statements)


@pytest.mark.parametrize("category,rows,code", [
    ("diagnostic_role", [(True, False, "SECRET_ATTRIBUTE", False, False, False, True)], "RUNTIME_ROLE_METADATA_UNAVAILABLE"),
    ("diagnostic_memberships", [(True, True, None, True, True)], "RUNTIME_MEMBERSHIP_METADATA_UNAVAILABLE"),
])
def test_runtime_diagnostic_unexpected_metadata_is_rejected_without_echo(engine, category, rows, code):
    engine.conn.overrides[category] = rows
    with pytest.raises(tool.PermissionSafetyError, match=code) as failure:
        tool.run(DSN, tool.DATABASE, diagnose_runtime=True)
    assert "SECRET" not in str(failure.value)


def test_runtime_diagnostic_pre_pg16_does_not_use_unsupported_set_function(engine):
    engine.conn.server_version = "150010"
    report = runtime_report(engine)
    sql = next(s for s in engine.conn.statements if "runtime:diagnostic_memberships" in s)
    assert "pg_has_role(t.oid,p.oid,'SET')" not in sql
    assert report["blocking_codes"] == []
