#!/usr/bin/env python3
"""
Builds the Grafana dashboard from code, in two versions:
  grafana/crawl-observability-dashboard.json    to import by hand (Dashboards > New > Import)
  grafana/dashboards/crawl-observability.json   loaded automatically by docker compose
Every SQL query is defined here, with a comment explaining what it measures.

Usage: python grafana/build_dashboard.py
"""

import json
from pathlib import Path

DS = {"type": "grafana-postgresql-datasource", "uid": "${DS_POSTGRES}"}
OUT = Path(__file__).with_name("crawl-observability-dashboard.json")          # for Dashboards > Import
PROVISIONED_OUT = Path(__file__).parent / "dashboards" / "crawl-observability.json"  # for docker compose
PROVISIONED_DS_UID = "crawl-postgres"  # uid in grafana/provisioning/datasources/postgres.yaml

_next_id = 0


def pid():
    global _next_id
    _next_id += 1
    return _next_id


def target(sql, fmt="time_series", ref="A", with_ds=True):
    t = {
        "refId": ref,
        "editorMode": "code",
        "format": fmt,
        "rawQuery": True,
        "rawSql": sql.strip(),
    }
    if with_ds:
        t["datasource"] = DS
    return t


def thresholds(*steps):
    """steps: (value or None for base, color)"""
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}


HEALTH_THRESHOLDS = thresholds((None, "red"), (50, "orange"), (85, "green"))
HEALTH_MAPPINGS = [
    {"type": "range", "options": {"from": 0, "to": 49.999, "result": {"text": "Blocked", "color": "red", "index": 0}}},
    {"type": "range", "options": {"from": 50, "to": 84.999, "result": {"text": "Degraded", "color": "orange", "index": 1}}},
    {"type": "range", "options": {"from": 85, "to": 100, "result": {"text": "Healthy", "color": "green", "index": 2}}},
]


def row(title, y):
    return {"type": "row", "id": pid(), "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def text(title, md, x, y, w, h):
    return {"type": "text", "id": pid(), "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": md.strip()}}


def stat(title, sql, x, y, w, h, fmt="table", unit="none", steps=((None, "red"),),
         graph="none", calc="lastNotNull", description="", decimals=None, extra_defaults=None):
    defaults = {"unit": unit, "color": {"mode": "thresholds"}, "thresholds": thresholds(*steps)}
    if decimals is not None:
        defaults["decimals"] = decimals
    if extra_defaults:
        defaults.update(extra_defaults)
    return {
        "type": "stat", "id": pid(), "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(sql, fmt)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": [calc], "fields": "", "values": False},
            "colorMode": "value", "graphMode": graph, "justifyMode": "center",
            "textMode": "auto", "orientation": "auto", "wideLayout": True,
            "showPercentChange": False,
        },
    }


