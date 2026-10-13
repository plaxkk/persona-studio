-- Run in the user's Supabase project before enabling local Analytics delivery.
-- Business responses and normalized metrics are private; no browser role gets access.
CREATE TABLE IF NOT EXISTS public.persona_analytics_runs (
    id text PRIMARY KEY,
    account text NOT NULL,
    source text NOT NULL,
    window_from text NOT NULL,
    window_to text NOT NULL,
    observed_at bigint NOT NULL,
    request_limit integer NOT NULL,
    received_count integer NOT NULL,
    parsed_count integer NOT NULL,
    gap_count integer NOT NULL,
    at_request_limit boolean NOT NULL,
    parser_version integer NOT NULL,
    raw_payload jsonb NOT NULL,
    coverage_gaps jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS public.persona_analytics_post_metrics (
    run_id text NOT NULL REFERENCES public.persona_analytics_runs(id),
    account text NOT NULL,
    post_id text NOT NULL,
    published_at_ms bigint NOT NULL,
    body_text text NOT NULL,
    metrics jsonb NOT NULL,
    observed_at bigint NOT NULL,
    PRIMARY KEY (run_id, post_id)
);
CREATE INDEX IF NOT EXISTS persona_analytics_post_history
    ON public.persona_analytics_post_metrics(account, post_id, observed_at DESC);

ALTER TABLE public.persona_analytics_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.persona_analytics_post_metrics ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.persona_analytics_runs FROM anon, authenticated;
REVOKE ALL ON TABLE public.persona_analytics_post_metrics FROM anon, authenticated;
GRANT ALL ON TABLE public.persona_analytics_runs TO service_role;
GRANT ALL ON TABLE public.persona_analytics_post_metrics TO service_role;
