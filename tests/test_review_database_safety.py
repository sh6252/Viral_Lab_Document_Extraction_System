"""No connection required: fail closed before disposable-schema setup."""

import pytest

from tests.integration.conftest import validate_test_dsn


@pytest.mark.parametrize("dsn,ack", [
    ("postgresql://localhost/production", "yes"),
    ("postgresql://localhost/review_test_example", None),
    ("postgresql://remote/review_test_example", "yes"),
    ("sqlite:///review_test_example", "yes"),
    ("postgresql://localhost/review_test_example?options=-csearch_path=public", "yes"),
    ("invalid", "yes"),
])
def test_unsafe_test_dsn_is_rejected_before_connecting(dsn, ack):
    with pytest.raises(ValueError, match="^EXPLICIT_LOCAL_DISPOSABLE_POSTGRESQL_TEST_DSN_REQUIRED$"):
        validate_test_dsn(dsn, ack)


def test_explicit_local_disposable_database_is_admitted():
    assert validate_test_dsn("postgresql://127.0.0.1/review_test_example", "yes").database == "review_test_example"
