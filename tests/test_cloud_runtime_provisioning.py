"""New SQL role provisioning and authenticated rollback probes: no real database."""
from base64 import b64encode
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import warnings
import json

import pytest

from backend.persistence.cloud_database import neon_url
from backend.tools import cloud_runtime_permissions as tool
from tests.test_cloud_runtime_permissions import Connection, DSN, Result, no_external_connections  # noqa: F401

PASSWORD = "SYNTHETIC-password-manager-fixture-0123456789!"
VERIFIER = b"SCRAM-SHA-256$4096:" + b64encode(b"s" * 16) + b"$" + b64encode(b"k" * 32) + b":" + b64encode(b"v" * 32)


class Denied(Exception):
    sqlstate = "42501"


class Savepoint:
    def __init__(self, conn):
        self.conn = conn
        self.before = deepcopy(conn.probes)

    def rollback(self):
        self.conn.probes = self.before
        self.conn.savepoint_rollbacks += 1


class ProvisionConnection(Connection):
    def __init__(self, state, owner):
        super().__init__()
        self.state, self.owner = state, owner
        self.probes = {}
        self.savepoint_rollbacks = 0
        self.sensitive_statements = []
        self.connection.driver_connection.pgconn.encrypt_password = self.encrypt
        self.connection.driver_connection.cursor = self.cursor
        self.capture_setting_present = True
        self.expect_strict_guard = True
        self.allow_forbidden = False
        self.deny_state = "42501"

    def encrypt(self, password, role, algorithm):
        pytest.fail("Neon creation must never pre-hash the password")

    @contextmanager
    def cursor(self):
        yield self

    def begin_nested(self):
        return Savepoint(self)

    def execute(self, statement, params=None):
        if not isinstance(statement, str) and hasattr(statement, "as_string"):
            sql = statement.as_string()
            self.sensitive_statements.append(sql)
            assert PASSWORD not in sql and "SCRAM-SHA-256" not in sql
            assert sql.endswith(" PASSWORD %s") and params == (PASSWORD,)
            assert ("SET LOCAL log_min_error_statement = 'panic'" in self.statements) is self.expect_strict_guard
            assert "SET LOCAL password_encryption = 'scram-sha-256'" in self.statements
            assert not self.state["exists"]
            self.state["exists"] = True
            return Result([])
        sql = str(statement)
        if "runtime:password_capture_setting" in sql:
            assert params["setting"] in ("pg_stat_statements.track", "pgaudit.log")
            self.statements.append(sql)
            return Result([(self.capture_setting_present,)])
        if "runtime:new_role_exists" in sql:
            assert params == {"runtime": tool.APP_RUNTIME}
            self.statements.append(sql)
            return Result([(self.state["oid"],)] if self.state["exists"] else [])
        if sql in tool._role_grants(tool.APP_RUNTIME):
            self.statements.append(sql)
            if sql.startswith("REVOKE"):
                self.state["public_create"] = False
            if sql == tool._role_grants(tool.APP_RUNTIME)[-1]:
                self.state["applied"] = True
            return Result([])
        if sql == f"ALTER ROLE {tool.APP_RUNTIME} NOLOGIN":
            self.statements.append(sql)
            self.state["login"] = False
            return Result([])
        if "runtime:login_identity" in sql:
            self.statements.append(sql)
            return Result([(tool.DATABASE, tool.APP_RUNTIME, tool.APP_RUNTIME)])
        if sql.startswith(("INSERT INTO", "UPDATE public")) and "SET source_bytes=source_bytes" not in sql and "SET event_json=event_json" not in sql:
            self.statements.append(sql)
            if sql.startswith("INSERT"):
                self.probes[sql.split()[2]] = params["id"]
            return SimpleNamespace(rowcount=1)
        if sql.startswith("SELECT count(*) FROM public."):
            self.statements.append(sql)
            return Result([(int(sql.split()[3] in self.probes),)])
        if sql.startswith(("CREATE SCHEMA", "CREATE TABLE", "DELETE FROM")) or "SET source_bytes=source_bytes" in sql or "SET event_json=event_json" in sql:
            self.statements.append(sql)
            if self.allow_forbidden:
                self.probes["unexpected_forbidden_operation"] = True
                return Result([])
            error = Denied("SECRET server message " + DSN)
            error.sqlstate = self.deny_state
            raise error
        self.applied = self.state["applied"]
        self.public_create = self.state["public_create"]
        if params and "runtime" in params:
            assert params["runtime"] == tool.APP_RUNTIME
        return super().execute(statement, params)


