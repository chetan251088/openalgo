# utils/config.py

import os

from dotenv import load_dotenv

# Load environment variables with override=True to ensure values are updated
# Supports DOTENV_FILE env var for multi-instance setups (e.g. .env.kotak, .env.dhan)
_dotenv_file = os.environ.get("DOTENV_FILE")
if _dotenv_file:
    _dotenv_path = os.path.join(os.path.dirname(__file__), "..", _dotenv_file)
    load_dotenv(dotenv_path=_dotenv_path, override=True)
else:
    load_dotenv(override=True)


def get_broker_api_key():
    return os.getenv("BROKER_API_KEY")


def get_broker_api_secret():
    return os.getenv("BROKER_API_SECRET")


def get_login_rate_limit_min():
    return os.getenv("LOGIN_RATE_LIMIT_MIN", "5 per minute")


def get_login_rate_limit_hour():
    return os.getenv("LOGIN_RATE_LIMIT_HOUR", "25 per hour")


def get_host_server():
    return os.getenv("HOST_SERVER", "http://127.0.0.1:5000")
