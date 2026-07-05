CREATE TABLE IF NOT EXISTS store (
    id            SERIAL PRIMARY KEY,
    store_key     TEXT UNIQUE NOT NULL,
    brand_names   JSONB NOT NULL,
    competitors   JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product (
    id          SERIAL PRIMARY KEY,
    store_id    INT NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    sku         TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price       NUMERIC,
    category    TEXT NOT NULL DEFAULT '',
    attributes  JSONB NOT NULL DEFAULT '{}',
    UNIQUE (store_id, sku)
);

CREATE TABLE IF NOT EXISTS prompt (
    id        SERIAL PRIMARY KEY,
    store_id  INT NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    type      TEXT NOT NULL CHECK (type IN ('product_intent', 'brand_sov')),
    category  TEXT NOT NULL DEFAULT '',
    version   INT NOT NULL DEFAULT 1,
    active    BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (store_id, text, version)
);

CREATE TABLE IF NOT EXISTS run (
    id             SERIAL PRIMARY KEY,
    store_id       INT NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'complete', 'degraded', 'failed')),
    coverage       REAL,
    execution_arn  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS observation (
    id                SERIAL PRIMARY KEY,
    run_id            INT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    prompt_id         INT NOT NULL REFERENCES prompt(id),
    engine            TEXT NOT NULL,
    model             TEXT NOT NULL,
    samples_total     INT NOT NULL,
    samples_present   INT NOT NULL,
    rank              REAL,               -- median rank across present samples
    sentiment         TEXT,
    framing           TEXT NOT NULL DEFAULT '',
    competitors_named JSONB NOT NULL DEFAULT '[]',
    citations         JSONB NOT NULL DEFAULT '[]',
    confidence_flag   TEXT NOT NULL DEFAULT 'ok'
                      CHECK (confidence_flag IN ('ok', 'low_confidence', 'unparseable')),
    raw_s3_keys       JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS obs_run_idx ON observation (run_id);
CREATE INDEX IF NOT EXISTS obs_prompt_engine_idx ON observation (prompt_id, engine, model);

CREATE TABLE IF NOT EXISTS diagnosis (
    id             SERIAL PRIMARY KEY,
    observation_id INT NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
    reasons        JSONB NOT NULL,
    priority       TEXT NOT NULL CHECK (priority IN ('high', 'medium', 'low'))
);

CREATE TABLE IF NOT EXISTS fix_draft (
    id           SERIAL PRIMARY KEY,
    diagnosis_id INT NOT NULL REFERENCES diagnosis(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('copy', 'schema', 'qa', 'attribute')),
    content      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'suggested'
                 CHECK (status IN ('suggested', 'approved', 'rejected', 'refused')),
    refusal_reason TEXT
);
