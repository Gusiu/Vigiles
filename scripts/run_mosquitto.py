"""Run Mosquitto with the project configuration in local mode.

Same steps as infra/mosquitto/entrypoint.sh: password file from the
environment, ACL, rendered configuration under ``<state>/mosquitto``.
Used by the supervisor node ``mosquitto``.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERS = {"gw-01": "MQTT_GW01_PASSWORD", "hub": "MQTT_HUB_PASSWORD", "explorer": "MQTT_EXPLORER_PASSWORD"}


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_dotenv()
    state = Path(os.environ.get("MOSQUITTO_STATE_DIR", ROOT / ".local" / "mosquitto")).resolve()
    config, data = state / "config", state / "data"
    config.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    passwords = config / "passwords"
    passwords.write_text("")
    passwords.chmod(0o600)
    for user, variable in USERS.items():
        subprocess.run(["mosquitto_passwd", "-b", str(passwords), user, os.environ[variable]], check=True)
    shutil.copy(ROOT / "infra/mosquitto/acl", config / "acl")
    template = (ROOT / "infra/mosquitto/mosquitto.conf.template").read_text(encoding="utf-8")
    conf = template.replace("@CONFIG@", str(config)).replace("@DATA@", str(data))
    port = os.environ.get("MQTT_PORT", "1883")
    conf = conf.replace("listener 1883", f"listener {port} 127.0.0.1")
    if os.geteuid() == 0:
        conf += "\nuser root\n"  # local mode: keep file ownership simple
    (config / "mosquitto.conf").write_text(conf, encoding="utf-8")
    sys.stdout.flush()
    os.execvp("mosquitto", ["mosquitto", "-c", str(config / "mosquitto.conf")])


if __name__ == "__main__":
    main()
