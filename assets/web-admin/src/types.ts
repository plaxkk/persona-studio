export type Engine = {
  id: string;
  installed: boolean;
  supported: boolean;
  status: string;
  checked: number;
  key_present: boolean;
  login_ready?: boolean;
  local_model?: string;
  message?: string;
  config: { model?: string; base_url?: string; reasoning?: string };
};
export type Persona = {
  name: string;
  identity: string;
  voice: string;
  interests: string;
  boundaries: string;
  version: number;
};
export type Settings = {
  brand: string;
  paused: boolean;
  engine: string;
  sync_interval: number;
  timezone: string;
  owner_id: string;
  telegram_enabled: boolean;
  x_username: string;
  x_status: string;
  last_sync: number;
  last_sync_requested: number;
  auto_replies: number;
  auto_posts: number;
  per_round: number;
  worker_heartbeat: number;
};
export type Task = {
  creation_id?: string;
  id: string;
  kind: string;
  status: string;
  automatic: number;
  created: number;
  error: string;
  message: string;
  result: {
    text?: string;
    draft_id?: string;
    conflict?: boolean;
    suggestion?: string;
    reason?: string;
  };
};
export type Activity = {
  id: number;
  kind: string;
  label: string;
  created: number;
  detail: Record<string, unknown>;
};
export type Source = {
  name: string;
  status: string;
  last_success: number;
  last_attempt: number;
  count: number;
};
export type Overview = {
  settings: Settings;
  persona: Persona;
  engines: Engine[];
  worker_alive: boolean;
  sync?: {stale: boolean; last_complete: number; latest: {status: string; error: string; updated: number} | null; sources: Source[]};
  counts: { new: number; drafts: number; confirmed: number };
  tasks: Task[];
  events: Activity[];
  sources: Source[];
  usage: { day: string; kind: string; automatic: number; count: number }[];
};
export type Post = {
  id: string;
  author: string;
  text: string;
  url: string;
  kind: string;
  status: string;
  reason: string;
  context: {
    parent_id?: string;
    parent?: {
      text?: string;
      author?: string;
      url?: string;
      unavailable?: boolean;
    };
    quote?: { text: string; id: string };
    suggestion?: string;
    source?: string;
    published_at?: number | null;
    manual_context_note?: string;
    media?: { kind: string; source?: string; description: string }[];
    truncated?: boolean;
    media_pending?: boolean;
  };
  created: number;
  draft?: Draft | null;
  review?: {
    context_status: "ready" | "pending";
    context_issue: string;
    topic: "developer" | "travel" | "other";
    scene: "direct" | "question" | "discussion";
    scenario?: string;
    score: Record<string, { value: number; reason: string }>;
    score_total: number | null;
    contribution: string;
    evidence: string;
    decision: string;
    decision_reason: string;
  };
};
export type InteractionPage = {
  items: Post[];
  total: number;
  offset: number;
  limit: number;
  topics: { developer: number; travel: number; other: number };
};
export type InteractionSkill = {
  id: number;
  name: string;
  level: "practiced" | "learning" | "want" | "uncertain";
  evidence: string;
  confirmed: number;
};
export type Draft = {
  creation_id?: string;
  id: string;
  kind: string;
  text: string;
  post_id: string | null;
  status: string;
  version: number;
  edited: number;
  engine: string;
  persona_version: number;
  created: number;
  updated: number;
  result_url: string;
  tweet_id?: string | null;
  user_confirmed_at?: number | null;
  platform_observed_at?: number | null;
  published_at?: number | null;
  publication_kind?: "post" | "quote" | "reply" | null;
  source_url?: string;
  source_text?: string;
  source_author?: string;
};
export type Connections = {
  x: {
    username: string;
    status: string;
    credentials: Record<string, boolean>;
    verified_at: number;
  };
  telegram: {
    owner_id: string;
    enabled: boolean;
    status: string;
    credentials: Record<string, boolean>;
  };
  supabase: {
    url: string;
    configured: boolean;
    queued: number;
    synced: number;
    last_status: string;
    last_checked_at: number;
    last_error: string;
  };
};
export type Message = {
  id: number;
  role: string;
  text: string;
  created: number;
};
export type Studio = {
  overview: Overview;
  drafts: Draft[];
  connections: Connections;
  messages: Message[];
};
export type Page =
  | "home"
  | "persona"
  | "inbox"
  | "compose"
  | "history"
  | "settings";
export type Actions = {
  refresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
  navigate: (page: Page) => void;
  run: <T>(
    action: () => Promise<T>,
    success?: string,
  ) => Promise<T | undefined>;
};
