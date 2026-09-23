"""Minimal Hermes subprocess. Social credentials never enter this environment."""

import contextlib, json, logging, os, sys


def main():
    os.umask(0o077)
    payload = json.load(sys.stdin)
    logging.disable(logging.CRITICAL)
    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            model=os.environ["MODEL_ID"],
            provider="custom",
            base_url=os.environ["MODEL_BASE_URL"],
            api_key=os.environ["MODEL_API_KEY"],
            enabled_toolsets=[],
            max_iterations=2,
            max_tokens=2048,
            quiet_mode=True,
            save_trajectories=False,
            skip_context_files=True,
            skip_memory=True,
        )
        if agent.tools:
            raise RuntimeError("unexpected_tools")
        result = agent.run_conversation(payload["prompt"])
    if result.get("failed") or not result.get("final_response"):
        raise RuntimeError("model_failed")
    print(
        json.dumps(
            {
                "text": result["final_response"],
                "usage": {
                    k: result[k]
                    for k in ["input_tokens", "output_tokens", "total_tokens"]
                    if isinstance(result.get(k), (int, float))
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"error":"engine_failed"}')
        sys.exit(1)
