-- Bound crawl-run recovery and recent-history lookups to the relevant source
-- rows instead of repeatedly scanning the full append-only run log.
CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_status_id
  ON crawl_runs (source_key, status, id DESC);

CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_id
  ON crawl_runs (source_key, id DESC);
