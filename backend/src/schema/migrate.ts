/**
 * Ordered-SQL migration runner (Phase 8, Step 8.3a).
 *
 * No ORM, no migration framework — migrations are visible .sql files
 * applied in filename order, each inside one transaction, recorded in a
 * `schema_migrations` table. Rerun is a no-op. The runner refuses loudly
 * when history diverges (applied-but-missing-on-disk, out-of-order
 * application, numbering gaps) instead of guessing.
 *
 * SQL files live next to this module (runtime reads them from disk —
 * backend runs via tsx, like the bridge). Install/run: `pnpm migrate`.
 */
import { Pool } from "pg";
import { readdir, readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

export interface MigrationFile {
  name: string;
  sql: string;
}

/** Structural slice of pg's Pool/Client — fakes implement this in tests. */
export interface SqlExecutor {
  query(sql: string, values?: unknown[]): Promise<{ rows: unknown[] }>;
}

export interface ApplyReport {
  applied: string[];
  skipped: string[];
}

export const DEFAULT_DATABASE_URL = "postgres://mobisentra:mobisentra@localhost:5432/mobisentra";

const MIGRATIONS_DIR = new URL("./migrations/", import.meta.url);
const NAME_PATTERN = /^\d{3}_[a-z0-9_]+\.sql$/;

const SCHEMA_MIGRATIONS_DDL = `CREATE TABLE IF NOT EXISTS schema_migrations (
  name       TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)`;

/** Lists the bundled migrations, validating naming and consecutive numbering (001, 002, …). */
export async function listMigrations(dir: URL = MIGRATIONS_DIR): Promise<MigrationFile[]> {
  const names = (await readdir(dir)).filter((name) => name.endsWith(".sql")).sort();
  if (names.length === 0) {
    throw new Error(`no migrations found in ${dir.pathname} — expected at least 001_*.sql`);
  }
  const files: MigrationFile[] = [];
  for (const [index, name] of names.entries()) {
    if (!NAME_PATTERN.test(name)) {
      throw new Error(`migration name "${name}" must match NNN_description.sql ([0-9]{3}_[a-z0-9_]+.sql)`);
    }
    if (Number(name.slice(0, 3)) !== index + 1) {
      throw new Error(`migration numbering broken at "${name}": expected ${String(index + 1).padStart(3, "0")} — must be consecutive from 001, no gaps or duplicates`);
    }
    files.push({ name, sql: await readFile(new URL(name, dir), "utf8") });
  }
  return files;
}

/** Applies pending migrations; every refusal below is a hard error (never guess). */
export async function applyMigrations(exec: SqlExecutor, migrations: MigrationFile[]): Promise<ApplyReport> {
  await exec.query(SCHEMA_MIGRATIONS_DDL);
  const applied = (await exec.query("SELECT name FROM schema_migrations ORDER BY name"))
    .rows.map(rowName)
    .filter((name): name is string => name !== null);
  const diskNames = migrations.map((m) => m.name);

  for (const name of applied) {
    if (!diskNames.includes(name)) {
      throw new Error(`migration "${name}" is recorded as applied but missing from the migrations directory — history diverged; resolve manually`);
    }
  }
  for (const [index, name] of applied.entries()) {
    if (diskNames[index] !== name) {
      throw new Error(`migration history out of order: "${name}" is applied but "${diskNames[index]}" comes first and is not — refusing to guess`);
    }
  }

  const pending = migrations.slice(applied.length);
  for (const migration of pending) {
    try {
      await exec.query("BEGIN");
      await exec.query(migration.sql);
      await exec.query("INSERT INTO schema_migrations (name) VALUES ($1)", [migration.name]);
      await exec.query("COMMIT");
    } catch (err) {
      await exec.query("ROLLBACK").catch(() => undefined); // best-effort cleanup; the original error is the signal
      throw err;
    }
  }
  return { applied: pending.map((m) => m.name), skipped: applied };
}

function rowName(row: unknown): string | null {
  if (row !== null && typeof row === "object" && "name" in row) {
    const value = (row as Record<string, unknown>)["name"];
    return typeof value === "string" ? value : null;
  }
  return null;
}

/** Lists + applies against `DATABASE_URL` (default = the compose dev stack). */
export async function runFromEnv(): Promise<ApplyReport> {
  const pool = new Pool({ connectionString: process.env.DATABASE_URL ?? DEFAULT_DATABASE_URL });
  try {
    return await applyMigrations(pool, await listMigrations());
  } finally {
    await pool.end();
  }
}

const invokedDirectly = process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  runFromEnv()
    .then((report) => {
      const detail = report.applied.length > 0 ? ` (${report.applied.join(", ")})` : "";
      console.log(`[migrate] applied ${report.applied.length}, skipped ${report.skipped.length}${detail}`);
    })
    .catch((err: unknown) => {
      // pg connection errors can carry an empty .message (AggregateError) — the name keeps the log non-blank
      const detail = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
      console.error(`[migrate] failed: ${detail}`);
      process.exitCode = 1;
    });
}