class ProvisionEngine:
    def __init__(self, state, owner):
        self.state = state
        self.conn = ProvisionConnection(state, owner)
        self.commits = self.rollbacks = 0
        self.disposed = False
        self.commit_error = None

    @contextmanager
    def begin(self):
        before, probes = deepcopy(self.state), deepcopy(self.conn.probes)
        try:
            yield self.conn
            if self.commit_error is not None:
                raise self.commit_error
        except BaseException:
            # Match SQLAlchemy's context manager: Ctrl+C also rolls back.
            self.state.clear()
            self.state.update(before)
            self.conn.probes = probes
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def dispose(self):
        self.disposed = True


@pytest.fixture
def provision(monkeypatch):
    state = {"exists": False, "applied": False, "public_create": True, "login": True, "oid": 123}
    owner, runtime = ProvisionEngine(state, True), ProvisionEngine(state, False)
    calls = []
    def factory(dsn, database):
        url = neon_url(dsn, database)
        assert url.host == "ep-demo.eu-central-1.aws.neon.tech" and database == tool.DATABASE
        calls.append(url.username)
        if url.username == tool.OWNER:
            return owner
        assert url.username == tool.APP_RUNTIME and url.password == PASSWORD
        assert state["exists"] and state["login"]
        return runtime
    def schema(bound, database):
        with bound.connect() as conn:
            assert conn in (owner.conn, runtime.conn)
            conn.statements.append("SCHEMA_GATE_CHECKED")
    monkeypatch.setattr(tool, "create_cloud_engine", factory)
    monkeypatch.setattr(tool, "check_business_schema", schema)
    def client_cursor(driver):
        assert driver is owner.conn.connection.driver_connection
        return owner.conn.cursor()
    monkeypatch.setattr(tool, "ClientCursor", client_cursor)
    return SimpleNamespace(owner=owner, runtime=runtime, state=state, calls=calls)


def create(**kwargs):
    return tool.run(DSN, tool.DATABASE, runtime=tool.APP_RUNTIME, create_runtime=True, password=PASSWORD, **kwargs)


def test_create_new_role_and_exact_grants_then_authenticate_and_rollback_every_probe(provision):
    assert create().startswith("CLOUD_NEW_RUNTIME_READY")
    assert provision.calls == [tool.OWNER, tool.APP_RUNTIME]
    assert provision.owner.commits == 1 and provision.runtime.commits == 0
    assert provision.runtime.rollbacks == 1 and not provision.runtime.conn.probes
    assert provision.runtime.conn.savepoint_rollbacks == 7
    assert provision.state == {"exists": True, "applied": True, "public_create": False, "login": True, "oid": 123}
    sql = provision.owner.conn.sensitive_statements[0]
    assert 'CREATE ROLE "viral_lab_app_runtime"' in sql
    assert "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT" in sql
    assert PASSWORD not in sql
    grants = [s for s in provision.owner.conn.statements if s.startswith(("GRANT", "REVOKE"))]
    assert grants == list(tool._role_grants(tool.APP_RUNTIME))
    assert not any(tool.RUNTIME in s for s in grants + [sql])
    assert provision.owner.disposed and provision.runtime.disposed


def test_existing_new_role_is_never_modified_or_password_reset(provision):
    provision.state["exists"] = True
    with pytest.raises(tool.PermissionSafetyError, match="NEW_RUNTIME_ALREADY_EXISTS_NO_CHANGES"):
        create()
    assert not provision.owner.conn.sensitive_statements
    assert not any(s.startswith(("GRANT", "REVOKE", "ALTER")) for s in provision.owner.conn.statements)


@pytest.mark.parametrize("acknowledge", [False, True])
def test_schema_failure_precedes_role_creation(provision, monkeypatch, acknowledge):
    def fail(*args):
        raise RuntimeError("SECRET " + DSN)
    monkeypatch.setattr(tool, "check_business_schema", fail)
    with pytest.raises(tool.PermissionSafetyError, match="CLOUD_SCHEMA_NOT_READY"):
        create(acknowledge_server_password_logging_risk=acknowledge)
    assert not provision.state["exists"] and not provision.owner.conn.sensitive_statements


@pytest.mark.parametrize("gate,code", [
    ("tls", "CLIENT_TLS_NOT_ACTIVE"), ("database", "CONNECTED_DATABASE_MISMATCH"),
    ("current_user", "CONNECTED_CURRENT_USER_MISMATCH"),
    ("session_user", "CONNECTED_SESSION_USER_MISMATCH"), ("owner", "DATABASE_OWNER_MISMATCH"),
])
@pytest.mark.parametrize("acknowledge", [False, True])
def test_live_identity_or_tls_failure_never_sends_the_password(provision, gate, code, acknowledge):
    if gate == "tls":
        provision.owner.conn.connection.driver_connection.pgconn.ssl_in_use = False
    else:
        identity = list(provision.owner.conn.identity)
        identity[{"database": 0, "current_user": 1, "session_user": 2, "owner": 3}[gate]] = "unexpected"
        provision.owner.conn.identity = tuple(identity)
    with pytest.raises(tool.PermissionSafetyError, match=code):
        create(acknowledge_server_password_logging_risk=acknowledge)
    assert not provision.owner.conn.sensitive_statements and not provision.state["exists"]
    assert not any(s.startswith(("GRANT", "REVOKE", "ALTER")) for s in provision.owner.conn.statements)


