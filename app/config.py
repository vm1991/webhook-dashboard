import os

from dotenv import load_dotenv

load_dotenv()


def get_max_attempts() -> int:
    """Total attempts per event. Read at call time so tests can override it."""
    raw = os.environ.get("MAX_ATTEMPTS", "5")
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"MAX_ATTEMPTS must be an integer >= 1, got {raw!r}") from None
    if value < 1:
        raise RuntimeError(f"MAX_ATTEMPTS must be an integer >= 1, got {raw!r}")
    return value


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Neon/Render give postgresql://; force the psycopg 3 driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url
