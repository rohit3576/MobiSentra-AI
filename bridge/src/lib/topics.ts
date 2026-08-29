/**
 * Topic mapping (extracted in Phase 7, 7.3a — single source of truth).
 *
 * MQTT topics use slashes; Kafka topics use dots: `mobisentra/events`
 * → `mobisentra.events`. Only topics under `<prefix>/` are forwarded —
 * anything else returns null and is ignored by the caller.
 *
 * The Phase-0 gotcha lives here on purpose: a dotted topic like
 * `mobisentra.events` does NOT match the `mobisentra/#` subscription and,
 * if it ever reaches us by misconfiguration, has no `<prefix>/` head —
 * it maps to null and is dropped, never forwarded as-is.
 */
export function mapTopic(mqttTopic: string, prefix: string): string | null {
  if (!mqttTopic.startsWith(`${prefix}/`)) {
    return null;
  }
  return mqttTopic.replaceAll("/", ".");
}