def timeseries(title, sql, x, y, w, h, unit="none", custom=None, defaults=None, overrides=None,
               description="", legend="bottom"):
    c = {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0, "pointSize": 4,
         "showPoints": "never", "spanNulls": False, "axisSoftMin": 0}
    c.update(custom or {})
    d = {"unit": unit, "custom": c, "color": {"mode": "palette-classic"}}
    d.update(defaults or {})
    return {
        "type": "timeseries", "id": pid(), "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(sql)],
        "fieldConfig": {"defaults": d, "overrides": overrides or []},
        "options": {"legend": {"displayMode": "list", "placement": legend, "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    }


def fixed_color(name, color):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


# ---------------------------------------------------------------------------
# Shared SQL fragments
# ---------------------------------------------------------------------------

# Verified Googlebot on French product pages: the population hit by the incident.
FR_GOOGLEBOT = """
  AND host = 'www.example.fr'
  AND page_type = 'product'
  AND bot_family = 'googlebot'
  AND is_verified_bot"""

panels = []
y = 0

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
panels.append(text("", """
### Incident review: Googlebot blocked on www&#46;example&#46;fr product pages
A bot-mitigation rule (`CDN-FR-0412`) matched the Googlebot user agent without verifying the IP, and blocked Google's crawler for 28.5 hours. Scope: verified Googlebot on French product pages, compared with the German, British and US sites.

<small>Synthetic data: AWS sample web server logs and a simulated crawl layer · 18 to 22 March 2024, UTC · Vertical markers show CDN changes</small>
""", 0, y, 24, 4))
y += 4

# ---------------------------------------------------------------------------
# 1. Executive overview
# ---------------------------------------------------------------------------
panels.append(row("Executive overview", y)); y += 1

panels.append(stat(
    "FR crawl health, latest hour",
    # Share of verified Googlebot requests on FR product pages that got a non-error
    # response, per hour. The tile shows the latest hour; the sparkline shows the period.
    f"""
SELECT
  $__timeGroupAlias(ts, '1h'),
  100.0 * count(*) FILTER (WHERE status < 400) / count(*) AS "Crawl success rate"
FROM access_logs
WHERE $__timeFilter(ts){FR_GOOGLEBOT}
GROUP BY 1
ORDER BY 1""",
    0, y, 6, 6, fmt="time_series", unit="percent", graph="area", calc="lastNotNull", decimals=1,
    steps=((None, "red"), (50, "orange"), (85, "green")),
    description="Share of legitimate Googlebot requests (IP inside Google's ranges) on French product pages answered with a status below 400, over the latest hour. Sparkline: hourly values over the whole period.",
    extra_defaults={"min": 0, "max": 100},
))

panels.append(stat(
    "Legitimate Googlebot requests blocked",
    # Requests from verified Googlebot IPs that the CDN rule rejected.
    """
SELECT count(*) AS "Legitimate Googlebot requests blocked"
FROM access_logs
WHERE $__timeFilter(ts)
  AND bot_family = 'googlebot'
  AND is_verified_bot
  AND cdn_rule_id IS NOT NULL""",
    6, y, 6, 6, unit="locale", steps=((None, "red"),),
    description="Requests from verified Google IP ranges rejected by rule CDN-FR-0412 during the selected period.",
))

panels.append(stat(
    "FR product URLs Google could not crawl",
    # Distinct product URLs where at least one verified Googlebot request was blocked.
    """
SELECT count(DISTINCT path) AS "FR product URLs Google could not crawl"
FROM access_logs
WHERE $__timeFilter(ts)
  AND bot_family = 'googlebot'
  AND is_verified_bot
  AND cdn_rule_id IS NOT NULL""",
    12, y, 6, 6, unit="locale", steps=((None, "red"),),
    description="Distinct French product URLs with at least one blocked legitimate Googlebot request. These URLs may not have been refreshed in Google's index during the incident.",
))

panels.append(stat(
    "Incident duration",
    # Time between the rule deployment and its fix, read from the change log.
    """
SELECT extract(epoch FROM
  max(event_time) FILTER (WHERE event_type = 'cdn_fix')
  - max(event_time) FILTER (WHERE event_type = 'cdn_deploy')
) AS "Incident duration"
FROM change_events""",
    18, y, 6, 6, unit="dthms", steps=((None, "orange"),),
    description="Time between the deployment of CDN-FR-0412 v1 and the fix (v2), read from the change log.",
))
y += 6

# ---------------------------------------------------------------------------
# 2. Incident investigation
# ---------------------------------------------------------------------------
panels.append(row("Incident investigation", y)); y += 1

panels.append(timeseries(
    "Googlebot crawl success rate, FR product pages",
    # Same metric as the first tile, hour by hour. The line turns red below 50 %.
    f"""
SELECT
  $__timeGroupAlias(ts, '1h'),
  100.0 * count(*) FILTER (WHERE status < 400) / count(*) AS "Crawl success rate"
FROM access_logs
WHERE $__timeFilter(ts){FR_GOOGLEBOT}
GROUP BY 1
ORDER BY 1""",
    0, y, 24, 8, unit="percent",
    custom={"gradientMode": "scheme", "fillOpacity": 15, "thresholdsStyle": {"mode": "dashed"}},
    defaults={"min": 0, "max": 100, "color": {"mode": "thresholds"},
              "thresholds": thresholds((None, "red"), (50, "orange"), (85, "green"))},
    description="Hourly share of legitimate Googlebot requests on French product pages answered with a status below 400. Dashed lines: 50 % and 85 % thresholds.",
))
y += 8

state = {
    "type": "state-timeline", "id": pid(), "title": "Googlebot crawl health by market",
    "description": "Hourly crawl success rate of legitimate Googlebot on product pages, per market. Healthy: 85 % and above. Degraded: 50 to 85 %. Blocked: below 50 %. Hourly samples are small (30 to 50 requests per market): isolated orange cells reflect random errors.",
    "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 7},
    # One series per market (the `metric` column), same success-rate formula.
    "targets": [target("""
SELECT
  $__timeGroupAlias(ts, '1h'),
  upper(market) AS metric,
  100.0 * count(*) FILTER (WHERE status < 400) / count(*) AS value
FROM access_logs
WHERE $__timeFilter(ts)
  AND page_type = 'product'
  AND bot_family = 'googlebot'
  AND is_verified_bot
GROUP BY 1, 2
ORDER BY 1, 2""")],
    "fieldConfig": {"defaults": {
        "unit": "percent", "min": 0, "max": 100, "decimals": 0,
        "color": {"mode": "thresholds"}, "thresholds": HEALTH_THRESHOLDS, "mappings": HEALTH_MAPPINGS,
        "custom": {"lineWidth": 0, "fillOpacity": 85},
    }, "overrides": []},
    "options": {"mergeValues": True, "showValue": "never", "alignValue": "left", "rowHeight": 0.85,
                "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                "tooltip": {"mode": "single", "sort": "none"}},
}
panels.append(state)
y += 7

# ---------------------------------------------------------------------------
# 3. Root cause
# ---------------------------------------------------------------------------
panels.append(row("Root cause", y)); y += 1

panels.append(timeseries(
    "Googlebot traffic on FR product pages: legitimate vs spoofed",
    # Every request whose user agent says Googlebot, split by whether its IP is in
    # Google's published ranges. The spoofed share appears on 19 March: the scraper.
    """
SELECT
  $__timeGroupAlias(ts, '1h'),
  CASE WHEN is_verified_bot THEN 'Legitimate Googlebot (verified IP)'
       ELSE 'Spoofed Googlebot (IP outside Google ranges)' END AS metric,
  count(*) AS value
FROM access_logs
WHERE $__timeFilter(ts)
  AND host = 'www.example.fr'
  AND page_type = 'product'
  AND bot_family = 'googlebot'
GROUP BY 1, 2
ORDER BY 1, 2""",
    0, y, 12, 8, unit="short",
    custom={"drawStyle": "bars", "fillOpacity": 85, "lineWidth": 0, "stacking": {"mode": "normal", "group": "A"}},
    overrides=[fixed_color("Legitimate Googlebot (verified IP)", "green"),
               fixed_color("Spoofed Googlebot (IP outside Google ranges)", "purple")],
    description="Requests per hour whose user agent claims to be Googlebot. Legitimate: IP inside Google's published ranges. Spoofed: IP outside them.",
))

panels.append(timeseries(
    "Origin response time p95 by market, product pages",
    # 95th percentile of response time for requests that reached the origin
    # (CDN blocks are excluded: they are answered at the edge in a few ms).
    """
SELECT
  $__timeGroupAlias(ts, '1h'),
  upper(market) AS metric,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY response_time_ms) AS value
FROM access_logs
WHERE $__timeFilter(ts)
  AND page_type = 'product'
  AND cdn_rule_id IS NULL
GROUP BY 1, 2
ORDER BY 1, 2""",
    12, y, 12, 8, unit="ms",
    overrides=[fixed_color("FR", "red"), fixed_color("DE", "#8AB8FF"),
               fixed_color("UK", "#B877D9"), fixed_color("US", "#73BF69")],
    description="95th percentile of response time for product-page requests that reached the origin server: 95 % of requests are answered faster. Requests blocked at the CDN are excluded.",
))
y += 8

panels.append({
    "type": "table", "id": pid(), "title": "Googlebot requests on FR product pages by phase: served vs blocked by CDN-FR-0412",
    "description": "Requests whose user agent claims to be Googlebot, split by IP verification (legitimate or spoofed) and by outcome (served or blocked by CDN-FR-0412). Phases are read from the change log.",
    "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 6},
    # Phases are derived from the change log, not hard-coded.
    "targets": [target("""
WITH ev AS (
  SELECT max(event_time) FILTER (WHERE event_type = 'cdn_deploy') AS deployed,
         max(event_time) FILTER (WHERE event_type = 'cdn_fix') AS fixed
  FROM change_events
)
SELECT
  CASE WHEN ts < ev.deployed THEN '1. Before the rule'
       WHEN ts < ev.fixed THEN '2. Rule v1 (UA match only)'
       ELSE '3. Rule v2 (verified-bot exception)' END AS "Phase",
  count(*) FILTER (WHERE is_verified_bot AND cdn_rule_id IS NULL) AS "Legitimate Googlebot served",
  count(*) FILTER (WHERE is_verified_bot AND cdn_rule_id IS NOT NULL) AS "Legitimate Googlebot blocked",
  count(*) FILTER (WHERE NOT is_verified_bot AND cdn_rule_id IS NULL) AS "Spoofed Googlebot served",
  count(*) FILTER (WHERE NOT is_verified_bot AND cdn_rule_id IS NOT NULL) AS "Spoofed Googlebot blocked"
FROM access_logs, ev
WHERE $__timeFilter(ts)
  AND host = 'www.example.fr'
  AND page_type = 'product'
  AND bot_family = 'googlebot'
GROUP BY 1
ORDER BY 1""", fmt="table")],
    "fieldConfig": {"defaults": {"custom": {"align": "center", "cellOptions": {"type": "auto"}}}, "overrides": [
        {"matcher": {"id": "byName", "options": "Phase"},
         "properties": [{"id": "custom.align", "value": "left"}, {"id": "custom.width", "value": 300}]},
        {"matcher": {"id": "byName", "options": "Legitimate Googlebot blocked"},
         "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": thresholds((None, "green"), (1, "red"))},
                        {"id": "color", "value": {"mode": "thresholds"}}]},
        {"matcher": {"id": "byName", "options": "Spoofed Googlebot served"},
         "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": thresholds((None, "green"), (1, "orange"))},
                        {"id": "color", "value": {"mode": "thresholds"}}]},
    ]},
    "options": {"showHeader": True, "cellHeight": "md", "footer": {"show": False}},
})
y += 6

