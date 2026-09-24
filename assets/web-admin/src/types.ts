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
  };
  created: number;
};
export type Draft = {
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
  posts: Post[];
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