def test_unexpected_elevated_membership_after_sql_creation_rolls_back_before_grants(provision):
    provision.owner.conn.unsafe_authority = True
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_MEMBERSHIP_OR_OWNERSHIP_REJECTED"):
        create()
    assert not provision.state["exists"]
    assert not any(s.startswith(("GRANT", "REVOKE")) for s in provision.owner.conn.statements)


def test_creation_failure_rolls_back_new_role_and_all_acl_changes(provision, monkeypatch):
    original = provision.owner.conn.execute
    def fail(statement, params=None):
        if str(statement) == tool._role_grants(tool.APP_RUNTIME)[4]:
            raise RuntimeError("SECRET " + PASSWORD)
        return original(statement, params)
    monkeypatch.setattr(provision.owner.conn, "execute", fail)
    with pytest.raises(tool.PermissionSafetyError, match="NEW_RUNTIME_PRECOMMIT_GRANT_05_FAILED") as failure:
        create()
    assert PASSWORD not in str(failure.value)
    assert not provision.state["exists"] and not provision.state["applied"] and provision.state["public_create"]
    assert provision.calls == [tool.OWNER]


@pytest.mark.parametrize("kind", ["unexpected_success", "wrong_sqlstate"])
def test_post_commit_probe_failure_rolls_back_probe_and_disables_only_new_role(provision, kind):
    if kind == "unexpected_success":
        provision.runtime.conn.allow_forbidden = True
    else:
        provision.runtime.conn.deny_state = "P0001"
    with pytest.raises(tool.PermissionSafetyError, match="NEW_RUNTIME_VERIFY_FAILED_ROLE_DISABLED"):
        create()
    assert provision.state["exists"] and not provision.state["login"]
    assert provision.runtime.rollbacks == 1 and not provision.runtime.conn.probes
    assert f"ALTER ROLE {tool.APP_RUNTIME} NOLOGIN" in provision.owner.conn.statements
    assert not any(f"ALTER ROLE {tool.RUNTIME}" in s or s.startswith("DROP ROLE") for s in provision.owner.conn.statements)


def test_failed_disable_is_reported_without_claiming_role_is_disabled(provision, monkeypatch):
    provision.runtime.conn.allow_forbidden = True
    original = provision.owner.conn.execute
    def fail(statement, params=None):
        if str(statement).startswith("ALTER ROLE"):
            raise RuntimeError("SECRET " + DSN)
        return original(statement, params)
    monkeypatch.setattr(provision.owner.conn, "execute", fail)
    with pytest.raises(tool.PermissionSafetyError, match="NEW_RUNTIME_VERIFY_FAILED_DISABLE_UNCONFIRMED"):
        create()
    assert provision.state["login"]


def test_authentication_failure_disables_only_just_created_role(provision, monkeypatch):
    factory = tool.create_cloud_engine
    def fail_runtime(dsn, database):
        if neon_url(dsn, database).username == tool.APP_RUNTIME:
            raise RuntimeError("SECRET " + PASSWORD)
        return factory(dsn, database)
    monkeypatch.setattr(tool, "create_cloud_engine", fail_runtime)
    with pytest.raises(tool.PermissionSafetyError, match="NEW_RUNTIME_VERIFY_FAILED_ROLE_DISABLED") as failure:
        create()
    assert PASSWORD not in str(failure.value)
    assert not provision.state["login"]


def test_changed_role_oid_prevents_disabling_a_replacement_role(provision, monkeypatch):
    def fail(*args):
        provision.state["oid"] += 1
        raise RuntimeError("verification failed")
    monkeypatch.setattr(tool, "_verify_new_runtime", fail)
    with pytest.raises(tool.PermissionSafetyError, match="NEW_RUNTIME_VERIFY_FAILED_DISABLE_UNCONFIRMED"):
        create()
    assert provision.state["login"]
    assert not any(s.startswith("ALTER ROLE") for s in provision.owner.conn.statements)


