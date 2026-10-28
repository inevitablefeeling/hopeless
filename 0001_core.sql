-- 0001_core: foundation tables (prefixes, global staff, whitelist, AFK, command usage).
-- Never edit this file once it has been deployed. Add 0002_*.sql for changes.

CREATE TABLE guild_settings (
    guild_id   BIGINT PRIMARY KEY,
    prefix     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT guild_settings_prefix_len CHECK (prefix IS NULL OR char_length(prefix) BETWEEN 1 AND 5)
);

CREATE TABLE global_staff (
    user_id  BIGINT PRIMARY KEY,
    role     TEXT NOT NULL CHECK (role IN ('administrator', 'moderator')),
    added_by BIGINT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE whitelist_entries (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    target_type TEXT   NOT NULL CHECK (target_type IN ('user', 'role')),
    target_id   BIGINT NOT NULL,
    scope       TEXT   NOT NULL,
    added_by    BIGINT NOT NULL,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT whitelist_entries_unique UNIQUE (guild_id, target_type, target_id, scope)
);

CREATE TABLE afk_status (
    guild_id BIGINT NOT NULL REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    user_id  BIGINT NOT NULL,
    reason   TEXT   NOT NULL,
    since    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE command_usage (
    guild_id  BIGINT NOT NULL,            -- 0 = direct messages
    command   TEXT   NOT NULL,
    uses      BIGINT NOT NULL DEFAULT 0,
    last_used TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, command)
);
