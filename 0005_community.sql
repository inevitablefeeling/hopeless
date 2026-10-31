-- 0005_community: message tracking and leveling, giveaways, polls, reputation (status and server tag), extra owners.
-- Additive. Never edit once deployed.

-- Message counts: tracked from the day the bot starts counting (Discord has no historical counter).
CREATE TABLE member_activity (
    guild_id   BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    messages   BIGINT NOT NULL DEFAULT 0,
    xp         BIGINT NOT NULL DEFAULT 0,
    level      INTEGER NOT NULL DEFAULT 0,
    tracking_since TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX member_activity_xp_idx ON member_activity (guild_id, xp DESC);

CREATE TABLE leveling_settings (
    guild_id        BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    log_channel_id  BIGINT,
    xp_min          INTEGER NOT NULL DEFAULT 15 CHECK (xp_min BETWEEN 1 AND 100),
    xp_max          INTEGER NOT NULL DEFAULT 25 CHECK (xp_max BETWEEN 1 AND 200),
    cooldown_seconds INTEGER NOT NULL DEFAULT 60 CHECK (cooldown_seconds BETWEEN 5 AND 600)
);

CREATE TABLE level_roles (
    guild_id BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    level    INTEGER NOT NULL CHECK (level BETWEEN 1 AND 500),
    role_id  BIGINT NOT NULL,
    PRIMARY KEY (guild_id, level)
);

CREATE TABLE giveaways (
    id               BIGSERIAL PRIMARY KEY,
    guild_id         BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    channel_id       BIGINT NOT NULL,
    message_id       BIGINT,
    host_id          BIGINT NOT NULL,
    prize            TEXT NOT NULL,
    winner_count     INTEGER NOT NULL DEFAULT 1 CHECK (winner_count BETWEEN 1 AND 20),
    ends_at          TIMESTAMPTZ NOT NULL,
    ended            BOOLEAN NOT NULL DEFAULT FALSE,
    required_role_id BIGINT,
    min_messages     INTEGER NOT NULL DEFAULT 0 CHECK (min_messages >= 0),
    winner_ids       BIGINT[] NOT NULL DEFAULT '{}',
    v2               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX giveaways_active_idx ON giveaways (ended, ends_at);
CREATE INDEX giveaways_guild_idx ON giveaways (guild_id, ended);

CREATE TABLE giveaway_entries (
    giveaway_id BIGINT NOT NULL REFERENCES giveaways (id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL,
    entered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (giveaway_id, user_id)
);

CREATE TABLE polls (
    id               BIGSERIAL PRIMARY KEY,
    guild_id         BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    channel_id       BIGINT NOT NULL,
    message_id       BIGINT,
    creator_id       BIGINT NOT NULL,
    question         TEXT NOT NULL,
    options          TEXT[] NOT NULL,
    ends_at          TIMESTAMPTZ,
    closed           BOOLEAN NOT NULL DEFAULT FALSE,
    required_role_id BIGINT,
    min_messages     INTEGER NOT NULL DEFAULT 0 CHECK (min_messages >= 0),
    allow_change     BOOLEAN NOT NULL DEFAULT FALSE,
    v2               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX polls_active_idx ON polls (closed, ends_at);

CREATE TABLE poll_votes (
    poll_id      BIGINT NOT NULL REFERENCES polls (id) ON DELETE CASCADE,
    user_id      BIGINT NOT NULL,
    option_index INTEGER NOT NULL CHECK (option_index >= 0),
    voted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (poll_id, user_id)
);

-- Reputation: a role while the member has the configured text in their custom status and/or wears this server's tag.
CREATE TABLE rep_settings (
    guild_id          BIGINT PRIMARY KEY REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    status_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    status_role_id    BIGINT,
    status_log_id     BIGINT,
    status_targets    TEXT[] NOT NULL DEFAULT '{}',
    tag_enabled       BOOLEAN NOT NULL DEFAULT FALSE,
    tag_role_id       BIGINT,
    tag_log_id        BIGINT
);

-- Who currently holds a rep role because of which condition (so only roles the bot granted are ever removed).
CREATE TABLE rep_members (
    guild_id   BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('status', 'tag')),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id, kind)
);

-- Extra owners: scoped to ONE server, never global.
CREATE TABLE extra_owners (
    guild_id BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    user_id  BIGINT NOT NULL,
    added_by BIGINT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id)
);
