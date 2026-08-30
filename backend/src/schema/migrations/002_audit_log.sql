-- Phase 8, Step 8.3a — append-only operator-action audit trail.
--
-- ack/escalate (8.4a) writes the events columns AND one row here in the
-- same transaction. FK to events: an audit row always points at a real
-- event; NO ACTION (default) means history cannot be silently deleted
-- out from under the audit log.
CREATE TABLE IF NOT EXISTS audit_log (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action   TEXT NOT NULL,                     -- 'ack' | 'escalate' today; open vocabulary by design
    event_id TEXT NOT NULL REFERENCES events (event_id),
    actor    TEXT NOT NULL,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail   JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_log_event_id ON audit_log (event_id);  -- incident detail join
