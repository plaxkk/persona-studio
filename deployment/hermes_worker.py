"""Private stdin/stdout boundary to the installed Hermes runtime; no account tools."""
import contextlib
import json
import logging
import os
from pathlib import Path
import sys


def main():
    os.umask(0o077)
    payload = json.load(sys.stdin)
    logging.disable(logging.CRITICAL)
    # Hermes dependencies may print during import; discard this diagnostic stream.
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        from run_agent import AIAgent
        persona = Path(os.environ['PERSONA_PATH'])
        rules = ('You are a clearly labeled synthetic persona. This deployment only permits text generation. '
                 'You have no tools. Never claim you sent posts, changed accounts, or executed commands.\n\n') + '\n\n'.join((persona / name).read_text() for name in
                             ('SKILL.md', 'voice.md', 'social.md', 'crisis_support.md'))
        agent = AIAgent(model=os.environ['MODEL_ID'], provider='custom',
                        base_url=os.environ['MODEL_BASE_URL'], api_key=os.environ['MODEL_API_KEY'],
                        enabled_toolsets=[], max_iterations=2, quiet_mode=True,
                        save_trajectories=False, skip_context_files=True, skip_memory=True,
                        ephemeral_system_prompt=rules)
        if agent.tools:
            raise RuntimeError('Unexpected enabled tools')
        result = agent.run_conversation(payload['text'], conversation_history=payload.get('history', []))
    if result.get('failed') or not result.get('final_response'):
        raise RuntimeError('Model response unavailable')
    print(json.dumps({'reply': result['final_response']}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not forward provider errors: URLs or exception messages can contain secrets.
        print(json.dumps({'error': type(exc).__name__}))
        sys.exit(1)
