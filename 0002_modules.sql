-- 0002_modules: moderation, logging, welcome, antinuke, automod, tickets, voice, templates, prestige.
-- Never edit this file once deployed. Add 0003_*.sql for changes.

CREATE TABLE logging_settings (
    guild_id            BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    server_channel_id   BIGINT,
    security_channel_id BIGINT,
    disabled_events     TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE welcome_settings (
    guild_id         BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    enabled          BOOLEAN NOT NULL DEFAULT FALSE,
    channel_id       BIGINT,
    message          TEXT,
    dm_enabled       BOOLEAN NOT NULL DEFAULT FALSE,
    dm_message       TEXT,
    leave_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    leave_channel_id BIGINT,
    leave_message    TEXT,
    image_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    image_url        TEXT
);

CREATE TABLE mod_settings (
    guild_id     BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    jail_role_id BIGINT,
    autorole_ids BIGINT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE mod_actions (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    action     TEXT   NOT NULL,
    target_id  BIGINT,
    actor_id   BIGINT,
    reason     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX mod_actions_guild_idx ON mod_actions (guild_id, created_at DESC);

CREATE TABLE jail_records (
    guild_id   BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    role_ids   BIGINT[] NOT NULL DEFAULT '{}',
    reason     TEXT,
    jailed_by  BIGINT,
    jailed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    release_at TIMESTAMPTZ,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX jail_due_idx ON jail_records (release_at) WHERE release_at IS NOT NULL;

CREATE TABLE channel_locks (
    guild_id   BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    channel_id BIGINT NOT NULL,
    previous   TEXT   NOT NULL CHECK (previous IN ('none', 'allow', 'deny')),
    locked_by  BIGINT,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE antinuke_guild (
    guild_id           BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    enabled            BOOLEAN NOT NULL DEFAULT FALSE,
    default_punishment TEXT    NOT NULL DEFAULT 'ban',
    alert_at           INTEGER NOT NULL DEFAULT 2,
    punish_at          INTEGER NOT NULL DEFAULT 3,
    window_seconds     INTEGER NOT NULL DEFAULT 60
);

CREATE TABLE antinuke_modules (
    guild_id       BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    key            TEXT   NOT NULL,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    punishment     TEXT,
    alert_at       INTEGER,
    punish_at      INTEGER,
    window_seconds INTEGER,
    restore        BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE antinuke_incidents (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    key        TEXT   NOT NULL,
    actor_id   BIGINT,
    target_id  BIGINT,
    level      TEXT   NOT NULL,
    detail     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX antinuke_incidents_idx ON antinuke_incidents (guild_id, created_at DESC);

CREATE TABLE snapshots (
    guild_id  BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    kind      TEXT   NOT NULL CHECK (kind IN ('channel', 'role')),
    target_id BIGINT NOT NULL,
    data      JSONB  NOT NULL,
    taken_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, kind, target_id)
);

CREATE TABLE automod_rules (
    guild_id       BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    rule           TEXT   NOT NULL,
    enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    threshold      INTEGER,
    window_seconds INTEGER,
    action         TEXT,
    PRIMARY KEY (guild_id, rule)
);

CREATE TABLE automod_exemptions (
    guild_id BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    rule     TEXT   NOT NULL,
    kind     TEXT   NOT NULL CHECK (kind IN ('domain', 'channel')),
    value    TEXT   NOT NULL,
    PRIMARY KEY (guild_id, rule, kind, value)
);

CREATE TABLE ticket_config (
    guild_id         BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    category_id      BIGINT,
    support_role_id  BIGINT,
    panel_channel_id BIGINT,
    panel_message_id BIGINT,
    panel_title      TEXT NOT NULL DEFAULT 'Support tickets',
    panel_text       TEXT NOT NULL DEFAULT 'Pick a topic below to open a private ticket with our staff.',
    categories       TEXT[] NOT NULL DEFAULT '{"General Support","Reports","Appeals"}',
    naming           TEXT NOT NULL DEFAULT 'ticket-{number}',
    autoclose_hours  INTEGER,
    inactivity_hours INTEGER
);

CREATE TABLE tickets (
    id            BIGSERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    number        INTEGER NOT NULL,
    channel_id    BIGINT UNIQUE,
    owner_id      BIGINT NOT NULL,
    category      TEXT   NOT NULL,
    status        TEXT   NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    priority      TEXT   NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    claimed_by    BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at     TIMESTAMPTZ,
    last_activity TIMESTAMPTZ NOT NULL DEFAULT now(),
    warned        BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX one_open_ticket_per_member ON tickets (guild_id, owner_id) WHERE status = 'open';
CREATE INDEX tickets_guild_idx ON tickets (guild_id, status);

CREATE TABLE ticket_notes (
    id         BIGSERIAL PRIMARY KEY,
    ticket_id  BIGINT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    author_id  BIGINT NOT NULL,
    note       TEXT   NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE voice_config (
    guild_id        BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    category_id     BIGINT,
    join_channel_id BIGINT,
    user_limit      INTEGER NOT NULL DEFAULT 0 CHECK (user_limit BETWEEN 0 AND 99),
    name_template   TEXT    NOT NULL DEFAULT '{user}''s room'
);

CREATE TABLE voice_rooms (
    channel_id BIGINT PRIMARY KEY,
    guild_id   BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    owner_id   BIGINT NOT NULL,
    waiting_channel_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX voice_rooms_guild_idx ON voice_rooms (guild_id);

CREATE TABLE templates (
    id            BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL,
    name          TEXT   NOT NULL,
    data          JSONB  NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, name)
);

CREATE TABLE prestige_credits (
    user_id BIGINT PRIMARY KEY,
    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0)
);

CREATE TABLE prestige_servers (
    guild_id   BIGINT PRIMARY KEY,
    enabled_by BIGINT NOT NULL,
    source     TEXT   NOT NULL,
    enabled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tag_roles (
    guild_id BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    role_id  BIGINT NOT NULL,
    tag      TEXT   NOT NULL
);
