"""Generate only a salted backend password hash using an interactive prompt."""
from getpass import getpass

from backend.core.demo_auth import hash_password


def main() -> None:
    password = getpass("New invited-judge password (16+ characters): ")
    if password != getpass("Repeat password: "):
        raise SystemExit("PASSWORD_CONFIRMATION_MISMATCH")
    print(hash_password(password))


if __name__ == "__main__":
    main()
