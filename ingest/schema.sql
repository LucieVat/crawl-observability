-- Schema for the Crawl Observability project.
-- The ingestion script runs this file on every load: the dataset is synthetic
-- and fully reproducible, so each run rebuilds the tables from scratch.

DROP TABLE IF EXISTS ingest_rejects;
DROP TABLE IF EXISTS ingest_runs;
DROP TABLE IF EXISTS change_events;
DROP TABLE IF EXISTS access_logs;

-- One row per HTTP request, after parsing, deduplication and enrichment.
CREATE TABLE access_logs (
    log_id            text PRIMARY KEY,
    ts                timestamptz NOT NULL,      -- request time (UTC)
    source            text NOT NULL,             -- 'aws_generator' or 'crawl_simulation'
    ip                inet,
    method            text,
    path              text,
    protocol          text,
    status            smallint,                  -- status actually served (after the CDN)
    origin_status     smallint,                  -- status the origin server would have returned
    bytes_sent        integer,
    user_agent        text,
    referer           text,
    -- Simulated dimensions (see README: "What is real and what is simulated")
    market            text,                      -- fr, de, uk, us
    host              text,
    application_pool  text,
    page_type         text,                      -- product, product_image, listing, content, api, static, crawl_directive
    directory         text,                      -- first path segment
    bot_family        text,                      -- googlebot, bingbot, ..., browser, automation_tool, unknown
    is_bot            boolean,
    is_verified_bot   boolean,                   -- IP inside the bot owner's published ranges
    cdn_rule_id       text,                      -- CDN rule that blocked the request, if any
    blocked_reason    text,
    response_time_ms  integer
);

CREATE INDEX access_logs_ts_idx          ON access_logs (ts);
CREATE INDEX access_logs_host_ts_idx     ON access_logs (host, ts);
CREATE INDEX access_logs_bot_ts_idx      ON access_logs (bot_family, ts);
CREATE INDEX access_logs_rule_idx        ON access_logs (cdn_rule_id) WHERE cdn_rule_id IS NOT NULL;

-- Change log used for Grafana annotations (deployments, fixes).
CREATE TABLE change_events (
    event_time   timestamptz NOT NULL,
    event_type   text NOT NULL,                  -- 'cdn_deploy', 'cdn_fix', 'traffic_change'
    cdn_rule_id  text,
    title        text NOT NULL,
    description  text
);

-- One row per run of the ingestion script (data freshness, volumes, rejects).
CREATE TABLE ingest_runs (
    run_id               serial PRIMARY KEY,
    started_at           timestamptz NOT NULL,
    finished_at          timestamptz,
    seed                 integer,
    aws_records          integer,
    lines_read           integer,
    rows_loaded          integer,
    duplicates_dropped   integer,
    rejected_lines       integer,
    data_min_ts          timestamptz,
    data_max_ts          timestamptz
);

-- Raw lines the parser could not load, with the reason.
CREATE TABLE ingest_rejects (
    run_id       integer REFERENCES ingest_runs (run_id),
    source_file  text,
    line_number  integer,
    reason       text,
    raw_line     text
);
