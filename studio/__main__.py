import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="人格工作室 — 半自动 X 内容工作台")
    parser.add_argument("service", choices=["api", "worker"], default="api", nargs="?")
    parser.add_argument(
        "--state-dir",
        default=os.environ.get(
            "STUDIO_STATE_DIR",
            str(Path(__file__).resolve().parents[1] / ".local/state"),
        ),
    )
    parser.add_argument("--host", default=os.environ.get("STUDIO_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("STUDIO_PORT", "18880"))
    )
    args = parser.parse_args()
    os.umask(0o077)
    os.environ["STUDIO_STATE_DIR"] = str(Path(args.state_dir).resolve())
    os.environ["STUDIO_PORT"] = str(args.port)
    if args.service == "worker":
        from .worker import main as worker

        worker()
    else:
        if args.host not in ["127.0.0.1", "localhost", "::1"] and not os.environ.get(
            "STUDIO_PUBLIC_URL", ""
        ).startswith("https://"):
            parser.error("远程监听必须配置 HTTPS STUDIO_PUBLIC_URL")
        import uvicorn
        from .api import create_app

        uvicorn.run(
            create_app(),
            host=args.host,
            port=args.port,
            access_log=False,
            proxy_headers=False,
        )


if __name__ == "__main__":
    main()
