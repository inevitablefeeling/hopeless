-- 0004_redesign: global admin permissions, global bans, audit trail, moderator roles, embed welcome,
-- customizable ticket panels. Additive and backward compatible. Never edit once deployed.

-- Global staff: explicit per-person permissions (administrators start with 'prestige' only).
ALTER TABLE global_staff ADD COLUMN perms TEXT[] NOT NULL DEFAULT '{}';
UPDATE global_staff SET perms = ARRAY['prestige'] WHERE role = 'administrator';

CREATE TABLE global_bans (
    case_id    BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    reason     TEXT   NOT NULL,
    issued_by  BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    lifted_by  BIGINT,
    lifted_at  TIMESTAMPTZ,
    lift_reason TEXT
);
CREATE UNIQUE INDEX global_bans_one_active ON global_bans (user_id) WHERE active;
CREATE INDEX global_bans_user_idx ON global_bans (user_id, created_at DESC);

CREATE TABLE global_audit (
    id         BIGSERIAL PRIMARY KEY,
    actor_id   BIGINT NOT NULL,
    action     TEXT   NOT NULL,
    target_id  BIGINT,
    detail     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX global_audit_idx ON global_audit (created_at DESC);

-- Per-server moderator roles (narrow the Discord permission check; never replace it).
ALTER TABLE mod_settings ADD COLUMN mod_role_ids BIGINT[] NOT NULL DEFAULT '{}';

-- Welcome and leave: embed styling.
ALTER TABLE welcome_settings
    ADD COLUMN use_embed   BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN title       TEXT,
    ADD COLUMN color       INTEGER,
    ADD COLUMN thumbnail   TEXT,
    ADD COLUMN footer      TEXT,
    ADD COLUMN rules_text  TEXT,
    ADD COLUMN leave_title TEXT;

-- Tickets: staff roles, messages, transcript destination, closing delay, limits.
ALTER TABLE ticket_config
    ADD COLUMN staff_role_ids        BIGINT[] NOT NULL DEFAULT '{}',
    ADD COLUMN opening_message       TEXT,
    ADD COLUMN instructions          TEXT,
    ADD COLUMN transcript_channel_id BIGINT,
    ADD COLUMN close_delay_seconds   INTEGER NOT NULL DEFAULT 3 CHECK (close_delay_seconds BETWEEN 0 AND 60),
    ADD COLUMN ticket_limit          INTEGER NOT NULL DEFAULT 1 CHECK (ticket_limit BETWEEN 1 AND 10),
    ADD COLUMN transcripts_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN default_priority      TEXT NOT NULL DEFAULT 'normal';

CREATE TABLE ticket_panels (
    id                 BIGSERIAL PRIMARY KEY,
    guild_id           BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    name               TEXT   NOT NULL,
    channel_id         BIGINT,
    message_id         BIGINT,
    title              TEXT   NOT NULL DEFAULT 'Ticket Panel',
    description        TEXT   NOT NULL DEFAULT 'Click **Create Ticket** for a general ticket, or pick a category below for something specific.',
    color              INTEGER,
    banner_url         TEXT,
    thumbnail_url      TEXT,
    footer             TEXT,
    button_label       TEXT   NOT NULL DEFAULT 'Create Ticket',
    button_style       TEXT   NOT NULL DEFAULT 'primary',
    button_emoji       TEXT,
    select_placeholder TEXT   NOT NULL DEFAULT 'Or choose a category...',
    UNIQUE (guild_id, name)
);

CREATE TABLE ticket_categories (
    id          BIGSERIAL PRIMARY KEY,
    panel_id    BIGINT NOT NULL REFERENCES ticket_panels (id) ON DELETE CASCADE,
    name        TEXT   NOT NULL,
    description TEXT,
    emoji       TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX ticket_categories_panel_idx ON ticket_categories (panel_id, position);

-- Tickets: more than one open ticket may be allowed (limit is enforced in code), and closing is a state.
DROP INDEX one_open_ticket_per_member;
ALTER TABLE tickets DROP CONSTRAINT tickets_status_check;
ALTER TABLE tickets ADD CONSTRAINT tickets_status_check CHECK (status IN ('open', 'closing', 'closed'));
ALTER TABLE tickets ADD COLUMN panel_id BIGINT, ADD COLUMN category_id BIGINT, ADD COLUMN close_started_at TIMESTAMPTZ;
CREATE INDEX tickets_owner_idx ON tickets (guild_id, owner_id, status);

CREATE TABLE ticket_events (
    id         BIGSERIAL PRIMARY KEY,
    ticket_id  BIGINT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    actor_id   BIGINT,
    action     TEXT   NOT NULL,
    detail     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ticket_events_idx ON ticket_events (ticket_id, created_at);
