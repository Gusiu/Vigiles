-- Hub state (PLAN.md 7.3). Shared by all hub replicas.

-- Idempotent Receiver: a consumer processes a message_id once.
CREATE TABLE processed_messages (
    consumer     text NOT NULL,
    message_id   text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    instance     text,
    PRIMARY KEY (consumer, message_id)
);

-- Aggregator state lives here, not in memory: with Competing Consumers two
-- detectors of one zone may reach different replicas.
CREATE TABLE alarm_windows (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site       text NOT NULL,
    zone       text NOT NULL,
    opened_at  timestamptz NOT NULL,
    closes_at  timestamptz NOT NULL,
    detectors  text[] NOT NULL,
    status     text NOT NULL CHECK (status IN ('open', 'confirmed', 'expired')),
    decided_at timestamptz,
    correlation_id text
);
CREATE UNIQUE INDEX alarm_windows_one_open_idx ON alarm_windows (site, zone) WHERE status = 'open';

CREATE TABLE outbox (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id      uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    kind            text NOT NULL,
    method          text NOT NULL DEFAULT 'POST',
    target_url      text NOT NULL,
    payload         jsonb NOT NULL,
    headers         jsonb NOT NULL DEFAULT '{}',
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'dead')),
    attempts        integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_by       text,
    locked_until    timestamptz,
    last_error      text,
    correlation_id  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    sent_at         timestamptz
);
CREATE INDEX outbox_due_idx ON outbox (next_attempt_at) WHERE status = 'pending';

-- Dead Letter Channel; the vigiles/dlq/# topic is only a preview.
CREATE TABLE dead_letters (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dedup_key    text NOT NULL UNIQUE,
    message_id   text,
    source_topic text NOT NULL,
    reason       text NOT NULL,
    raw_payload  text NOT NULL,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    replayed_at  timestamptz
);

CREATE TABLE integration_events (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts             timestamptz NOT NULL DEFAULT now(),
    instance       text NOT NULL,
    stage          text NOT NULL,
    event          text NOT NULL,
    message_id     text,
    correlation_id text,
    outcome        text NOT NULL,
    details        jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX integration_events_correlation_idx ON integration_events (correlation_id);
