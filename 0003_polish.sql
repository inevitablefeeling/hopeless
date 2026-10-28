-- 0003_polish: kick as the safer default response, multi-channel logging, voice interface channel.
-- Never edit this file once deployed. Add 0004_*.sql for changes.

ALTER TABLE antinuke_guild ALTER COLUMN default_punishment SET DEFAULT 'kick';
ALTER TABLE logging_settings ADD COLUMN channels JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE voice_config ADD COLUMN interface_channel_id BIGINT;
ALTER TABLE ticket_config ALTER COLUMN categories SET DEFAULT '{"Support","Contact","Partnership","Report","Ban Appeal","Other"}';
ALTER TABLE ticket_config ALTER COLUMN panel_title SET DEFAULT 'Ticket Panel';
ALTER TABLE ticket_config ALTER COLUMN panel_text SET DEFAULT 'Click **Create Ticket** for a general ticket, or pick a category below for something specific.';
ALTER TABLE logging_settings ADD COLUMN alert_role_ids BIGINT[] NOT NULL DEFAULT '{}';
