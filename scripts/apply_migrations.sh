#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="${REPO_ROOT:-$(pwd)}"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-$ROOT_DIR/sql/migrations}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required."
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "psql command is required."
  exit 1
fi

if [[ ! -d "$MIGRATIONS_DIR" ]]; then
  echo "Migrations directory not found: $MIGRATIONS_DIR"
  exit 1
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<SQL
CREATE TABLE IF NOT EXISTS public.app_schema_migrations (
  version TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL

shopt -s nullglob
migration_files=("$MIGRATIONS_DIR"/*.sql)
shopt -u nullglob

if [[ ${#migration_files[@]} -eq 0 ]]; then
  echo "No migration files found in $MIGRATIONS_DIR."
  exit 0
fi

IFS=$'
' migration_files=($(printf '%s
' "${migration_files[@]}" | sort))
unset IFS

applied_count=0
skipped_count=0

for migration_file in "${migration_files[@]}"; do
  migration_name="$(basename "$migration_file")"
  migration_version="${migration_name%.sql}"

  already_applied="$(
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v version="$migration_version" -tA <<'SQL'
SELECT 1
FROM public.app_schema_migrations
WHERE version = :'version'
LIMIT 1;
SQL
  )"

  if [[ "$(echo "$already_applied" | tr -d '[:space:]')" == "1" ]]; then
    echo "Skipping $migration_name (already applied)"
    skipped_count=$((skipped_count + 1))
    continue
  fi

  echo "Applying $migration_name"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration_file"

  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v version="$migration_version" -v name="$migration_name" <<'SQL'
INSERT INTO public.app_schema_migrations (version, name)
VALUES (:'version', :'name');
SQL

  applied_count=$((applied_count + 1))
done

echo "Migration complete. Applied: $applied_count, skipped: $skipped_count."
