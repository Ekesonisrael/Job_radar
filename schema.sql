-- Main applications tracker
create table applications (
  id uuid primary key default gen_random_uuid(),
  job_url text unique,
  title text,
  applied_at timestamptz,
  status text default 'found',   -- found | applied | closed | skipped
  followup_3_sent boolean default false,
  followup_7_sent boolean default false,
  followup_14_sent boolean default false
);

-- State storage for Telegram offset (prevents duplicate command processing)
create table bot_state (
  key text primary key,
  value text
);
