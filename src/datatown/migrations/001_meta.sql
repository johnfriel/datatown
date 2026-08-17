CREATE TABLE IF NOT EXISTS meta.dataset_snapshots (
    id uuid PRIMARY KEY,
    source text NOT NULL CHECK (source <> ''),
    dataset text NOT NULL CHECK (dataset <> ''),
    acquired_at timestamptz NOT NULL,
    source_url text,
    description text,
    manifest_object_key text NOT NULL UNIQUE CHECK (manifest_object_key <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, dataset, acquired_at)
);

CREATE TABLE IF NOT EXISTS meta.dataset_files (
    id uuid PRIMARY KEY,
    snapshot_id uuid NOT NULL REFERENCES meta.dataset_snapshots(id) ON DELETE RESTRICT,
    role text NOT NULL CHECK (role <> ''),
    filename text NOT NULL CHECK (filename <> ''),
    object_key text NOT NULL UNIQUE CHECK (object_key <> ''),
    format text NOT NULL CHECK (format <> ''),
    sha256 text NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, role)
);

CREATE INDEX IF NOT EXISTS dataset_files_snapshot_id_idx
    ON meta.dataset_files (snapshot_id);

CREATE TABLE IF NOT EXISTS meta.import_runs (
    id uuid PRIMARY KEY,
    snapshot_id uuid NOT NULL REFERENCES meta.dataset_snapshots(id) ON DELETE RESTRICT,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    target_schema text NOT NULL CHECK (target_schema <> ''),
    target_table text NOT NULL CHECK (target_table <> ''),
    row_count bigint CHECK (row_count IS NULL OR row_count >= 0),
    importer text NOT NULL CHECK (importer <> ''),
    git_commit text,
    error text
);

CREATE INDEX IF NOT EXISTS import_runs_snapshot_id_idx
    ON meta.import_runs (snapshot_id);

