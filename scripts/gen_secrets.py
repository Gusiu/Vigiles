"""Create .env with random secrets and render the Keycloak realm.

    python scripts/gen_secrets.py           # keeps an existing .env
    python scripts/gen_secrets.py --force   # new secrets (reset volumes afterwards)

Every value named change-me-... in .env.example is replaced by a random
one. infra/keycloak/realm.json is rendered from realm.template.json with
the client secrets and the demo user password from .env. Both files are
in .gitignore; only the templates are committed.
"""

import argparse
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"
TEMPLATE, REALM = ROOT / "infra/keycloak/realm.template.json", ROOT / "infra/keycloak/realm.json"


def generate_env() -> str:
    def replace(match: re.Match) -> str:
        return secrets.token_urlsafe(18)

    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#") and "change-me-" in line:
            key, _, value = line.partition("=")
            if key == "SERVICE_TOKENS":
                value = re.sub(r"change-me-[a-z0-9-]+", replace, value)
            else:
                value = secrets.token_urlsafe(18)
            line = f"{key}={value}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def read_env(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def render_realm(values: dict[str, str]) -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    missing = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)\}", template)) - set(values))
    if missing:
        raise SystemExit(f".env lacks {', '.join(missing)}; compare it with .env.example")
    REALM.write_text(re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: values[m[1]], template), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    args = parser.parse_args()
    if ENV.exists() and not args.force:
        print(".env exists, keeping it (use --force for new secrets)")
    else:
        ENV.write_text(generate_env(), encoding="utf-8")
        print("wrote .env with random secrets")
    values = read_env(ENV.read_text(encoding="utf-8"))
    render_realm(values)
    print(f"rendered {REALM.relative_to(ROOT)}; demo user password: DEMO_USER_PASSWORD in .env")


if __name__ == "__main__":
    main()
