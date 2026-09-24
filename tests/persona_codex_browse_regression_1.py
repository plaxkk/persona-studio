"""Opt-in real-model regression: ISSUE-002, browse approval (consumes quota).
Found by /qa on 2026-09-24.
Report: .gstack/qa-reports/qa-report-persona-studio-2026-09-24.md
Run: .venv/bin/python -m tests.persona_codex_browse_regression_1
Uses synthetic browser evidence; never touches X or production state.
"""
import asyncio
import tempfile
from pathlib import Path
from studio.codex_persona import CodexPersonaRunner, preflight
from studio.store import Store

async def main():
    check = preflight()
    assert check['ready']
    with tempfile.TemporaryDirectory() as temp:
        runner = CodexPersonaRunner(Store(Path(temp)))
        imports = runner.imports
        imports.presence('1.3.0')
        ident = imports.create('example', 20, check['model'], check['reasoning'])
        with runner.store.db() as c:
            c.execute("UPDATE persona_imports SET status='collecting' WHERE id=?", (ident,))
        async def browser():
            while True:
                imports.presence('1.3.0')
                action = imports.next_action()
                if action:
                    imports.receive(action['id'], ident, 0, {
                        'state':'ready', 'account':'example',
                        'snapshot':'Synthetic profile and 20 own records. No real account.',
                        'records':[{'id':str(90071992547409930+n), 'author':'example', 'kind':'post' if n%2 else 'reply', 'text':f'Synthetic sample {n}'} for n in range(20)]
                    })
                await asyncio.sleep(.1)
        bridge = asyncio.create_task(browser())
        try:
            result = await asyncio.wait_for(runner.execute(imports.get(ident),
                'Synthetic test. First call persona browse with action snapshot. Then progress and finish. Return finished=true only after finish accepts.',
                {'type':'object','properties':{'finished':{'type':'boolean'}},'required':['finished'],'additionalProperties':False}, True), 120)
            current = imports.get(ident)
            assert result['finished'] and current['phase'] == 'analyze'
            assert current['actions'] >= 1 and current['counts']['usable'] == 20
            print('PASS real Codex browse -> synthetic extension evidence -> verified finish')
        finally:
            bridge.cancel()
            await asyncio.gather(bridge, return_exceptions=True)

if __name__ == '__main__':
    asyncio.run(main())