def test_interrupted_probe_rolls_back_and_disables_only_new_role(provision, monkeypatch):
    def interrupted(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(tool, "_permission_probe", interrupted)
    with pytest.raises(tool.PermissionSafetyError, match="NEW_RUNTIME_VERIFY_FAILED_ROLE_DISABLED"):
        create()
    assert not provision.state["login"] and provision.runtime.rollbacks == 1


def test_verify_existing_new_role_never_changes_roles_and_uses_real_login_path(provision):
    provision.state.update(exists=True, applied=True, public_create=False)
    assert tool.run(DSN, tool.DATABASE, runtime=tool.APP_RUNTIME, verify_runtime=True, password=PASSWORD).startswith("CLOUD_NEW_RUNTIME_READY")
    assert provision.calls == [tool.APP_RUNTIME]
    assert provision.runtime.rollbacks == 1 and not provision.runtime.conn.probes
    assert not any(s.startswith(("ALTER ROLE", "GRANT", "REVOKE")) for s in provision.runtime.conn.statements)


@pytest.mark.parametrize("password", [None, "", "short", "x" * 257, "x" * 31 + " ", "x" * 32 + "\n", "x" * 32 + "\x00", "x" * 32 + "א"])
def test_password_validation_is_offline_and_never_echoes_value(password):
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_PASSWORD_REQUIRES"):
        tool.run(DSN, tool.DATABASE, runtime=tool.APP_RUNTIME, create_runtime=True, password=password)


def test_explicit_new_role_required_and_arbitrary_role_names_never_reach_sql():
    for runtime, code in [(tool.RUNTIME, "EXPLICIT_NEW_RUNTIME_ROLE_REQUIRED"), ("injected_role", "RUNTIME_ROLE_NOT_ALLOWLISTED")]:
        with pytest.raises(tool.PermissionSafetyError, match=code):
            tool.run(DSN, tool.DATABASE, runtime=runtime, create_runtime=True, password=PASSWORD)


def test_password_is_hidden_and_confirmed_in_cli(provision, monkeypatch, capsys):
    prompts = []
    def read(prompt):
        prompts.append(prompt)
        return PASSWORD
    monkeypatch.setattr(tool.getpass, "getpass", read)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--runtime-role", tool.APP_RUNTIME, "--create-runtime"]) == 0
    output = capsys.readouterr().out
    assert len(prompts) == 2 and PASSWORD not in output and DSN not in output and "SCRAM" not in output


def test_password_confirmation_mismatch_and_no_echo_fallback_fail_offline(monkeypatch):
    values = iter([PASSWORD, PASSWORD + "wrong"])
    monkeypatch.setattr(tool.getpass, "getpass", lambda _: next(values))
    with pytest.raises(tool.PermissionSafetyError, match="RUNTIME_PASSWORD_CONFIRMATION_MISMATCH"):
        tool._read_password(confirm=True)
    def warning(_):
        warnings.warn("no safe terminal", tool.getpass.GetPassWarning)
    monkeypatch.setattr(tool.getpass, "getpass", warning)
    with pytest.raises(tool.PermissionSafetyError, match="SECURE_INTERACTIVE_PASSWORD_INPUT_REQUIRED"):
        tool._read_password(confirm=True)


def test_password_input_os_errors_are_redacted(monkeypatch):
    def fail(_):
        raise RuntimeError("SECRET " + PASSWORD)
    monkeypatch.setattr(tool.getpass, "getpass", fail)
    with pytest.raises(tool.PermissionSafetyError, match="SECURE_INTERACTIVE_PASSWORD_INPUT_REQUIRED") as failure:
        tool._read_password(confirm=True)
    assert PASSWORD not in str(failure.value)


def test_cli_engine_error_never_echoes_password_or_connection_string(monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError("SECRET " + DSN + " " + PASSWORD)
    monkeypatch.setattr(tool, "create_cloud_engine", fail)
    monkeypatch.setattr(tool.getpass, "getpass", lambda _: PASSWORD)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--runtime-role", tool.APP_RUNTIME, "--create-runtime"]) == 2
    assert capsys.readouterr().out == "NEW_RUNTIME_PRECOMMIT_ENGINE_FAILED\n"


@pytest.mark.parametrize("password", ["SYNTHETIC-quote'backslash\\percent%s-0123456789!", "SYNTHETIC-');DROP/**/ROLE/**/old_role;--0123456789!"])
def test_special_characters_are_a_separate_clientcursor_parameter_not_sql(provision, monkeypatch, password, capsys, caplog):
    monkeypatch.setitem(globals(), "PASSWORD", password)
    assert create().startswith("CLOUD_NEW_RUNTIME_READY")
    template = provision.owner.conn.sensitive_statements[0]
    assert template.endswith(" PASSWORD %s") and password not in template
    assert all(password not in sql for sql in provision.owner.conn.statements)
    output = capsys.readouterr()
    assert password not in output.out + output.err + caplog.text


@pytest.mark.parametrize("mode", ["apply", "check", "diagnose", "diagnose_runtime", "verify_runtime", "diagnose_provisioning"])
def test_creation_cannot_be_combined_with_other_modes(mode):
    with pytest.raises(tool.PermissionSafetyError, match="EXCLUSIVE_MODE_REQUIRED"):
        tool.run(DSN, tool.DATABASE, runtime=tool.APP_RUNTIME, create_runtime=True, password=PASSWORD, **{mode: True})


class SensitiveFailure(Exception):
    def __init__(self, state):
        self.sqlstate = state
        super().__init__("SECRET " + DSN + " " + PASSWORD + " " + VERIFIER.decode())


@pytest.mark.parametrize("state,reason", [
    ("42501", "INSUFFICIENT_PRIVILEGE"), ("25001", "TRANSACTION_BLOCK_REJECTED"),
    ("25006", "READ_ONLY_TRANSACTION"), ("0A000", "FEATURE_UNSUPPORTED"),
    ("28P01", "AUTHENTICATION_FAILED"), ("42710", "DUPLICATE_OBJECT"),
    ("XX000", "SERVER_INTERNAL_ERROR"), ("08006", "CONNECTION_FAILED"),
    (PASSWORD, "FAILED"), (None, "FAILED"),
])
def test_create_statement_failure_is_precommit_and_redacts_every_sensitive_value(provision, monkeypatch, state, reason):
    original = provision.owner.conn.execute
    def fail(statement, params=None):
        if hasattr(statement, "as_string"):
            raise SensitiveFailure(state)
        return original(statement, params)
    monkeypatch.setattr(provision.owner.conn, "execute", fail)
    with pytest.raises(tool.PermissionSafetyError) as failure:
        create()
    assert str(failure.value) == "NEW_RUNTIME_PRECOMMIT_CREATE_ROLE_" + reason
    assert not provision.state["exists"] and not provision.state["applied"]
    assert provision.owner.commits == 0 and provision.owner.rollbacks == 1
    assert provision.calls == [tool.OWNER]
    assert not any(s.startswith(("GRANT", "REVOKE", "ALTER")) for s in provision.owner.conn.statements)


@pytest.mark.parametrize("number", range(1, 8))
def test_each_documented_grant_has_its_own_failure_stage_and_rolls_back_every_change(provision, monkeypatch, number):
    original = provision.owner.conn.execute
    def fail(statement, params=None):
        if str(statement) == tool._role_grants(tool.APP_RUNTIME)[number - 1]:
            raise SensitiveFailure("42501")
        return original(statement, params)
    monkeypatch.setattr(provision.owner.conn, "execute", fail)
    with pytest.raises(tool.PermissionSafetyError) as failure:
        create()
    assert str(failure.value) == f"NEW_RUNTIME_PRECOMMIT_GRANT_{number:02d}_INSUFFICIENT_PRIVILEGE"
    assert not provision.state["exists"] and not provision.state["applied"] and provision.state["public_create"]
    assert provision.owner.commits == 0 and provision.calls == [tool.OWNER]


@pytest.mark.parametrize("statement", [
    "SET LOCAL log_statement = 'none'", "SET LOCAL log_min_error_statement = 'panic'",
    "SET LOCAL log_min_messages = 'panic'", "SET LOCAL log_min_duration_statement = -1",
    "SET LOCAL log_min_duration_sample = -1", "SET LOCAL log_transaction_sample_rate = 0",
    "SET LOCAL password_encryption = 'scram-sha-256'",
    "SET LOCAL pg_stat_statements.track = 'none'", "SET LOCAL pgaudit.log = 'none'",
])
def test_logging_guard_permission_failure_names_the_setting_before_create_not_commit(provision, monkeypatch, statement):
    original = provision.owner.conn.execute
    def fail(sql, params=None):
        if str(sql) == statement:
            raise SensitiveFailure("42501")
        return original(sql, params)
    monkeypatch.setattr(provision.owner.conn, "execute", fail)
    setting = statement.split()[2].upper().replace(".", "_")
    stage = "PASSWORD_POLICY" if setting == "PASSWORD_ENCRYPTION" else "PASSWORD_LOGGING_GUARD_" + setting
    with pytest.raises(tool.PermissionSafetyError, match="^NEW_RUNTIME_PRECOMMIT_" + stage + "_INSUFFICIENT_PRIVILEGE$"):
        create()
    assert not provision.owner.conn.sensitive_statements and not provision.state["exists"]
    assert provision.owner.commits == 0 and provision.owner.rollbacks == 1
    assert not any(s.startswith(("GRANT", "REVOKE", "ALTER")) for s in provision.owner.conn.statements)


def test_native_neon_path_changes_no_logging_settings_and_preserves_grants_login_and_rollback(provision, monkeypatch):
    provision.owner.conn.expect_strict_guard = False
    original = provision.owner.conn.execute
    def reject_logging(statement, params=None):
        sql = str(statement)
        if sql.startswith(("SET LOCAL log_", "SET LOCAL pgaudit", "SET LOCAL pg_stat_statements")) or "runtime:password_capture_setting" in sql:
            pytest.fail("Explicit Neon-native workflow must not touch server logging settings")
        return original(statement, params)
    monkeypatch.setattr(provision.owner.conn, "execute", reject_logging)
    assert create(acknowledge_server_password_logging_risk=True).startswith("CLOUD_NEW_RUNTIME_READY")
    assert provision.calls == [tool.OWNER, tool.APP_RUNTIME]
    assert provision.owner.commits == 1 and provision.runtime.rollbacks == 1
    assert not provision.runtime.conn.probes
    assert [s for s in provision.owner.conn.statements if s.startswith(("GRANT", "REVOKE"))] == list(tool._role_grants(tool.APP_RUNTIME))
    assert not any(tool.RUNTIME in s for s in provision.owner.conn.statements + provision.owner.conn.sensitive_statements)


def test_strict_guard_denial_never_falls_back_to_native_creation(provision, monkeypatch):
    original = provision.owner.conn.execute
    def deny_first_setting(statement, params=None):
        if str(statement) == "SET LOCAL log_statement = 'none'":
            raise SensitiveFailure("42501")
        return original(statement, params)
    monkeypatch.setattr(provision.owner.conn, "execute", deny_first_setting)
    with pytest.raises(tool.PermissionSafetyError, match="^NEW_RUNTIME_PRECOMMIT_PASSWORD_LOGGING_GUARD_LOG_STATEMENT_INSUFFICIENT_PRIVILEGE$"):
        create()
    assert not provision.state["exists"] and not provision.owner.conn.sensitive_statements
    assert provision.calls == [tool.OWNER]
    assert "SET LOCAL password_encryption = 'scram-sha-256'" not in provision.owner.conn.statements


def test_cli_discloses_native_server_logging_risk_before_hidden_password_input(provision, monkeypatch, capsys, caplog):
    provision.owner.conn.expect_strict_guard = False
    prompts = []
    def read_hidden(prompt):
        prompts.append(prompt)
        output = capsys.readouterr().out
        if len(prompts) == 1:
            assert "SERVER_PASSWORD_LOGGING_RISK_ACKNOWLEDGED" in output
            assert "server/provider logs may retain" in output
        assert PASSWORD not in output and DSN not in output
        return PASSWORD
    monkeypatch.setattr(tool.getpass, "getpass", read_hidden)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--runtime-role", tool.APP_RUNTIME,
                      "--create-runtime", "--acknowledge-server-password-logging-risk"]) == 0
    output = capsys.readouterr()
    assert len(prompts) == 2
    assert "CLOUD_NEW_RUNTIME_READY" in output.out
    assert not any(secret in output.out + output.err + caplog.text for secret in (PASSWORD, DSN, VERIFIER.decode()))


