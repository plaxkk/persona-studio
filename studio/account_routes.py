"""账号观察（06C）：读一个账号的样本 → 算特征 → 打三个分 → 打风险硬标签。

Every route takes its input from the path, never a query string: the connector
refuses query strings outside a short allowlist (`browser-extension/desktop.js`),
so a `?handle=` route would 403 through the cloud panel.

There is no "add the account first" step here, unlike 对标账号: an account is
whatever you ran into, so any well-formed handle is readable in one click and a
detail route answers for a handle that has never been read (`run: null`) instead
of 404 — the panel needs the *plan* to be able to offer the read in the first
place.

`watchroom` is the one part of 06C that is a **ledger rather than a view**: 方案 §六
asks for 50 accounts a week looked at by hand so the flags can be measured for
precision, and a verdict is what turns a flag into that measurement. It is
reached by hand (`/watch`, for an account nobody has read yet) as well as
automatically (a hit adds the account).
"""
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from . import account_flags, account_quality

HANDLE = re.compile(r'[A-Za-z0-9_]{1,15}')


class Watch(BaseModel):
    note: str = Field(default='', max_length=200)


class Verdict(BaseModel):
    # 方案 §三 第 4 步的标签要说「像不像」，而像不像只有人看得出来——所以
    # 复核结论是三个词，不是一个分数。
    verdict: str = Field(min_length=1, max_length=16)
    note: str = Field(default='', max_length=200)
    action: str = Field(default='', max_length=200)


def register(app, store, jobs, session):
    def known(handle: str) -> str:
        try:
            return account_quality.clean_handle(handle)
        except account_quality.AccountQualityError:
            raise HTTPException(422, '用户名格式不正确。')

    def thresholds() -> dict:
        return {'days': account_quality.SAMPLE_WINDOW_DAYS,
                'limit': account_quality.SAMPLE_LIMIT,
                'min_post_sample': account_quality.MIN_POST_SAMPLE,
                'min_reply_sample': account_quality.MIN_REPLY_SAMPLE,
                'reads': {kind: account_quality.READ_LABEL[kind]
                          for kind in account_quality.READ_SET}}

    @app.get('/api/v1/accounts/quality', dependencies=[Depends(session)])
    def listing():
        return {'accounts': account_quality.accounts(store),
                'thresholds': thresholds()}

    @app.get('/api/v1/accounts/quality/{handle}', dependencies=[Depends(session)])
    def one(handle: str):
        clean = known(handle)
        run = account_quality.latest(store, clean)
        since, until = account_quality.window(store)
        return {'handle': clean,
                'window': {'days': account_quality.SAMPLE_WINDOW_DAYS,
                           'from': since, 'to': until},
                'plan': account_quality.plan(store, clean),
                'samples': account_quality.sample_counts(store, clean),
                # A read's own state, so the panel can say which of the four
                # reads answered and which one is the reason the gaps exist.
                'reads': (account_quality.read_states(store, run['task_id'])
                          if run and run.get('task_id') else {}),
                # The 06C-2 three scores and their ledger. Lifted to the top level
                # as well as riding inside `run`, because "no run yet" and "a run
                # whose scores could not be computed" are different answers.
                'score': (run or {}).get('score'),
                # The 06C-3 risk flags, same reason for the same lift.
                'flags': (run or {}).get('flags'),
                'run': run,
                'thresholds': thresholds()}

    @app.post('/api/v1/accounts/quality/read/{handle}',
              dependencies=[Depends(session)])
    def read(handle: str):
        clean = known(handle)
        # One read per account at a time; a second click joins the first.
        task = jobs.enqueue('account_quality', {'handle': clean},
                            dedupe='account_quality:' + clean)
        return {'task_id': task, 'handle': clean,
                'plan': account_quality.plan(store, clean)}

    @app.get('/api/v1/accounts/watchroom', dependencies=[Depends(session)])
    def watchroom():
        # 观察室自己那组线是标签层的（`thresholds`），采集层的那组另起一个名字，
        # 否则两边同名会互相盖掉，面板就会拿采集的线去解释标签。
        return dict(account_flags.watchroom(store),
                    sample_thresholds=thresholds())

    @app.post('/api/v1/accounts/watchroom/{handle}/watch',
              dependencies=[Depends(session)])
    def watch(handle: str, payload: Watch):
        clean = known(handle)
        return account_flags.watch(store, clean, note=payload.note)

    @app.delete('/api/v1/accounts/watchroom/{handle}',
                dependencies=[Depends(session)])
    def forget(handle: str):
        clean = known(handle)
        if not account_flags.forget(store, clean):
            raise HTTPException(404, '这个账号不在观察室里。')
        return {'handle': clean, 'removed': True}

    @app.post('/api/v1/accounts/watchroom/{handle}/review',
              dependencies=[Depends(session)])
    def review(handle: str, payload: Verdict):
        clean = known(handle)
        try:
            return account_flags.review(store, clean, verdict=payload.verdict,
                                        note=payload.note, action=payload.action)
        except account_flags.AccountFlagsError as error:
            raise HTTPException(422, str(error))