# ---------------------------------------------------------------------------
# 4. Data quality
# ---------------------------------------------------------------------------
panels.append(row("Data quality", y)); y += 1

panels.append(stat(
    "Latest ingestion run",
    # Volumes from the most recent run of ingest/ingest.py.
    """
SELECT rows_loaded AS "Rows loaded",
       duplicates_dropped AS "Duplicates dropped",
       rejected_lines AS "Malformed lines rejected"
FROM ingest_runs
ORDER BY run_id DESC
LIMIT 1""",
    0, y, 10, 5, unit="locale", steps=((None, "text"),),
    description="Volumes from the latest run of the ingestion script. Duplicates and malformed lines are counted, then excluded.",
))

panels.append(stat(
    "Unidentified user agents",
    # Requests whose user agent is empty or '-': no bot or browser can be inferred.
    """
SELECT 100.0 * count(*) FILTER (WHERE bot_family = 'unknown') / count(*) AS "Unidentified user agents"
FROM access_logs
WHERE $__timeFilter(ts)""",
    10, y, 5, 5, unit="percent", decimals=2, steps=((None, "green"), (1, "orange"), (5, "red")),
    description="Share of requests with an empty user agent ('' or '-'). They cannot be classified as bot or human.",
))

panels.append(stat(
    "Data loaded",
    # When the ingestion script last finished.
    """
SELECT extract(epoch FROM finished_at) * 1000 AS "Data loaded"
FROM ingest_runs
ORDER BY run_id DESC
LIMIT 1""",
    15, y, 4, 5, unit="dateTimeAsIso", steps=((None, "text"),),
    description="Last successful run of the ingestion script.",
))