@pytest.mark.parametrize("mode", [None, "apply", "check", "diagnose", "diagnose_runtime", "diagnose_provisioning", "verify_runtime"])
def test_logging_risk_acknowledgement_is_creation_only_and_never_connects_or_prompts(mode, monkeypatch, capsys):
    options = {mode: True} if mode else {}
    with pytest.raises(tool.PermissionSafetyError, match="SERVER_PASSWORD_LOGGING_ACK_REQUIRES_CREATE_RUNTIME"):
        tool.run(DSN, tool.DATABASE, runtime=tool.APP_RUNTIME, acknowledge_server_password_logging_risk=True, **options)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    def forbidden(*args):
        pytest.fail("Invalid mode must fail before asking for a password")
    monkeypatch.setattr(tool.getpass, "getpass", forbidden)
    args = ["--confirm-synthetic-database", tool.DATABASE, "--runtime-role", tool.APP_RUNTIME,
            "--acknowledge-server-password-logging-risk"]
    if mode:
        args.append("--" + mode.replace("_", "-"))
    assert tool.main(args) == 2
    assert capsys.readouterr().out == "SERVER_PASSWORD_LOGGING_ACK_REQUIRES_CREATE_RUNTIME\n"


@pytest.mark.parametrize("exists", [False, True])
def test_new_runtime_existence_diagnostic_is_read_only_and_does_not_attempt_logging_changes(provision, exists):
    provision.state["exists"] = exists
    if not exists:
        provision.owner.conn.role = []
    report = json.loads(tool.run(DSN, tool.DATABASE, runtime=tool.APP_RUNTIME, diagnose_runtime=True))
    assert report["checks"]["runtime_role_exists"] is exists
    assert report["blocking_codes"] == ([] if exists else ["RUNTIME_ROLE_MISSING"])
    assert "SET TRANSACTION READ ONLY" in provision.owner.conn.statements
    assert not provision.owner.conn.sensitive_statements
    assert not any(s.startswith(("CREATE", "GRANT", "REVOKE", "ALTER", "INSERT", "UPDATE", "DELETE", "SET LOCAL log_")) for s in provision.owner.conn.statements)


