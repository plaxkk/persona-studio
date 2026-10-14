from __future__ import annotations
import json
import os
import re
import sqlite3
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DEFAULTS = dict(
    brand="人格工作室",
    paused=True,
    engine="hermes",
    sync_interval=1800,
    timezone="Asia/Shanghai",
    owner_id="",
    telegram_enabled=False,
    x_username="",
    x_status="not_connected",
    x_verified_at=0,
    last_sync=0,
    last_complete_sync=0,
    last_sync_requested=0,
    auto_replies=12,
    auto_posts=2,
    per_round=3,
    worker_heartbeat=0,
    telegram_status="not_connected",
    supabase_url="",
    analytics_cloud_last_status="not_configured",
    analytics_cloud_checked_at=0,
    # 06D：盯哪个上游、要不要自动盯。默认盯 X 官方开源的那份排序算法，默认开着——
    # 所以这个开关在页面上看得见、也关得掉。
    algorithm_repo="xai-org/x-algorithm",
    algorithm_watch=True,
)
PERSONA = dict(
    name="未命名的角色",
    identity="我是一个明确标注的虚构 AI 人格。",
    voice="自然、简洁、有自己的观点，不模仿真人身份。",
    interests="",
    boundaries="",
    version=1,
)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / "studio.sqlite3"
        self.migrate()

    @contextmanager
    def db(self, immediate=False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        try:
            if immediate:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def migrate(self):
        with self.db() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS persona_versions(version INTEGER PRIMARY KEY,body TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS engines(id TEXT PRIMARY KEY,config TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'not_configured',checked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY,status TEXT NOT NULL,last_success INTEGER NOT NULL DEFAULT 0,last_attempt INTEGER NOT NULL DEFAULT 0,count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,author TEXT NOT NULL,text TEXT NOT NULL,url TEXT NOT NULL,kind TEXT NOT NULL,context TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'new',reason TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,seen INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL,post_id TEXT REFERENCES posts(id),status TEXT NOT NULL DEFAULT 'draft',version INTEGER NOT NULL DEFAULT 1,edited INTEGER NOT NULL DEFAULT 0,persona_version INTEGER NOT NULL,engine TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,updated INTEGER NOT NULL,result_url TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS draft_versions(draft_id TEXT NOT NULL,version INTEGER NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(draft_id,version));
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,automatic INTEGER NOT NULL DEFAULT 0,dedupe TEXT,attempts INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,updated INTEGER NOT NULL,available INTEGER NOT NULL,result TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '',cancel INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS active_dedupe ON tasks(dedupe) WHERE status IN ('queued','running') AND dedupe IS NOT NULL;
            CREATE TABLE IF NOT EXISTS generation_ledger(task_id TEXT PRIMARY KEY,day TEXT NOT NULL,kind TEXT NOT NULL,automatic INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,label TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '{}',created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel TEXT NOT NULL,role TEXT NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_summaries(channel TEXT PRIMARY KEY,through_id INTEGER NOT NULL,text TEXT NOT NULL,updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT,category TEXT NOT NULL,text TEXT NOT NULL,source TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(text,content='memories',content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memories BEGIN INSERT INTO memory_fts(rowid,text) VALUES(new.id,new.text); END;
            CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS telegram_updates(id INTEGER PRIMARY KEY,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS notices(draft_id TEXT PRIMARY KEY,status TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS creations(id TEXT PRIMARY KEY,idea TEXT NOT NULL,candidate TEXT NOT NULL DEFAULT '',stage TEXT NOT NULL DEFAULT 'idea',version INTEGER NOT NULL DEFAULT 1,task_id TEXT,draft_id TEXT REFERENCES drafts(id),created INTEGER NOT NULL,updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS persona_browser_presence(id INTEGER PRIMARY KEY CHECK(id=1),version TEXT NOT NULL,seen INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS browser_reads(id INTEGER PRIMARY KEY,task_id TEXT NOT NULL,source TEXT NOT NULL,account TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',updated INTEGER NOT NULL,UNIQUE(task_id,source));
            """)
            for k, v in DEFAULTS.items():
                c.execute(
                    "INSERT OR IGNORE INTO settings VALUES(?,?)", (k, json.dumps(v))
                )
            c.execute(
                "INSERT OR IGNORE INTO settings VALUES(?,?)",
                ("persona", json.dumps(PERSONA, ensure_ascii=False)),
            )
            c.execute(
                "INSERT OR IGNORE INTO persona_versions VALUES(1,?,?)",
                (json.dumps(PERSONA, ensure_ascii=False), int(time.time())),
            )
            for engine in ["hermes", "openclaw", "codex"]:
                c.execute(
                    "INSERT OR IGNORE INTO engines(id,config) VALUES(?,?)",
                    (engine, json.dumps({"model": "", "base_url": ""})),
                )
            c.execute(
                "INSERT OR IGNORE INTO migrations VALUES(1,?)", (int(time.time()),)
            )
            c.execute(
                "INSERT OR IGNORE INTO migrations VALUES(2,?)", (int(time.time()),)
            )
            if not c.execute("SELECT 1 FROM migrations WHERE version=3").fetchone():
                c.executescript("""
                CREATE TABLE IF NOT EXISTS interaction_context_reads(
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, post_id TEXT NOT NULL,
                    author TEXT NOT NULL, account TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL,
                    UNIQUE(task_id,post_id));
                CREATE TABLE IF NOT EXISTS interaction_reviews(
                    post_id TEXT PRIMARY KEY REFERENCES posts(id), context_status TEXT NOT NULL DEFAULT 'pending',
                    context_issue TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT 'other',
                    scene TEXT NOT NULL DEFAULT 'discussion', score TEXT NOT NULL DEFAULT '{}',
                    score_total INTEGER, contribution TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT '',
                    decision_reason TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS interaction_skills(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    level TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
                    confirmed INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL,
                    updated INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS interaction_posts_page ON posts(kind,status,created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS interaction_posts_author ON posts(author,created DESC);
                """)
                c.execute("INSERT INTO migrations VALUES(3,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=4").fetchone():
                c.executescript("""
                CREATE TABLE IF NOT EXISTS draft_publications(
                    draft_id TEXT PRIMARY KEY REFERENCES drafts(id),
                    tweet_id TEXT, url TEXT, author TEXT,
                    user_confirmed INTEGER NOT NULL DEFAULT 0,
                    user_confirmed_at INTEGER,
                    platform_observed_at INTEGER,
                    published_at INTEGER,
                    observation_source TEXT,
                    publication_kind TEXT NOT NULL DEFAULT 'post'
                );
                CREATE INDEX IF NOT EXISTS publication_tweet ON draft_publications(tweet_id);
                CREATE TABLE IF NOT EXISTS post_metric_snapshots(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tweet_id TEXT NOT NULL, observed_at INTEGER NOT NULL,
                    published_at INTEGER, age_seconds INTEGER,
                    target_window TEXT, window_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    views INTEGER, likes INTEGER, replies INTEGER,
                    reposts INTEGER, quotes INTEGER, bookmarks INTEGER,
                    UNIQUE(tweet_id,observed_at,source)
                );
                CREATE INDEX IF NOT EXISTS metric_tweet_time ON post_metric_snapshots(tweet_id,observed_at DESC);
                CREATE TABLE IF NOT EXISTS profile_snapshots(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT NOT NULL, observed_at INTEGER NOT NULL,
                    day TEXT NOT NULL, followers INTEGER,
                    bio TEXT, pinned_tweet_id TEXT, source TEXT NOT NULL,
                    UNIQUE(account,day,source)
                );
                CREATE TABLE IF NOT EXISTS metric_reads(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL UNIQUE,
                    tweet_id TEXT NOT NULL, author TEXT NOT NULL,
                    account TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_reads(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL UNIQUE,
                    account TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS growth_goals(
                    id INTEGER PRIMARY KEY CHECK(id=1), target_followers INTEGER NOT NULL,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                """)
                columns = {r['name'] for r in c.execute("PRAGMA table_info(draft_versions)")}
                if 'reason' not in columns:
                    c.execute("ALTER TABLE draft_versions ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
                for row in c.execute("SELECT id,kind,result_url FROM drafts WHERE status='confirmed'").fetchall():
                    url = row['result_url'] or None
                    match = re.fullmatch(r'https://x\.com/([A-Za-z0-9_]{1,15})/status/([0-9]{1,30})', url or '')
                    c.execute("INSERT OR IGNORE INTO draft_publications(draft_id,tweet_id,url,author,user_confirmed,publication_kind) VALUES(?,?,?,?,1,?)",
                              (row['id'], match[2] if match else None, url, match[1] if match else None, 'reply' if row['kind'] == 'reply' else 'post'))
                c.execute("INSERT INTO migrations VALUES(4,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=5").fetchone():
                c.executescript("""
                CREATE TABLE IF NOT EXISTS analytics_runs(
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, source TEXT NOT NULL,
                    window_from TEXT NOT NULL, window_to TEXT NOT NULL,
                    observed_at INTEGER NOT NULL, request_limit INTEGER NOT NULL,
                    received_count INTEGER NOT NULL, parsed_count INTEGER NOT NULL,
                    gap_count INTEGER NOT NULL, at_request_limit INTEGER NOT NULL,
                    parser_version INTEGER NOT NULL, raw_json TEXT NOT NULL,
                    cloud_status TEXT NOT NULL DEFAULT 'pending',
                    cloud_attempts INTEGER NOT NULL DEFAULT 0,
                    cloud_retry_at INTEGER NOT NULL DEFAULT 0,
                    cloud_error TEXT NOT NULL DEFAULT '', cloud_synced_at INTEGER);
                CREATE INDEX IF NOT EXISTS analytics_runs_pending
                    ON analytics_runs(cloud_status,cloud_retry_at,observed_at);
                CREATE TABLE IF NOT EXISTS analytics_post_metrics(
                    run_id TEXT NOT NULL REFERENCES analytics_runs(id),
                    post_id TEXT NOT NULL, published_at_ms INTEGER NOT NULL,
                    text TEXT NOT NULL, metrics_json TEXT NOT NULL,
                    PRIMARY KEY(run_id,post_id));
                CREATE INDEX IF NOT EXISTS analytics_post_history
                    ON analytics_post_metrics(post_id,run_id);
                CREATE TABLE IF NOT EXISTS analytics_coverage_gaps(
                    run_id TEXT NOT NULL REFERENCES analytics_runs(id),
                    item_index INTEGER NOT NULL, post_id TEXT,
                    reason TEXT NOT NULL, PRIMARY KEY(run_id,item_index));
                """)
                c.execute("INSERT INTO migrations VALUES(5,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=6").fetchone():
                c.executescript("""
                CREATE TABLE IF NOT EXISTS analytics_browser_reads(
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                    account TEXT NOT NULL, days INTEGER NOT NULL,
                    status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                    updated INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS analytics_browser_queue
                    ON analytics_browser_reads(status,updated);
                """)
                c.execute("INSERT INTO migrations VALUES(6,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=7").fetchone():
                c.execute("ALTER TABLE analytics_browser_reads ADD COLUMN from_date TEXT")
                c.execute("ALTER TABLE analytics_browser_reads ADD COLUMN to_date TEXT")
                c.execute("INSERT INTO migrations VALUES(7,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=8").fetchone():
                c.execute("ALTER TABLE analytics_browser_reads ADD COLUMN kind TEXT NOT NULL DEFAULT 'content'")
                c.executescript("""
                CREATE TABLE IF NOT EXISTS analytics_overview_runs(
                    id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    window_from TEXT NOT NULL, window_to TEXT NOT NULL,
                    previous_from TEXT NOT NULL, previous_to TEXT NOT NULL,
                    observed_at INTEGER NOT NULL, point_count INTEGER NOT NULL,
                    gap_count INTEGER NOT NULL, parser_version INTEGER NOT NULL,
                    scalars_json TEXT NOT NULL, raw_json TEXT NOT NULL,
                    cloud_status TEXT NOT NULL DEFAULT 'pending',
                    cloud_attempts INTEGER NOT NULL DEFAULT 0,
                    cloud_retry_at INTEGER NOT NULL DEFAULT 0,
                    cloud_error TEXT NOT NULL DEFAULT '', cloud_synced_at INTEGER);
                CREATE INDEX IF NOT EXISTS analytics_overview_pending
                    ON analytics_overview_runs(cloud_status,cloud_retry_at,observed_at);
                CREATE TABLE IF NOT EXISTS analytics_overview_points(
                    run_id TEXT NOT NULL REFERENCES analytics_overview_runs(id),
                    series TEXT NOT NULL, item_index INTEGER NOT NULL,
                    metric_index INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
                    metric_type TEXT NOT NULL, count REAL, verified INTEGER,
                    PRIMARY KEY(run_id,series,item_index,metric_index));
                CREATE TABLE IF NOT EXISTS analytics_overview_gaps(
                    run_id TEXT NOT NULL REFERENCES analytics_overview_runs(id),
                    series TEXT NOT NULL, item_index INTEGER NOT NULL,
                    metric_index INTEGER NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(run_id,series,item_index,metric_index,reason));
                """)
                c.execute("INSERT INTO migrations VALUES(8,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=9").fetchone():
                c.executescript("""
                CREATE TABLE IF NOT EXISTS creation_media(
                    id TEXT PRIMARY KEY,
                    creation_id TEXT NOT NULL REFERENCES creations(id),
                    draft_id TEXT REFERENCES drafts(id),
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    mime TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS creation_media_owner
                    ON creation_media(creation_id,created);
                """)
                c.execute("INSERT INTO migrations VALUES(9,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=10").fetchone():
                # `kind` separates replies from everything else. It is nullable:
                # rows staged before this migration were never classified, and a
                # NULL is honest about that where 'post' would be a claim.
                c.execute("ALTER TABLE analytics_post_metrics ADD COLUMN kind TEXT")
                c.executescript("""
                CREATE TABLE IF NOT EXISTS weekly_reports(
                    id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    week_start TEXT NOT NULL, week_end TEXT NOT NULL,
                    timezone TEXT NOT NULL, from_ms INTEGER NOT NULL,
                    to_ms INTEGER NOT NULL, generated_at INTEGER NOT NULL,
                    data_quality TEXT NOT NULL, facts_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    cloud_status TEXT NOT NULL DEFAULT 'pending',
                    cloud_attempts INTEGER NOT NULL DEFAULT 0,
                    cloud_retry_at INTEGER NOT NULL DEFAULT 0,
                    cloud_error TEXT NOT NULL DEFAULT '', cloud_synced_at INTEGER,
                    UNIQUE(account,week_start));
                CREATE INDEX IF NOT EXISTS weekly_report_pending
                    ON weekly_reports(cloud_status,cloud_retry_at,generated_at);
                CREATE TABLE IF NOT EXISTS weekly_report_issues(
                    report_id TEXT NOT NULL REFERENCES weekly_reports(id),
                    ordinal INTEGER NOT NULL, code TEXT NOT NULL,
                    title TEXT NOT NULL, evidence TEXT NOT NULL,
                    benchmark TEXT NOT NULL DEFAULT '',
                    hypothesis TEXT NOT NULL DEFAULT '',
                    alternative TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL,
                    PRIMARY KEY(report_id,ordinal));
                -- The 14-day experiment ledger lands with the report that has to
                -- read it ("上周尝试的结果"), so an empty table can say 暂无数据
                -- instead of the report pretending the question does not exist.
                -- The five cloud_* columns are in place for the Supabase mirror;
                -- no pusher writes them yet.
                CREATE TABLE IF NOT EXISTS experiments(
                    id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    hypothesis TEXT NOT NULL, metric TEXT NOT NULL,
                    baseline_json TEXT NOT NULL DEFAULT '{}',
                    target_json TEXT NOT NULL DEFAULT '{}',
                    started_at INTEGER NOT NULL, planned_end_at INTEGER NOT NULL,
                    ended_at INTEGER, status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    decision TEXT NOT NULL DEFAULT '',
                    created INTEGER NOT NULL, updated INTEGER NOT NULL,
                    cloud_status TEXT NOT NULL DEFAULT 'pending',
                    cloud_attempts INTEGER NOT NULL DEFAULT 0,
                    cloud_retry_at INTEGER NOT NULL DEFAULT 0,
                    cloud_error TEXT NOT NULL DEFAULT '', cloud_synced_at INTEGER);
                CREATE TABLE IF NOT EXISTS experiment_observations(
                    experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    day TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
                    known INTEGER NOT NULL, source TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    PRIMARY KEY(experiment_id,day,metric,source));
                """)
                c.execute("INSERT INTO migrations VALUES(10,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=11").fetchone():
                # Benchmark posts (06B). `metrics_json` holds only the counts X
                # actually published; a withheld count is listed in
                # `engagement_json.unknown` and never written as 0. `posted_at`
                # is nullable for the same reason, and `coverage_gap` records
                # what the read did not reach instead of implying completeness.
                c.executescript("""
                CREATE TABLE IF NOT EXISTS benchmark_accounts(
                    handle TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    added_at INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS benchmark_posts(
                    post_id TEXT PRIMARY KEY, handle TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    text_source TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '', posted_at INTEGER,
                    kind TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    engagement_json TEXT NOT NULL DEFAULT '{}',
                    is_viral INTEGER NOT NULL DEFAULT 0,
                    captured_at INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    coverage_gap TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS benchmark_posts_handle
                    ON benchmark_posts(handle,posted_at);
                CREATE TABLE IF NOT EXISTS viral_deconstructions(
                    id TEXT PRIMARY KEY,
                    post_id TEXT NOT NULL REFERENCES benchmark_posts(post_id),
                    handle TEXT NOT NULL, thesis TEXT NOT NULL DEFAULT '',
                    hook TEXT NOT NULL DEFAULT '',
                    structure_json TEXT NOT NULL DEFAULT '{}',
                    evidence TEXT NOT NULL DEFAULT '',
                    design_doc TEXT NOT NULL DEFAULT '',
                    engine TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS search_reads(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL UNIQUE,
                    handle TEXT NOT NULL, account TEXT NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL
                );
                """)
                # §4.7.1 names these two accounts to watch, so they are seeded
                # rather than left for the user to re-type. They start active
                # but unread: no post row exists until a real search runs.
                seeded = int(time.time())
                for handle, label, note in (
                    ("lifesinger", "Frank Wang 玉伯", "产品需求 §4.7.1 手动录入"),
                    ("chunxiangai", "赵纯想", "产品需求 §4.7.1 手动录入"),
                ):
                    c.execute(
                        "INSERT OR IGNORE INTO benchmark_accounts"
                        "(handle,label,note,added_at,active) VALUES(?,?,?,?,1)",
                        (handle, label, note, seeded),
                    )
                c.execute("INSERT INTO migrations VALUES(11,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=12").fetchone():
                # 账号质量（06C）。五张表照《账号质量打标程序化方案》的流水线
                # 分层：样本 → 特征 → 特征值 → 分数 → 硬标签，再加一张观察台名单。
                # 06C-1 只写前两张；后三张现在就建，是为了让"还没算分"表现为一行
                # 空的 `account_scores`（可渲染成暂无数据），而不是让面板去猜这张表
                # 该不该存在——这与 v10 里 experiments 随周报一起落地是同一条理由。
                #
                # `value REAL` 可空、旁边配 `known`：读不到的观测量必须留空并写清
                # 为什么，写 0 会让"没有这种情况"和"我们看不到"变成一个数。
                c.executescript("""
                CREATE TABLE IF NOT EXISTS account_samples(
                    handle TEXT NOT NULL,
                    sample_kind TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    text_source TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    posted_at INTEGER,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    engagement_json TEXT NOT NULL DEFAULT '{}',
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    captured_at INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    coverage_gap TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(handle,sample_kind,sample_id));
                CREATE INDEX IF NOT EXISTS account_samples_kind
                    ON account_samples(handle,sample_kind,posted_at);
                CREATE TABLE IF NOT EXISTS account_features(
                    id TEXT PRIMARY KEY, handle TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    window_days INTEGER NOT NULL,
                    from_date TEXT NOT NULL, to_date TEXT NOT NULL,
                    task_id TEXT NOT NULL DEFAULT '',
                    sample_counts_json TEXT NOT NULL DEFAULT '{}',
                    coverage_gap TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL,
                    created INTEGER NOT NULL,
                    cloud_status TEXT NOT NULL DEFAULT 'pending',
                    cloud_attempts INTEGER NOT NULL DEFAULT 0,
                    cloud_retry_at INTEGER NOT NULL DEFAULT 0,
                    cloud_error TEXT NOT NULL DEFAULT '', cloud_synced_at INTEGER);
                CREATE INDEX IF NOT EXISTS account_features_handle
                    ON account_features(handle,observed_at);
                CREATE TABLE IF NOT EXISTS account_feature_values(
                    feature_id TEXT NOT NULL REFERENCES account_features(id),
                    name TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
                    layer TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '',
                    value REAL, known INTEGER NOT NULL,
                    note TEXT NOT NULL DEFAULT '', sample_size INTEGER,
                    PRIMARY KEY(feature_id,name));
                CREATE TABLE IF NOT EXISTS account_scores(
                    feature_id TEXT PRIMARY KEY REFERENCES account_features(id),
                    handle TEXT NOT NULL,
                    authenticity REAL, professionalism REAL, monetization REAL,
                    confidence TEXT NOT NULL,
                    rule_version TEXT NOT NULL DEFAULT '',
                    model_version TEXT NOT NULL DEFAULT '',
                    labels_json TEXT NOT NULL DEFAULT '{}',
                    created INTEGER NOT NULL, updated INTEGER NOT NULL,
                    cloud_status TEXT NOT NULL DEFAULT 'pending',
                    cloud_attempts INTEGER NOT NULL DEFAULT 0,
                    cloud_retry_at INTEGER NOT NULL DEFAULT 0,
                    cloud_error TEXT NOT NULL DEFAULT '', cloud_synced_at INTEGER);
                CREATE INDEX IF NOT EXISTS account_scores_pending
                    ON account_scores(cloud_status,cloud_retry_at);
                CREATE TABLE IF NOT EXISTS account_flags(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feature_id TEXT NOT NULL REFERENCES account_features(id),
                    handle TEXT NOT NULL, code TEXT NOT NULL,
                    layer TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT '',
                    rule_version TEXT NOT NULL DEFAULT '',
                    created INTEGER NOT NULL, UNIQUE(feature_id,code));
                CREATE TABLE IF NOT EXISTS account_watchlist(
                    handle TEXT PRIMARY KEY, added_at INTEGER NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 0, last_hit INTEGER,
                    hits_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'watching',
                    note TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '',
                    updated INTEGER NOT NULL);
                -- 一次账号观察要读主页 + 三个搜索，而 search_reads.task_id 是
                -- UNIQUE 的，一个任务只能挂一次搜索。队列单独一张表，用
                -- (task_id,kind) 做键：同一次观察里的四次读取互不挤占，
                -- 又不会重复排同一种读取。
                CREATE TABLE IF NOT EXISTS account_reads(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL, kind TEXT NOT NULL,
                    handle TEXT NOT NULL, account TEXT NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL,
                    UNIQUE(task_id,kind));
                """)
                c.execute("INSERT INTO migrations VALUES(12,?)", (int(time.time()),))
            if not c.execute("SELECT 1 FROM migrations WHERE version=13").fetchone():
                # 上游算法跟踪（06D）。三张表分开：上游「上次看到哪儿」的状态、每次
                # 真的动过的事实、以及人对这次改动的判断。三样不同命：状态每次检查都
                # 会改，事实一旦写下就不再改，判断只有人能写。
                #
                # `intervening` 可空，和别处的 `known` 是同一条约定：0 表示「两次检查
                # 之间确实没有别的提交」，NULL 表示「有别的提交，但本机数不出有几条」。
                # 写 0 会把「看不到」说成「没有」。
                #
                # `files_total` 也可空：GitHub 一次提交的文件清单最多给 300 条，超过
                # 就只列前 300 条；总数拿不到时留空，并由 `files_truncated` +
                # `coverage_gap` 说明这次看到了多少。
                c.executescript("""
                CREATE TABLE IF NOT EXISTS algorithm_upstream_state(
                    repo TEXT PRIMARY KEY,
                    branch TEXT NOT NULL DEFAULT '',
                    etag TEXT NOT NULL DEFAULT '',
                    last_sha TEXT NOT NULL DEFAULT '',
                    last_commit_at INTEGER,
                    baseline INTEGER NOT NULL DEFAULT 0,
                    last_checked INTEGER,
                    last_ok INTEGER,
                    last_status TEXT NOT NULL DEFAULT '',
                    last_note TEXT NOT NULL DEFAULT '',
                    last_release_tag TEXT NOT NULL DEFAULT '',
                    checked_count INTEGER NOT NULL DEFAULT 0,
                    created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS algorithm_events(
                    id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL, sha TEXT NOT NULL,
                    branch TEXT NOT NULL DEFAULT '',
                    commit_at INTEGER,
                    author TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    parent_sha TEXT NOT NULL DEFAULT '',
                    intervening INTEGER,
                    files_json TEXT NOT NULL DEFAULT '[]',
                    modules_json TEXT NOT NULL DEFAULT '[]',
                    files_listed INTEGER NOT NULL DEFAULT 0,
                    files_total INTEGER,
                    files_truncated INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    coverage_gap TEXT NOT NULL DEFAULT '',
                    created INTEGER NOT NULL,
                    UNIQUE(repo,sha));
                CREATE INDEX IF NOT EXISTS algorithm_events_repo
                    ON algorithm_events(repo,created);
                CREATE TABLE IF NOT EXISTS algorithm_proposals(
                    event_id TEXT PRIMARY KEY REFERENCES algorithm_events(id),
                    repo TEXT NOT NULL, sha TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    analysis TEXT NOT NULL DEFAULT '',
                    engine TEXT NOT NULL DEFAULT '',
                    engine_error TEXT NOT NULL DEFAULT '',
                    impact TEXT NOT NULL DEFAULT '',
                    impact_at INTEGER,
                    notify_status TEXT NOT NULL DEFAULT 'pending',
                    notify_note TEXT NOT NULL DEFAULT '',
                    notified_at INTEGER,
                    created INTEGER NOT NULL, updated INTEGER NOT NULL,
                    cloud_status TEXT NOT NULL DEFAULT 'pending',
                    cloud_attempts INTEGER NOT NULL DEFAULT 0,
                    cloud_retry_at INTEGER NOT NULL DEFAULT 0,
                    cloud_error TEXT NOT NULL DEFAULT '',
                    cloud_synced_at INTEGER);
                CREATE INDEX IF NOT EXISTS algorithm_proposals_cloud
                    ON algorithm_proposals(cloud_status,cloud_retry_at);
                """)
                # 这两个键只写在 `DEFAULTS` 里是到不了老库的：`DEFAULTS` 只在 v1 那一段
                # 被种下去一次，之后新建的键老库永远没有。而 `jobs.enqueue` 的 `cfg`
                # 是直接读 settings 全表的，缺一个键就是 KeyError——检查在哪儿都会炸，
                # 不只是在面板上。所以这里跟着 migration 一起补上。
                for key, value in (("algorithm_repo", DEFAULTS["algorithm_repo"]),
                                   ("algorithm_watch", DEFAULTS["algorithm_watch"])):
                    c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)",
                              (key, json.dumps(value)))
                c.execute("INSERT INTO migrations VALUES(13,?)", (int(time.time()),))
        self.path.chmod(0o600)

    def get(self, key, default=None):
        with self.db() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value, c=None):
        if c is None:
            with self.db() as conn:
                self.set(key, value, conn)
            return
        c.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def settings(self):
        return {k: self.get(k, v) for k, v in DEFAULTS.items()}

    def rows(self, sql, args=()):
        with self.db() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def event(self, kind, label, detail=None, c=None):
        if c is None:
            with self.db() as conn:
                self.event(kind, label, detail, conn)
            return
        c.execute(
            "INSERT INTO events(kind,label,detail,created) VALUES(?,?,?,?)",
            (
                kind,
                label,
                json.dumps(detail or {}, ensure_ascii=False),
                int(time.time()),
            ),
        )

    def backup(self):
        folder = self.root / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / f"studio-{time.time_ns()}.sqlite3"
        with sqlite3.connect(self.path) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            # Transient browser material must not outlive its seven-day policy
            # through routine studio backups. Applied memories/versions remain.
            if dst.execute(
                "SELECT 1 FROM sqlite_master WHERE name='persona_imports'"
            ).fetchone():
                dst.execute("PRAGMA secure_delete=ON")
                dst.execute("DELETE FROM persona_sources")
                dst.execute("DELETE FROM persona_browser_actions")
                dst.execute(
                    "UPDATE persona_imports SET profile='{}', status=CASE WHEN status='applied' THEN status ELSE 'cancelled' END,message='备份不包含临时采集材料。'"
                )
                dst.commit()
                dst.execute("VACUUM")
        target.chmod(0o600)
        return target

    def import_legacy(self):
        old = self.root / "factory.sqlite3"
        if not old.exists() or self.get("legacy_imported", False):
            return
        folder = self.root / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        snapshot = folder / f"factory-before-studio-{time.time_ns()}.sqlite3"
        with sqlite3.connect(old) as src, sqlite3.connect(snapshot) as dst:
            src.backup(dst)
        snapshot.chmod(0o600)
        legacy = sqlite3.connect(snapshot)
        legacy.row_factory = sqlite3.Row
        now = int(time.time())
        from .security import Secrets, sanitize

        vault = Secrets(self.root)
        secret_values = vault.all().values()
        for name in [".env", "factory.json", "openclaw.json"]:
            source = self.root / name
            if source.is_file():
                dest = folder / (snapshot.stem + "-" + name.lstrip("."))
                shutil.copyfile(source, dest)
                dest.chmod(0o600)
        with self.db(True) as c:
            if c.execute(
                "SELECT 1 FROM settings WHERE key='legacy_imported'"
            ).fetchone():
                legacy.close()
                return
            tables = {
                r[0]
                for r in legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "personas" in tables:
                selected = legacy.execute(
                    "SELECT * FROM personas WHERE enabled=1 ORDER BY created_at LIMIT 1"
                ).fetchone()
                if selected:
                    persona = dict(PERSONA)
                    persona["name"] = selected["name"]
                    persona["version"] = 2
                    location = Path(selected["path"]).expanduser()
                    voice = location / "voice.md"
                    if voice.is_file():
                        persona["voice"] = sanitize(
                            voice.read_text()[:6000], secret_values
                        )
                    self.set("persona", persona, c)
                    c.execute(
                        "INSERT OR IGNORE INTO persona_versions VALUES(?,?,?)",
                        (2, json.dumps(persona, ensure_ascii=False), now),
                    )
                    archive = folder / (snapshot.stem + "-persona")
                    if location.is_dir():
                        shutil.copytree(location, archive, symlinks=True)
                        for item in archive.rglob("*"):
                            if not item.is_symlink():
                                item.chmod(0o700 if item.is_dir() else 0o600)
                    for corpus in [
                        location / "data/sanitized_corpus.jsonl",
                        location / "data/synthetic.json",
                    ]:
                        if not corpus.is_file():
                            continue
                        try:
                            content = corpus.read_text()
                            records = (
                                json.loads(content)
                                if corpus.suffix == ".json"
                                else [json.loads(line) for line in content.splitlines()]
                            )
                            if isinstance(records, dict):
                                records = records.get("records", [])
                            for record in records[:500]:
                                text = (
                                    record.get("text", "")
                                    if isinstance(record, dict)
                                    else str(record)
                                )
                                if text:
                                    c.execute(
                                        "INSERT INTO memories(category,text,source,created) VALUES(?,?,?,?)",
                                        (
                                            "style",
                                            sanitize(text, secret_values),
                                            "legacy_corpus",
                                            now,
                                        ),
                                    )
                        except (ValueError, TypeError):
                            self.event(
                                "migration", "旧版部分语料格式无法导入，已保留备份", c=c
                            )
            if "memory" in tables:
                for r in legacy.execute("SELECT * FROM memory"):
                    c.execute(
                        "INSERT INTO memories(category,text,source,created) VALUES(?,?,?,?)",
                        (
                            r["category"],
                            sanitize(r["content"], secret_values),
                            "legacy",
                            r["ts"],
                        ),
                    )
            if "pending_actions" in tables:
                for r in legacy.execute(
                    "SELECT * FROM pending_actions WHERE text != ''"
                ):
                    # No reliable original context: import as an archived draft, never claim posted.
                    c.execute(
                        "INSERT INTO drafts(id,kind,text,status,persona_version,created,updated) VALUES(?,?,?,?,?,?,?)",
                        (
                            "legacy-" + str(r["id"]),
                            "reply" if r["action"] == "reply" else "post",
                            sanitize(r["text"], secret_values),
                            "archived" if r["status"] != "pending" else "draft",
                            1,
                            r["ts"],
                            now,
                        ),
                    )
                    c.execute(
                        "INSERT INTO draft_versions(draft_id,version,text,created) VALUES(?,?,?,?)",
                        (
                            "legacy-" + str(r["id"]),
                            1,
                            sanitize(r["text"], secret_values),
                            now,
                        ),
                    )
            if "audit" in tables:
                for r in legacy.execute("SELECT id,action,ts,sent,shadow FROM audit"):
                    self.event(
                        "legacy",
                        "已保留旧版活动记录",
                        {
                            "legacy_id": r["id"],
                            "action": r["action"],
                            "legacy_sent": bool(r["sent"]),
                            "legacy_shadow": bool(r["shadow"]),
                        },
                        c,
                    )
            self.set("legacy_imported", True, c)
            self.set("paused", True, c)
            self.event("migration", "已迁移旧版数据；同步与生成保持暂停", c=c)
        legacy.close()

    def draft(self, text="", kind="post", post_id=None, engine="", c=None):
        if c is None:
            with self.db(True) as conn:
                return self.draft(text, kind, post_id, engine, conn)
        ident = uuid.uuid4().hex
        now = int(time.time())
        persona = json.loads(
            c.execute("SELECT value FROM settings WHERE key='persona'").fetchone()[0]
        )
        c.execute(
            "INSERT INTO drafts(id,kind,text,post_id,persona_version,engine,created,updated) VALUES(?,?,?,?,?,?,?,?)",
            (ident, kind, text, post_id, persona["version"], engine, now, now),
        )
        c.execute("INSERT INTO draft_versions(draft_id,version,text,created) VALUES(?,?,?,?)", (ident, 1, text, now))
        return ident