panels.append({
    "type": "table", "id": pid(), "title": "Rejected lines by reason",
    "description": "Raw lines the parser refused, grouped by reason.",
    "datasource": DS, "gridPos": {"x": 19, "y": y, "w": 5, "h": 5},
    "targets": [target("""
SELECT reason AS "Reason", count(*) AS "Lines"
FROM ingest_rejects
GROUP BY 1
ORDER BY 2 DESC""", fmt="table")],
    "fieldConfig": {"defaults": {}, "overrides": []},
    "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
})
y += 5



# ---------------------------------------------------------------------------
# Annotations: CDN changes from the change log, drawn on every time panel
# ---------------------------------------------------------------------------

def annotation(name, color, event_type):
    return {
        "name": name, "enable": True, "iconColor": color, "datasource": DS,
        "target": target(f"""
SELECT event_time AS time, title AS text, event_type AS tags
FROM change_events
WHERE $__timeFilter(event_time)
  AND event_type = '{event_type}'
ORDER BY 1""", fmt="table", ref="Anno", with_ds=False),
    }


dashboard = {
    "__inputs":[{"name": "DS_POSTGRES", "label": "PostgreSQL (Neon)", "description": "",
                  "type": "datasource", "pluginId": "grafana-postgresql-datasource", "pluginName": "PostgreSQL"}],
    "__requires": [{"type": "datasource", "id": "grafana-postgresql-datasource", "name": "PostgreSQL", "version": "1.0.0"}],
    "title": "Crawl Observability: CDN incident review",
    "uid": "crawl-observability-dashboard",
    "description": "CDN rule blocking Googlebot on French product pages: detection, localisation, root cause and recovery.",
    "tags": ["seo", "crawl", "logs", "portfolio"],
    "timezone": "utc",
    "graphTooltip": 1,
    "editable": True,
    "time": {"from": "2024-03-18T00:00:00.000Z", "to": "2024-03-23T00:00:00.000Z"},
    "timepicker": {},
    "refresh": "",
    "schemaVersion": 39,
    "version": 1,
    "templating": {"list": []},
    "links": [],
    "annotations": {"list": [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        annotation("Latency alert", "yellow", "alert"),
        annotation("CDN rule deployed", "red", "cdn_deploy"),
        annotation("CDN rule fixed", "green", "cdn_fix"),
    ]},
    "panels": panels,
}

OUT.write_text(json.dumps(dashboard, indent=2), encoding="utf-8")
print(f"Wrote {OUT} ({len(panels)} panels)")

# Provisioned copy for the Docker stack: no import inputs, datasource bound by uid.
provisioned = json.loads(json.dumps(dashboard).replace("${DS_POSTGRES}", PROVISIONED_DS_UID))
provisioned.pop("__inputs"); provisioned.pop("__requires")
PROVISIONED_OUT.write_text(json.dumps(provisioned, indent=2), encoding="utf-8")
print(f"Wrote {PROVISIONED_OUT}")
