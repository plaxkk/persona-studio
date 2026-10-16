-- Server-only snapshots for Growth Plan. Personal plans, check-ins, handles,
-- and relationship notes stay in local SQLite.
CREATE TABLE IF NOT EXISTS public.persona_growth_reward_snapshots (
    id text PRIMARY KEY,
    account text NOT NULL,
    observed_at bigint NOT NULL,
    page_status text NOT NULL,
    period text,
    metrics jsonb NOT NULL,
    raw_payload jsonb NOT NULL,
    source text NOT NULL
);
CREATE INDEX IF NOT EXISTS persona_growth_rewards_history
    ON public.persona_growth_reward_snapshots(account, observed_at DESC);

CREATE TABLE IF NOT EXISTS public.persona_growth_follower_reads (
    task_id text PRIMARY KEY,
    account text NOT NULL,
    observed_at bigint NOT NULL,
    discovered integer NOT NULL,
    read_count integer NOT NULL,
    stopped_reason text NOT NULL,
    cumulative_bands jsonb NOT NULL,
    exact_count integer NOT NULL,
    verified_observed integer NOT NULL
);
CREATE INDEX IF NOT EXISTS persona_growth_follower_history
    ON public.persona_growth_follower_reads(account, observed_at DESC);

ALTER TABLE public.persona_growth_reward_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.persona_growth_follower_reads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.persona_growth_reward_snapshots FROM anon, authenticated;
REVOKE ALL ON TABLE public.persona_growth_follower_reads FROM anon, authenticated;
GRANT ALL ON TABLE public.persona_growth_reward_snapshots TO service_role;
GRANT ALL ON TABLE public.persona_growth_follower_reads TO service_role;