def test_native_path_commit_denial_is_distinct_from_guard_denial(provision):
    provision.owner.conn.expect_strict_guard = False
    provision.owner.commit_error = SensitiveFailure("42501")
    with pytest.raises(tool.PermissionSafetyError, match="^NEW_RUNTIME_COMMIT_OUTCOME_UNCERTAIN_INSUFFICIENT_PRIVILEGE$"):
        create(acknowledge_server_password_logging_risk=True)
    assert len(provision.owner.conn.sensitive_statements) == 1
    assert not provision.state["exists"] and provision.calls == [tool.OWNER]


def test_extensions_not_loaded_need_no_extension_or_configuration_changes(provision):
    provision.owner.conn.capture_setting_present = False
    assert create().startswith("CLOUD_NEW_RUNTIME_READY")
    assert not any(s.startswith(("SET LOCAL pg_stat_statements", "SET LOCAL pgaudit", "CREATE EXTENSION")) for s in provision.owner.conn.statements)


def test_unknown_capture_state_fails_closed_before_create(provision):
    provision.owner.conn.capture_setting_present = None
    with pytest.raises(tool.PermissionSafetyError, match="PASSWORD_LOGGING_STATE_UNAVAILABLE"):
        create()
    assert not provision.owner.conn.sensitive_statements and not provision.state["exists"]


