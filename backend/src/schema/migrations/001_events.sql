-- Phase 8, Step 8.3a — canonical event/incident history.
--
-- Single-table model (approved default): every envelope IS the incident
-- record; ack/escalation columns ride along. The API's severity/camera/
-- vehicle/time filters (8.4a) all hit indexed columns.
--
-- Idempotent on purpose: the runner records applied names, but a manual
-- psql re-run must stay safe too.
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,              -- CloudEvents id (edge-generated, dedupe key)
    envelope     JSONB NOT NULL,                -- raw validated CloudEvent (raw passthrough from 8.1a)
    event_type   TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    camera_id    TEXT NOT NULL,
    vehicle_id   TEXT NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL,          -- data.timestamp (edge clock, RFC-3339)
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    acked_at     TIMESTAMPTZ,                   -- NULL = unacked incident (dashboard filter)
    acked_by     TEXT,
    escalation   JSONB
);

CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events (occurred_at);  -- time windows + newest-first
CREATE INDEX IF NOT EXISTS idx_events_camera_id   ON events (camera_id);
CREATE INDEX IF NOT EXISTS idx_events_vehicle_id  ON events (vehicle_id);
CREATE INDEX IF NOT EXISTS idx_events_severity    ON events (severity);
