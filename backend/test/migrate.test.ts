import { afterEach, describe, expect, it } from "vitest";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  applyMigrations,
  listMigrations,
  type MigrationFile,
  type SqlExecutor,
} from "../src/schema/migrate.js";

/**
 * Fake pg — implements just enough server semantics for the runner:
 * schema_migrations DDL is a no-op, the SELECT returns the recorded
 * names, INSERT INTO schema_migrations records its parameter, and
 * BEGIN/COMMIT/ROLLBACK track (and check) transaction nesting.
 */
class FakePg implements SqlExecutor {
  readonly statements: string[] = [];
  readonly insertValues: unknown[][] = [];
  appliedNames: string[] = [];
  /** When set, any query whose SQL contains this substring throws. */
  failOn: string | null = null;
  private txnOpen = false;

  async query(sql: string, values?: unknown[]): Promise<{ rows: unknown[] }> {
    if (this.failOn !== null && sql.includes(this.failOn)) {
      throw new Error(`injected failure at: ${sql.slice(0, 60)}`);
    }
    this.statements.push(sql);
    if (values !== undefined) {
      this.insertValues.push(values);
    }
    if (sql.startsWith("CREATE TABLE IF NOT EXISTS schema_migrations")) {
      return { rows: [] };
    }
    if (sql.startsWith("SELECT name FROM schema_migrations")) {
      return { rows: this.appliedNames.map((name) => ({ name })) };
    }
    if (sql.startsWith("INSERT INTO schema_migrations")) {
      const name = values?.[0];
      if (typeof name === "string") {
        this.appliedNames.push(name);
        this.appliedNames.sort();
      }
      return { rows: [] };
    }
    if (sql === "BEGIN") {
      if (this.txnOpen) throw new Error("nested BEGIN");
      this.txnOpen = true;
      return { rows: [] };
    }
    if (sql === "COMMIT") {
      if (!this.txnOpen) throw new Error("COMMIT without BEGIN");
      this.txnOpen = false;
      return { rows: [] };
    }
    if (sql === "ROLLBACK") {
      if (!this.txnOpen) throw new Error("ROLLBACK without BEGIN");
      this.txnOpen = false;
      return { rows: [] };
    }
    return { rows: [] }; // migration DDL
  }

  count(statement: string): number {
    return this.statements.filter((sql) => sql === statement).length;
  }
}

async function tempMigrationDir(files: Array<{ name: string; sql: string }>): Promise<URL> {
  const dir = await mkdtemp(join(tmpdir(), "mobisentra-migrate-"));
  for (const file of files) {
    await writeFile(join(dir, file.name), file.sql);
  }
  return pathToFileURL(`${dir}/`);
}

afterEach(() => {
  expect(new FakePg().statements, "no stray queries").toEqual([]);
});

describe("listMigrations (real bundled dir)", () => {
  it("returns the shipped migrations in order with their SQL", async () => {
    const migrations = await listMigrations();
    expect(migrations.map((m) => m.name)).toEqual(["001_events.sql", "002_audit_log.sql"]);
    expect(migrations[0]?.sql).toContain("CREATE TABLE IF NOT EXISTS events");
    expect(migrations[0]?.sql).toContain("event_id     TEXT PRIMARY KEY");
    expect(migrations[1]?.sql).toContain("CREATE TABLE IF NOT EXISTS audit_log");
    expect(migrations[1]?.sql).toContain("REFERENCES events (event_id)");
  });

  it("rejects a numbering gap with the expected number in the message", async () => {
    const dir = await tempMigrationDir([
      { name: "001_a.sql", sql: "-- a" },
      { name: "003_b.sql", sql: "-- b" },
    ]);
    await expect(listMigrations(dir)).rejects.toThrow(/003_b\.sql.*expected 002.*no gaps/s);
  });

  it("rejects names that do not match NNN_description.sql", async () => {
    const dir = await tempMigrationDir([{ name: "junk.sql", sql: "-- x" }]);
    await expect(listMigrations(dir)).rejects.toThrow(/must match NNN_description\.sql/);
  });

  it("rejects an empty migrations directory (packaging bug, not a clean apply)", async () => {
    const dir = await tempMigrationDir([]);
    await expect(listMigrations(dir)).rejects.toThrow(/no migrations found/);
  });
});

describe("applyMigrations", () => {
  it("fresh DB: creates bookkeeping, applies each migration in one transaction, records names", async () => {
    const fake = new FakePg();
    const report = await applyMigrations(fake, await listMigrations());

    expect(report.applied).toEqual(["001_events.sql", "002_audit_log.sql"]);
    expect(report.skipped).toEqual([]);
    expect(fake.statements[0]).toContain("CREATE TABLE IF NOT EXISTS schema_migrations");
    expect(fake.statements[1]).toBe("SELECT name FROM schema_migrations ORDER BY name");
    // each migration: BEGIN → its SQL → recorded INSERT → COMMIT, no interleaving
    const beginAt = fake.statements.indexOf("BEGIN");
    expect(fake.statements[beginAt + 1]).toContain("CREATE TABLE IF NOT EXISTS events");
    expect(fake.statements[beginAt + 2]).toBe("INSERT INTO schema_migrations (name) VALUES ($1)");
    expect(fake.statements[beginAt + 3]).toBe("COMMIT");
    expect(fake.count("BEGIN")).toBe(2);
    expect(fake.count("COMMIT")).toBe(2);
    expect(fake.appliedNames).toEqual(["001_events.sql", "002_audit_log.sql"]);
    expect(fake.insertValues).toEqual([["001_events.sql"], ["002_audit_log.sql"]]);
  });

  it("rerun is a no-op: nothing applied, no transactions opened", async () => {
    const fake = new FakePg();
    await applyMigrations(fake, await listMigrations());
    const report = await applyMigrations(fake, await listMigrations());

    expect(report.applied).toEqual([]);
    expect(report.skipped).toEqual(["001_events.sql", "002_audit_log.sql"]);
    expect(fake.count("BEGIN")).toBe(2); // unchanged from the first run
  });

  it("applied-but-missing-on-disk → clear divergence error", async () => {
    const fake = new FakePg();
    fake.appliedNames = ["001_events.sql", "002_audit_log.sql", "099_gone.sql"];
    await expect(applyMigrations(fake, await listMigrations())).rejects.toThrow(
      /099_gone\.sql.*missing from the migrations directory.*resolve manually/s
    );
  });

  it("out-of-order history (later applied, earlier pending) → refuses", async () => {
    const fake = new FakePg();
    fake.appliedNames = ["002_audit_log.sql"];
    await expect(applyMigrations(fake, await listMigrations())).rejects.toThrow(
      /out of order.*002_audit_log\.sql.*001_events\.sql.*refusing to guess/s
    );
  });

  it("failing migration: ROLLBACK issued, failure propagates, nothing recorded past it", async () => {
    const fake = new FakePg();
    fake.failOn = "CREATE TABLE IF NOT EXISTS audit_log"; // 002's DDL
    const migrations: MigrationFile[] = [
      { name: "001_events.sql", sql: "CREATE TABLE IF NOT EXISTS events ();" },
      { name: "002_audit_log.sql", sql: "CREATE TABLE IF NOT EXISTS audit_log ();" },
    ];
    await expect(applyMigrations(fake, migrations)).rejects.toThrow(/injected failure/);
    expect(fake.statements).toContain("ROLLBACK");
    expect(fake.appliedNames).toEqual(["001_events.sql"]); // 001 committed, 002 not recorded
  });
});
