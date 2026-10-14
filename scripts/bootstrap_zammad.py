"""Configure a fresh Zammad from the compose stack, with no clicking through the setup wizard.

Steps:
1. Render Zammad's auto wizard file from .env (admin user, Mailpit email channel).
2. Copy it into the running railsserver container and trigger the wizard.
3. Deactivate Zammad's automatic replies to customers (the copilot never replies to
   clients, and nor should the demo ticket system).
4. Thread follow-up emails onto their ticket by their References headers.
5. Create an API token for the copilot and print it, so it can go in .env.

Run once after `docker compose up -d --wait`:

    uv run python scripts/bootstrap_zammad.py
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUPPORT_ADDRESS = "support@northgate-ms.example"


def load_env(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines from .env, letting real environment variables win."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k in values or k.startswith("ZAMMAD_")})
    return values


class Zammad:
    def __init__(self, url: str, user: str | None = None, password: str | None = None):
        self.url = url.rstrip("/")
        self.auth = None
        if user and password:
            self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.auth:
            req.add_header("Authorization", self.auth)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or "null")


def auto_wizard_config(env: dict[str, str], token: str) -> dict:
    return {
        "Token": token,
        "TextModuleLocale": {"Locale": "en-gb"},
        "Settings": [
            {"name": "product_name", "value": "Northgate Service Desk"},
            {"name": "organization", "value": "Northgate Managed Services"},
            {"name": "system_online_service", "value": False},
        ],
        "Users": [
            {
                "login": env["ZAMMAD_ADMIN_EMAIL"],
                "email": env["ZAMMAD_ADMIN_EMAIL"],
                "firstname": "Northgate",
                "lastname": "Admin",
                "password": env["ZAMMAD_ADMIN_PASSWORD"],
                "roles": ["Admin", "Agent"],
            }
        ],
        "Organizations": [{"name": "Northgate Managed Services"}],
        "Channels": [
            {
                "id": 10,
                "area": "Email::Account",
                "group_id": 1,
                "active": True,
                "options": {
                    # Mailpit only speaks plain POP3 and SMTP on the internal network.
                    "inbound": {
                        "adapter": "pop3",
                        "options": {
                            "host": "mailpit",
                            "port": 1110,
                            "user": env.get("MAILPIT_POP3_USER", "zammad"),
                            "password": env["MAILPIT_POP3_PASSWORD"],
                            "ssl": "off",
                        },
                    },
                    "outbound": {
                        "adapter": "smtp",
                        "options": {"host": "mailpit", "port": 1025, "ssl_verify": False},
                    },
                },
            }
        ],
        "EmailAddresses": [
            {"id": 1, "channel_id": 10, "name": "Northgate Service Desk", "email": SUPPORT_ADDRESS}
        ],
    }


def run_wizard(env: dict[str, str], zammad: Zammad) -> None:
    token = secrets.token_urlsafe(24)
    config = auto_wizard_config(env, token)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(config, handle)
        local = handle.name
    # Readable by the zammad user inside the container; the file is deleted below.
    os.chmod(local, 0o644)
    try:
        subprocess.run(
            ["docker", "compose", "cp", local, "zammad-railsserver:/opt/zammad/auto_wizard.json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(local)
    result = zammad.request("GET", f"/api/v1/getting_started/auto_wizard/{token}")
    if not result.get("auto_wizard_success"):
        sys.exit(f"Zammad's auto wizard did not succeed: {result}")
    # Zammad deletes the file after a successful run; remove it regardless, as it
    # contains the admin password.
    subprocess.run(
        ["docker", "compose", "exec", "-T", "zammad-railsserver", "rm", "-f", "auto_wizard.json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )


def wait_for_zammad(zammad: Zammad, timeout: int = 300) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return zammad.request("GET", "/api/v1/getting_started")
        except urllib.error.HTTPError as error:
            # Once set up, Zammad requires authentication for this endpoint.
            if error.code in (401, 403):
                return {"setup_done": True}
            raise
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                sys.exit(f"Zammad did not answer at {zammad.url} within {timeout} seconds.")
            time.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure a fresh Zammad from .env.")
    parser.add_argument(
        "--no-token",
        action="store_true",
        help="Do not create an API token (for CI, where logs are public).",
    )
    args = parser.parse_args()
    env = load_env(ROOT / ".env")
    for key in ("ZAMMAD_ADMIN_EMAIL", "ZAMMAD_ADMIN_PASSWORD", "MAILPIT_POP3_PASSWORD"):
        if not env.get(key):
            sys.exit(f"Set {key} in .env first (see .env.example).")
    url = env.get("ZAMMAD_URL", "http://localhost:8080")

    status = wait_for_zammad(Zammad(url))
    if status.get("setup_done"):
        print("Zammad is already set up; skipping the auto wizard.")
    else:
        run_wizard(env, Zammad(url))
        print("Zammad set up: admin user created, Mailpit email channel connected.")

    admin = Zammad(url, env["ZAMMAD_ADMIN_EMAIL"], env["ZAMMAD_ADMIN_PASSWORD"])
    for trigger in admin.request("GET", "/api/v1/triggers"):
        if trigger["name"].startswith("auto reply") and trigger["active"]:
            admin.request("PUT", f"/api/v1/triggers/{trigger['id']}", {"active": False})
            print(f"Deactivated trigger: {trigger['name']}")

    # Thread follow-up emails onto their ticket using the References and
    # In-Reply-To headers, not only a ticket number in the subject.
    for setting in admin.request("GET", "/api/v1/settings"):
        if setting["name"] == "postmaster_follow_up_search_in":
            value = {"value": ["references", "subject_references"]}
            if setting["state_current"] != value:
                admin.request("PUT", f"/api/v1/settings/{setting['id']}", {"state_current": value})
                print("Enabled follow-up detection by email references")

    if args.no_token:
        return
    if env.get("ZAMMAD_TOKEN"):
        print("ZAMMAD_TOKEN is already set; not creating another token.")
        return
    created = admin.request(
        "POST",
        "/api/v1/user_access_token",
        {"name": "service-desk-copilot", "permission": ["ticket.agent", "admin.object"]},
    )
    print("\nAdd this line to .env (it is shown only once):\n")
    print(f"ZAMMAD_TOKEN={created['token']}")


if __name__ == "__main__":
    main()
