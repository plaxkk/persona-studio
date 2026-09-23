"""Install pinned official runtime alongside Studio without replacing personal Codex."""

import hashlib
import json
import os
from pathlib import Path
import platform
import tarfile
import tempfile
import urllib.request

VERSION = "0.156.1"
ROOT = Path(__file__).resolve().parents[1]


def fetch(url):
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "PersonaStudio-Installer"}),
        timeout=90,
    ) as r:
        return r.read()


def main():
    arch = {"x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        platform.machine()
    )
    if not arch or platform.system() not in ("Darwin", "Linux"):
        raise SystemExit("Unsupported platform")
    target = arch + (
        "-apple-darwin" if platform.system() == "Darwin" else "-unknown-linux-musl"
    )
    release = json.loads(
        fetch(
            "https://api.github.com/repos/openai/codex/releases/tags/rust-v" + VERSION
        )
    )
    root = ROOT / ".local/codex-persona-runtime"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for program in ("codex", "codex-code-mode-host"):
        name = program + "-" + target
        asset = next(x for x in release["assets"] if x["name"] == name + ".tar.gz")
        archive = fetch(asset["browser_download_url"])
        if asset.get("digest") != "sha256:" + hashlib.sha256(archive).hexdigest():
            raise SystemExit("Official release checksum mismatch or unavailable")
        import io

        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            member = next(m for m in tar.getmembers() if m.name == name and m.isfile())
            data = tar.extractfile(member).read()
        path = root / name
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as f:
            f.write(data)
            temp = Path(f.name)
        temp.chmod(0o700)
        os.replace(temp, path)
        if program == "codex-code-mode-host":
            alias = root / program
            if alias.is_symlink():
                alias.unlink()
            if not alias.exists():
                alias.symlink_to(name)
    print(
        "Installed dedicated official Codex "
        + VERSION
        + ". Personal login/configuration unchanged."
    )


if __name__ == "__main__":
    main()
