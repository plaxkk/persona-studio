"""Install two native services; retire only legacy services belonging to this checkout."""

import argparse, json, os, plistlib, shutil, sqlite3, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument("--state-dir", default=str(ROOT / ".local/state"))
parser.add_argument("--port", type=int, default=18880)
parser.add_argument("--public-url", default="")
parser.add_argument("--systemd-user", action="store_true")
args = parser.parse_args()
os.umask(0o077)
state = Path(args.state_dir).resolve()
state.mkdir(parents=True, exist_ok=True, mode=0o700)
backup = state / "backups" / ("services-" + str(time.time_ns()))
backup.mkdir(parents=True, mode=0o700)
# Back up configuration before stopping legacy writers. SQLite backup below runs after stop.
for filename in [".env", "factory.json", "openclaw.json"]:
    path = state / filename
    if path.exists():
        shutil.copyfile(path, backup / filename)
        (backup / filename).chmod(0o600)
python = ROOT / ".venv/bin/python"
assert python.is_file(), "Run Python environment setup first"
logs = state / "logs"
logs.mkdir(mode=0o700, exist_ok=True)
env = {
    "PATH": str(Path.home() / ".local/bin")
    + ":/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
    "STUDIO_STATE_DIR": str(state),
    "STUDIO_PORT": str(args.port),
    "PYTHONUTF8": "1",
}
if args.public_url:
    env["STUDIO_PUBLIC_URL"] = args.public_url
if sys.platform == "darwin":
    folder = Path.home() / "Library/LaunchAgents"
    folder.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for label in [
        "local.x-persona.admin",
        "local.x-persona.hermes",
        "local.x-persona.shadow",
        "local.persona-studio.api",
        "local.persona-studio.worker",
    ]:
        target = folder / (label + ".plist")
        if target.exists():
            if str(ROOT) not in target.read_text():
                raise SystemExit("Refusing unrelated service " + label)
            subprocess.run(
                ["launchctl", "bootout", domain + "/" + label], capture_output=True
            )
            shutil.move(str(target), backup / target.name)
    from studio.store import Store

    store = Store(state)
    store.import_legacy()
    store.set("paused", True)
    store.backup()
    for service in ["api", "worker"]:
        label = "local.persona-studio." + service
        target = folder / (label + ".plist")
        value = {
            "Label": label,
            "ProgramArguments": [str(python), "-m", "studio", service],
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "Umask": 63,
            "StandardOutPath": str(logs / (service + ".log")),
            "StandardErrorPath": str(logs / (service + ".error.log")),
        }
        with target.open("wb") as f:
            plistlib.dump(value, f)
        target.chmod(0o600)
        for attempt in range(10):
            r = subprocess.run(
                ["launchctl", "bootstrap", domain, str(target)], capture_output=True
            )
            if r.returncode == 0:
                break
            time.sleep(1)
        else:
            raise SystemExit("Unable to start " + label)
        print(label + ": installed")
elif sys.platform.startswith("linux") and args.systemd_user:
    folder = Path.home() / ".config/systemd/user"
    folder.mkdir(parents=True, exist_ok=True)
    # Existing system-wide or differently named legacy units require explicit review before migration.
    for label in [
        "x-persona-admin",
        "x-persona-hermes",
        "x-persona-shadow",
        "persona-studio-api",
        "persona-studio-worker",
    ]:
        old = folder / (label + ".service")
        if old.exists():
            if str(ROOT) not in old.read_text():
                raise SystemExit("Refusing unrelated service " + label)
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", label], capture_output=True
            )
            shutil.move(str(old), backup / old.name)
    from studio.store import Store

    store = Store(state)
    store.import_legacy()
    store.set("paused", True)
    store.backup()
    for service in ["api", "worker"]:
        environment = "\n".join(
            "Environment=" + json.dumps(k + "=" + v) for k, v in env.items()
        )
        value = f"[Unit]\nDescription=Persona Studio {service}\nAfter=network-online.target\n\n[Service]\nWorkingDirectory={json.dumps(str(ROOT))}\nExecStart={json.dumps(str(python))} -m studio {service}\n{environment}\nRestart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n"
        (folder / f"persona-studio-{service}.service").write_text(value)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        [
            "systemctl",
            "--user",
            "enable",
            "--now",
            "persona-studio-api",
            "persona-studio-worker",
        ],
        check=True,
    )
else:
    raise SystemExit("Use macOS launchd or Linux --systemd-user")
print("Migration completed; sync and generation remain paused.")
