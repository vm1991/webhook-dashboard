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


def _get_float(name: str, default: str, minimum: float) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a number >= {minimum:g}, got {raw!r}") from None
    if value < minimum:
        raise RuntimeError(f"{name} must be a number >= {minimum:g}, got {raw!r}")
    return value


def get_delivery_timeout() -> float:
    return _get_float("DELIVERY_TIMEOUT_SECONDS", "2", 0.001)


def get_sim_slow_seconds() -> float:
    return _get_float("SIM_SLOW_SECONDS", "5", 0)


def get_sim_base_url() -> str:
    url = os.environ.get("SIM_BASE_URL")
    if url:
        return url.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


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