def test_clientcursor_initialization_error_is_redacted_before_create(provision, monkeypatch):
    def fail(*args):
        raise SensitiveFailure(None)
    monkeypatch.setattr(tool, "ClientCursor", fail)
    with pytest.raises(tool.PermissionSafetyError, match="^NEW_RUNTIME_PRECOMMIT_CREATE_ROLE_FAILED$"):
        create()
    assert not provision.owner.conn.sensitive_statements and not provision.state["exists"]


def test_secret_bearing_driver_error_is_never_printed_or_logged(provision, monkeypatch, capsys, caplog):
    original = provision.owner.conn.execute
    def fail(statement, params=None):
        if hasattr(statement, "as_string"):
            assert params == (PASSWORD,)
            raise SensitiveFailure("42501")
        return original(statement, params)
    monkeypatch.setattr(provision.owner.conn, "execute", fail)
    monkeypatch.setattr(tool.getpass, "getpass", lambda _: PASSWORD)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--runtime-role", tool.APP_RUNTIME, "--create-runtime"]) == 2
    output = capsys.readouterr()
    assert output.out == "NEW_RUNTIME_PRECOMMIT_CREATE_ROLE_INSUFFICIENT_PRIVILEGE\n"
    for secret in (PASSWORD, DSN, VERIFIER.decode()):
        assert secret not in output.out + output.err + caplog.text


@pytest.mark.parametrize("state", ["XX000", "08006", "42501"])
def test_neon_precommit_callback_failure_can_leave_role_absent_after_all_grants_succeeded(provision, state):
    provision.owner.commit_error = SensitiveFailure(state)
    with pytest.raises(tool.PermissionSafetyError) as failure:
        create()
    assert str(failure.value).startswith("NEW_RUNTIME_COMMIT_OUTCOME_UNCERTAIN_")
    assert len(provision.owner.conn.sensitive_statements) == 1
    assert all(s in provision.owner.conn.statements for s in tool._role_grants(tool.APP_RUNTIME))
    assert not provision.state["exists"] and not provision.state["applied"] and provision.state["public_create"]
    assert provision.calls == [tool.OWNER]  # No verification, retry, or cleanup SQL.
    assert not any(s.startswith("ALTER") for s in provision.owner.conn.statements)


def test_commit_connection_loss_never_claims_rollback_or_changes_a_possibly_committed_role(provision, monkeypatch):
    @contextmanager
    def uncertain_commit():
        yield provision.owner.conn
        # Server committed, but the client did not receive the acknowledgement.
        raise SensitiveFailure("08006")
    monkeypatch.setattr(provision.owner, "begin", uncertain_commit)
    with pytest.raises(tool.PermissionSafetyError, match="^NEW_RUNTIME_COMMIT_OUTCOME_UNCERTAIN_CONNECTION_FAILED$"):
        create()
    assert provision.state["exists"] and provision.state["login"]
    assert provision.calls == [tool.OWNER]
    assert not any(s.startswith("ALTER") for s in provision.owner.conn.statements)


def test_postcommit_login_failure_keeps_verification_phase_when_new_role_is_disabled(provision, monkeypatch):
    @contextmanager
    def login_failed():
        raise SensitiveFailure("28P01")
        yield  # pragma: no cover
    monkeypatch.setattr(provision.runtime, "begin", login_failed)
    with pytest.raises(tool.PermissionSafetyError) as failure:
        create()
    assert str(failure.value) == "NEW_RUNTIME_VERIFY_FAILED_ROLE_DISABLED: NEW_RUNTIME_VERIFY_LOGIN_AUTHENTICATION_FAILED"
    assert not provision.state["login"] and provision.state["exists"]


def test_verification_safety_failure_keeps_probe_stage(provision):
    provision.runtime.conn.allow_forbidden = True
    with pytest.raises(tool.PermissionSafetyError) as failure:
        create()
    assert str(failure.value) == "NEW_RUNTIME_VERIFY_FAILED_ROLE_DISABLED: NEW_RUNTIME_VERIFY_PROBE_SAFETY_CHECK_REJECTED"


def test_owner_engine_creation_failure_has_a_specific_redacted_code(provision, monkeypatch):
    def fail(*args):
        raise SensitiveFailure(None)
    monkeypatch.setattr(tool, "create_cloud_engine", fail)
    with pytest.raises(tool.PermissionSafetyError, match="^NEW_RUNTIME_PRECOMMIT_ENGINE_FAILED$"):
        create()


def setup_read_only_diagnostic(provision):
    provision.owner.conn.overrides.update({
        "provisioning_actor": [(False, True, True, False, False, False)],
        "provisioning_acl": [(True,) * 5], "provisioning_update_acl": [(True,)],
    })


def test_provisioning_diagnostic_is_catalog_only_and_does_not_request_any_password(provision, monkeypatch, capsys):
    setup_read_only_diagnostic(provision)
    def forbidden(*args):
        pytest.fail("Read-only diagnostics must never handle passwords or DDL")
    monkeypatch.setattr(tool.getpass, "getpass", forbidden)
    monkeypatch.setattr(provision.owner.conn.connection.driver_connection.pgconn, "encrypt_password", forbidden)
    monkeypatch.setattr(provision.owner.conn.connection.driver_connection, "cursor", forbidden)
    monkeypatch.setattr(tool, "ClientCursor", forbidden)
    monkeypatch.setenv("NEON_OWNER_DSN", DSN)
    assert tool.main(["--confirm-synthetic-database", tool.DATABASE, "--runtime-role", tool.APP_RUNTIME, "--diagnose-provisioning"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["blocking_codes"] == []
    assert report["checks"]["creation_uses_prehashed_password"] is False
    assert all(type(v) is bool for v in report["checks"].values())
    assert report["checks"]["new_runtime_role_exists"] is False
    assert not any(secret in output for secret in (DSN, PASSWORD, VERIFIER.decode(), "DO_NOT_PRINT"))
    assert "SET TRANSACTION READ ONLY" in provision.owner.conn.statements
    assert not any(s.startswith(("CREATE", "GRANT", "REVOKE", "ALTER", "INSERT", "UPDATE", "DELETE")) for s in provision.owner.conn.statements)
    assert not provision.state["exists"] and provision.calls == [tool.OWNER]


@pytest.mark.parametrize("cause", ["native", "grants", "read_only", "recovery", "exists", "column_grant"])
def test_read_only_diagnostic_distinguishes_owner_capability_blockers(provision, cause):
    setup_read_only_diagnostic(provision)
    overrides = provision.owner.conn.overrides
    actor = list(overrides["provisioning_actor"][0])
    if cause == "native":
        actor[1:3] = [False, False]
        code = "OWNER_CREATE_ROLE_AUTHORITY_NOT_OBSERVED"
    elif cause == "grants":
        overrides["provisioning_acl"] = [(True, True, False, True, True)]
        code = "OWNER_REQUIRED_GRANT_AUTHORITY_NOT_OBSERVED"
    elif cause == "column_grant":
        overrides["provisioning_update_acl"] = [(False,)]
        code = "OWNER_REQUIRED_GRANT_AUTHORITY_NOT_OBSERVED"
    else:
        index, code = {"read_only": (3, "DEFAULT_TRANSACTIONS_READ_ONLY"),
                       "recovery": (4, "SERVER_IN_RECOVERY"),
                       "exists": (5, "NEW_RUNTIME_ALREADY_EXISTS_NO_CHANGES")}[cause]
        actor[index] = True
    overrides["provisioning_actor"] = [tuple(actor)]
    report = json.loads(tool.run(DSN, tool.DATABASE, runtime=tool.APP_RUNTIME, diagnose_provisioning=True))
    assert code in report["blocking_codes"]


@pytest.mark.parametrize("gate", ["tls", "schema", "target", "role"])
def test_read_only_diagnostic_preserves_every_existing_target_gate(provision, monkeypatch, gate):
    setup_read_only_diagnostic(provision)
    dsn, runtime = DSN, tool.APP_RUNTIME
    if gate == "tls":
        provision.owner.conn.connection.driver_connection.pgconn.ssl_in_use = False
    elif gate == "schema":
        monkeypatch.setattr(tool, "check_business_schema", lambda *args: (_ for _ in ()).throw(SensitiveFailure(None)))
    elif gate == "target":
        dsn = DSN.replace("viral_lab_submission_synthetic", "viral_lab")
    else:
        runtime = tool.RUNTIME
    with pytest.raises(tool.PermissionSafetyError):
        tool.run(dsn, tool.DATABASE, runtime=runtime, diagnose_provisioning=True)
    assert not any("runtime:provisioning_actor" in s for s in provision.owner.conn.statements)
