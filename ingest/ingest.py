#!/usr/bin/env python3
"""
Crawl Observability: ingestion pipeline.

What this script does, in order:
  1. Generates web server logs with the AWS sample generator (unchanged code,
     only the number of records is raised).
  2. Generates a simulated crawl layer: verified Googlebot and Bingbot traffic
     on four country hosts, a scraper spoofing Googlebot, a few automation
     tools, and a small, documented share of data-quality issues.
  3. Parses both raw files, rejects malformed lines, drops duplicates.
  4. Enriches every request with simulated dimensions (market, host,
     application pool, bot family, bot verification, response time).
  5. Applies the CDN incident: rule CDN-FR-0412 blocks real Googlebot on
     French product pages between its deployment and its fix.
  6. Loads everything into PostgreSQL (or writes a CSV with --dry-run).

Usage:
  python ingest/ingest.py              # load into the database in DATABASE_URL (.env)
  python ingest/ingest.py --dry-run    # no database, writes data/processed/access_logs.csv
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import math
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vendor.aws_sample_webserver_logs import generate_synthetic_logs as aws  # noqa: E402

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Scenario settings. Everything simulated is driven from here.
# ---------------------------------------------------------------------------

SEED = 42
AWS_RECORDS = 100_000  # the AWS sample ships with 10,000; raised for volume

WINDOW_START = datetime(2024, 3, 18, 0, 0, 0, tzinfo=UTC)  # same window as the AWS generator
WINDOW_END = datetime(2024, 3, 23, 0, 0, 0, tzinfo=UTC)

MARKETS = {
    # market: host, share of AWS (human) traffic, verified Googlebot requests per hour
    "fr": {"host": "www.example.fr", "aws_share": 0.22, "googlebot_per_hour": 90},
    "de": {"host": "www.example.de", "aws_share": 0.20, "googlebot_per_hour": 70},
    "uk": {"host": "www.example.co.uk", "aws_share": 0.23, "googlebot_per_hour": 80},
    "us": {"host": "www.example.com", "aws_share": 0.35, "googlebot_per_hour": 140},
}
HOST_TO_MARKET = {cfg["host"]: m for m, cfg in MARKETS.items()}
BINGBOT_RATIO = 0.25          # Bingbot volume relative to Googlebot
CATALOGUE_SIZE = 4_000        # product URLs per market in the crawl layer

# The incident timeline (UTC)
SCRAPER_START = datetime(2024, 3, 19, 14, 0, tzinfo=UTC)   # spoofed Googlebot starts on the FR catalogue
SCRAPER_PER_HOUR = 150
CDN_RULE_ID = "CDN-FR-0412"
RULE_DEPLOYED = datetime(2024, 3, 20, 9, 40, tzinfo=UTC)   # v1: matches the UA token only (the bug)
RULE_FIXED = datetime(2024, 3, 21, 14, 10, tzinfo=UTC)     # v2: verified-bot exception added
INCIDENT_HOST = "www.example.fr"
INCIDENT_PATH_PREFIX = "/product/"

# Simplified published IP ranges used for bot verification.
# Real verification uses a reverse DNS lookup confirmed by a forward lookup.
BOT_RANGES = {
    "google": [ipaddress.ip_network("66.249.64.0/19")],
    "bing": [ipaddress.ip_network(n) for n in ("157.55.39.0/24", "207.46.13.0/24", "40.77.167.0/24")],
}
FAMILY_OWNER = {"googlebot": "google", "googlebot_image": "google", "bingbot": "bing"}

# Scrapers and tools use IETF documentation ranges, so no real address appears.
SCRAPER_NETWORKS = [ipaddress.ip_network("203.0.113.0/24"), ipaddress.ip_network("198.51.100.0/24")]

# Share of data-quality issues injected into the simulated crawl layer only
DUPLICATE_RATE = 0.003
TRUNCATED_RATE = 0.0005
BAD_TIMESTAMP_RATE = 0.0002

GOOGLEBOT_SMARTPHONE_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.6261.94 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
GOOGLEBOT_DESKTOP_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BINGBOT_UA = (
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; "
    "+http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36"
)
AUTOMATION_UAS = ["python-requests/2.31.0", "curl/8.4.0", "Go-http-client/1.1"]
UNKNOWN_UAS = ["", "-"]

RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
AWS_RAW_FILE = RAW_DIR / "aws_webserver_logs_bulk.txt"
CRAWL_RAW_FILE = RAW_DIR / "crawl_simulation.ndjson"

COLUMNS = [
    "log_id", "ts", "source", "ip", "method", "path", "protocol", "status", "origin_status",
    "bytes_sent", "user_agent", "referer", "market", "host", "application_pool", "page_type",
    "directory", "bot_family", "is_bot", "is_verified_bot", "cdn_rule_id", "blocked_reason",
    "response_time_ms",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def poisson(rng: random.Random, lam: float) -> int:
    """Number of events in an interval with average `lam` (Poisson draw)."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def random_ip(rng: random.Random, networks: list) -> str:
    net = rng.choice(networks)
    return str(net[rng.randint(1, net.num_addresses - 2)])


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def weighted(rng: random.Random, pairs: list):
    values, weights = zip(*pairs)
    return rng.choices(values, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Step 1: AWS sample logs (unchanged generator)
# ---------------------------------------------------------------------------

def generate_aws_logs() -> None:
    aws.NUM_RECORDS = AWS_RECORDS
    aws.OUTPUT_FILE = str(AWS_RAW_FILE)
    aws.main()  # seeds its own random generator with 42


# ---------------------------------------------------------------------------
# Step 2: simulated crawl layer
# ---------------------------------------------------------------------------

def build_catalogues(rng: random.Random) -> dict:
    """A fixed set of product URLs per market, so crawlers revisit the same URLs."""
    return {m: rng.sample(range(10000, 99999), CATALOGUE_SIZE) for m in MARKETS}


def crawl_path(rng: random.Random, catalogue: list) -> str:
    kind = weighted(rng, [("product", 55), ("listing", 18), ("content", 5), ("static", 17), ("directive", 5)])
    if kind == "product":
        pid = rng.choice(catalogue)
        return f"/product/{pid}/detail" if rng.random() < 0.85 else f"/product/{pid}/reviews"
    if kind == "listing":
        if rng.random() < 0.7:
            return rng.choice(aws.BROWSE_PATHS)
        return rng.choice(aws.FILTER_PATHS).format(brand=rng.choice(aws.BRANDS), cat=rng.choice(aws.CATEGORIES))
    if kind == "content":
        return rng.choice(aws.PAGE_PATHS)
    if kind == "static":
        return rng.choice([p for p in aws.STATIC_PATHS if p not in ("/robots.txt", "/sitemap.xml")])
    return rng.choice(["/robots.txt", "/sitemap.xml"])


def crawl_status(rng: random.Random, path: str) -> int:
    if path in ("/robots.txt", "/sitemap.xml"):
        return 200
    if path.startswith("/static/") or path in ("/favicon.ico", "/apple-touch-icon.png"):
        return weighted(rng, [(200, 80), (304, 20)])
    return weighted(rng, [(200, 90), (304, 4), (404, 3), (301, 2), (500, 1)])


def bytes_for(rng: random.Random, status: int) -> int:
    if status == 304:
        return 0
    if status in (301, 302):
        return rng.randint(0, 500)
    if status >= 400:
        return rng.randint(200, 5000)
    return rng.randint(2000, 60000)


def diurnal(ts: datetime) -> float:
    hour = ts.hour + ts.minute / 60
    return 1 + 0.15 * math.sin(2 * math.pi * (hour - 6) / 24)


def generate_crawl_layer(rng: random.Random) -> int:
    """Writes the simulated crawl layer as one JSON document per line."""
    catalogues = build_catalogues(rng)
    scraper_ips = [random_ip(rng, SCRAPER_NETWORKS) for _ in range(12)]
    events = []
    counter = 0

    def add(ts, host, ip, ua, path, status, method="GET"):
        nonlocal counter
        counter += 1
        events.append({
            "log_id": f"C{counter:07d}",
            "ip": ip,
            "timestamp": iso(ts),
            "method": method,
            "path": path,
            "protocol": "HTTP/1.1",
            "status": status,
            "bytes_sent": bytes_for(rng, status),
            "user_agent": ua,
            "host": host,
        })

    bucket = timedelta(minutes=5)
    t = WINDOW_START
    while t < WINDOW_END:
        factor = diurnal(t)
        for market, cfg in MARKETS.items():
            host = cfg["host"]
            per_bucket = cfg["googlebot_per_hour"] / 12 * factor
            # Verified Googlebot
            for _ in range(poisson(rng, per_bucket)):
                ts = t + timedelta(seconds=rng.uniform(0, 299))
                ua = GOOGLEBOT_SMARTPHONE_UA if rng.random() < 0.8 else GOOGLEBOT_DESKTOP_UA
                path = crawl_path(rng, catalogues[market])
                add(ts, host, random_ip(rng, BOT_RANGES["google"]), ua, path, crawl_status(rng, path))
            # Verified Bingbot
            for _ in range(poisson(rng, per_bucket * BINGBOT_RATIO)):
                ts = t + timedelta(seconds=rng.uniform(0, 299))
                path = crawl_path(rng, catalogues[market])
                add(ts, host, random_ip(rng, BOT_RANGES["bing"]), BINGBOT_UA, path, crawl_status(rng, path))
            # Automation tools and unidentifiable user agents
            for _ in range(poisson(rng, 2 / 12)):
                ts = t + timedelta(seconds=rng.uniform(0, 299))
                path = crawl_path(rng, catalogues[market])
                add(ts, host, random_ip(rng, SCRAPER_NETWORKS), rng.choice(AUTOMATION_UAS), path, crawl_status(rng, path))
            for _ in range(poisson(rng, 0.5 / 12)):
                ts = t + timedelta(seconds=rng.uniform(0, 299))
                path = crawl_path(rng, catalogues[market])
                add(ts, host, random_ip(rng, SCRAPER_NETWORKS), rng.choice(UNKNOWN_UAS), path, crawl_status(rng, path))
        # Scraper spoofing Googlebot on the French catalogue
        if t >= SCRAPER_START:
            for _ in range(poisson(rng, SCRAPER_PER_HOUR / 12)):
                ts = t + timedelta(seconds=rng.uniform(0, 299))
                pid = rng.randint(10000, 99999)
                add(ts, INCIDENT_HOST, rng.choice(scraper_ips), GOOGLEBOT_DESKTOP_UA, f"/product/{pid}/detail", 200)
        t += bucket

    events.sort(key=lambda e: e["timestamp"])
    with open(CRAWL_RAW_FILE, "w", encoding="utf-8") as f:
        for e in events:
            line = json.dumps(e)
            roll = rng.random()
            if roll < TRUNCATED_RATE:
                f.write(line[: len(line) // 2] + "\n")          # cut-off line
                continue
            if roll < TRUNCATED_RATE + BAD_TIMESTAMP_RATE:
                bad = dict(e, timestamp=e["timestamp"][:11] + "25:61:00Z")
                f.write(json.dumps(bad) + "\n")                  # impossible time
                continue
            f.write(line + "\n")
            if rng.random() < DUPLICATE_RATE:
                f.write(line + "\n")                             # delivered twice
    return len(events)


# ---------------------------------------------------------------------------
# Step 3: parsing and validation
# ---------------------------------------------------------------------------

REQUIRED = ("log_id", "timestamp", "path", "status", "user_agent")


def read_documents(path: Path, source: str, rejects: list):
    """Yields (source, document). The AWS file is in OpenSearch bulk format:
    an index-creation command, then pairs of lines (action line, document)."""
    in_bulk = source != "aws_generator"
    with open(path, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not in_bulk:
                in_bulk = line == "POST /_bulk"
                continue
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                rejects.append((path.name, line_no, "invalid_json", line[:500]))
                continue
            if isinstance(doc, dict) and "index" in doc and len(doc) == 1:
                continue  # bulk action line, not a log
            missing = [k for k in REQUIRED if k not in doc]
            if missing:
                rejects.append((path.name, line_no, "missing_field:" + ",".join(missing), line[:500]))
                continue
            try:
                doc["_ts"] = datetime.strptime(doc["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            except ValueError:
                rejects.append((path.name, line_no, "invalid_timestamp", line[:500]))
                continue
            yield source, doc


# ---------------------------------------------------------------------------
# Step 4 and 5: enrichment and CDN incident
# ---------------------------------------------------------------------------

def classify_path(path: str) -> tuple[str, str]:
    """Returns (page_type, directory)."""
    first = "/" + path.lstrip("/").split("/", 1)[0]
    if path in ("/robots.txt", "/sitemap.xml"):
        return "crawl_directive", "/"
    if path.startswith("/product/"):
        return "product", "/product"
    if path.startswith("/image/"):
        return "product_image", "/image"
    if path.startswith(("/browse/", "/filter/")):
        return "listing", first
    if path.startswith("/api/"):
        return "api", "/api"
    if path.startswith("/static/") or path in ("/favicon.ico", "/apple-touch-icon.png"):
        return "static", "/static" if path.startswith("/static/") else "/"
    return "content", "/"


POOL_GROUP = {
    "product": "catalog", "product_image": "catalog", "listing": "listing",
    "content": "content", "api": "api", "static": "static", "crawl_directive": "static",
}


def bot_family(ua: str) -> str:
    u = (ua or "").strip().lower()
    if u in ("", "-"):
        return "unknown"
    if "googlebot-image" in u:
        return "googlebot_image"
    if "googlebot" in u:
        return "googlebot"
    if "bingbot" in u:
        return "bingbot"
    if "ahrefsbot" in u:
        return "ahrefsbot"
    if "semrushbot" in u:
        return "semrushbot"
    if u.startswith(("python-requests", "curl/", "go-http-client", "wget/")):
        return "automation_tool"
    if u.startswith("mozilla/"):
        return "browser"
    return "unknown"


def verify(ip: str, family: str):
    """True or False for families with published ranges, None otherwise."""
    owner = FAMILY_OWNER.get(family)
    if owner is None:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in BOT_RANGES[owner])


MEDIAN_MS = {
    "static": 30, "crawl_directive": 20, "content": 180, "listing": 240,
    "product": 280, "product_image": 60, "api": 150,
}


def response_time(rng: random.Random, page_type: str, status: int, overloaded: bool, blocked: bool) -> int:
    if blocked:
        return max(3, int(12 * math.exp(rng.gauss(0, 0.3))))  # answered at the edge
    ms = MEDIAN_MS[page_type] * math.exp(rng.gauss(0, 0.45))
    if status == 304:
        ms *= 0.3
    if status >= 500:
        ms *= 2.5
    if overloaded:
        ms *= 1.6
    return max(1, int(ms))


def enrich(source: str, doc: dict, rng: random.Random) -> dict:
    ts = doc["_ts"]
    if "host" in doc:
        host = doc["host"]
        market = HOST_TO_MARKET[host]
    else:  # AWS logs have no host: assign a market (simulated)
        market = weighted(rng, [(m, c["aws_share"]) for m, c in MARKETS.items()])
        host = MARKETS[market]["host"]
    path = doc["path"]
    page_type, directory = classify_path(path)
    family = bot_family(doc.get("user_agent"))
    status = int(doc["status"])

    # Before the rule, the scraper overloads the French catalogue: slower, more 503s.
    overloaded = (
        host == INCIDENT_HOST and page_type in ("product", "product_image")
        and SCRAPER_START <= ts < RULE_DEPLOYED
    )
    if overloaded and status == 200 and rng.random() < 0.03:
        status = 503

    row = {
        "log_id": doc["log_id"],
        "ts": ts,
        "source": source,
        "ip": doc.get("ip"),
        "method": doc.get("method"),
        "path": path,
        "protocol": doc.get("protocol"),
        "status": status,
        "origin_status": status,
        "bytes_sent": doc.get("bytes_sent"),
        "user_agent": doc.get("user_agent"),
        "referer": doc.get("referer"),
        "market": market,
        "host": host,
        "application_pool": f"{'us' if market == 'us' else 'eu'}-{POOL_GROUP[page_type]}",
        "page_type": page_type,
        "directory": directory,
        "bot_family": family,
        "is_bot": None if family == "unknown" else family != "browser",
        "is_verified_bot": verify(doc.get("ip"), family),
        "cdn_rule_id": None,
        "blocked_reason": None,
    }

    blocked_reason = cdn_rule(row)
    if blocked_reason:
        row.update(status=403, bytes_sent=rng.randint(400, 700),
                   cdn_rule_id=CDN_RULE_ID, blocked_reason=blocked_reason)
    row["response_time_ms"] = response_time(rng, page_type, row["status"], overloaded, bool(blocked_reason))
    return row


def cdn_rule(row: dict):
    """CDN-FR-0412. v1 challenges any UA containing 'Googlebot' on FR product pages
    (the bug: no verified-bot exception). v2 only challenges unverified ones."""
    if row["host"] != INCIDENT_HOST or not row["path"].startswith(INCIDENT_PATH_PREFIX):
        return None
    if "googlebot" not in (row["user_agent"] or "").lower():
        return None
    if RULE_DEPLOYED <= row["ts"] < RULE_FIXED:
        return "ua_token_match"
    if row["ts"] >= RULE_FIXED and not row["is_verified_bot"]:
        return "unverified_bot"
    return None


CHANGE_EVENTS = [
    (SCRAPER_START + timedelta(minutes=35), "alert", None,
     "Latency alert on the FR catalogue",
     "p95 response time on www.example.fr/product/* above threshold."),
    (RULE_DEPLOYED, "cdn_deploy", CDN_RULE_ID,
     "CDN-FR-0412 v1 deployed",
     "Bot mitigation on www.example.fr/product/*: challenges any user agent containing 'Googlebot'. "
     "No exception for verified crawlers."),
    (RULE_FIXED, "cdn_fix", CDN_RULE_ID,
     "CDN-FR-0412 v2: verified-bot exception",
     "Requests from verified Googlebot IPs are no longer challenged."),
]


# ---------------------------------------------------------------------------
# Step 6: output
# ---------------------------------------------------------------------------

def load_postgres(database_url: str, rows: list, rejects: list, stats: dict) -> None:
    import psycopg

    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            conn.execute(schema)
            run_id = conn.execute(
                "INSERT INTO ingest_runs (started_at, seed, aws_records, lines_read) "
                "VALUES (%s, %s, %s, %s) RETURNING run_id",
                (stats["started_at"], SEED, AWS_RECORDS, stats["documents_read"] + len(rejects)),
            ).fetchone()[0]
            with conn.cursor() as cur:
                with cur.copy(f"COPY access_logs ({', '.join(COLUMNS)}) FROM STDIN") as copy:
                    for r in rows:
                        copy.write_row([r[c] for c in COLUMNS])
                cur.executemany(
                    "INSERT INTO change_events (event_time, event_type, cdn_rule_id, title, description) "
                    "VALUES (%s, %s, %s, %s, %s)", CHANGE_EVENTS)
                cur.executemany(
                    "INSERT INTO ingest_rejects (run_id, source_file, line_number, reason, raw_line) "
                    "VALUES (%s, %s, %s, %s, %s)", [(run_id, *r) for r in rejects])
            conn.execute(
                "UPDATE ingest_runs SET finished_at = now(), rows_loaded = %s, duplicates_dropped = %s, "
                "rejected_lines = %s, data_min_ts = %s, data_max_ts = %s WHERE run_id = %s",
                (len(rows), stats["duplicates"], len(rejects), stats["min_ts"], stats["max_ts"], run_id),
            )
            conn.execute("ANALYZE access_logs")


def write_csv(rows: list) -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "access_logs.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "ts": iso(r["ts"])})
    return out


def print_summary(rows: list, rejects: list, stats: dict) -> None:
    print(f"\nRows loaded:          {len(rows):,}")
    print(f"Duplicates dropped:   {stats['duplicates']:,}")
    print(f"Rejected lines:       {len(rejects):,}  {dict(Counter(r[2] for r in rejects))}")
    print(f"Time range (UTC):     {iso(stats['min_ts'])} to {iso(stats['max_ts'])}")

    def phase(ts):
        if ts < RULE_DEPLOYED:
            return "1 before rule"
        if ts < RULE_FIXED:
            return "2 incident"
        return "3 after fix"

    ok, total = Counter(), Counter()
    for r in rows:
        if r["host"] == INCIDENT_HOST and r["page_type"] == "product" and r["bot_family"] == "googlebot" and r["is_verified_bot"]:
            p = phase(r["ts"])
            total[p] += 1
            ok[p] += r["status"] < 400
    print("\nVerified Googlebot on www.example.fr product pages (success = status below 400):")
    for p in sorted(total):
        print(f"  {p[2:]:<14} {total[p]:>6,} requests   success rate {ok[p] / total[p]:6.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="write a CSV instead of loading the database")
    args = parser.parse_args()

    database_url = None
    if not args.dry_run:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            sys.exit("DATABASE_URL is missing. Copy .env.example to .env and paste your connection string.")

    stats = {"started_at": datetime.now(UTC)}
    rng = random.Random(SEED)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print("Step 1/4  Generating AWS sample logs")
    generate_aws_logs()
    print("Step 2/4  Generating the simulated crawl layer")
    n_crawl = generate_crawl_layer(rng)
    print(f"          {n_crawl:,} crawl-layer requests")

    print("Step 3/4  Parsing, validating, enriching")
    rejects, rows, seen = [], [], set()
    duplicates = documents = 0
    for path, source in ((AWS_RAW_FILE, "aws_generator"), (CRAWL_RAW_FILE, "crawl_simulation")):
        for src, doc in read_documents(path, source, rejects):
            documents += 1
            if doc["log_id"] in seen:
                duplicates += 1
                continue
            seen.add(doc["log_id"])
            rows.append(enrich(src, doc, rng))
    rows.sort(key=lambda r: r["ts"])
    stats.update(duplicates=duplicates, documents_read=documents,
                 min_ts=rows[0]["ts"], max_ts=rows[-1]["ts"])

    if args.dry_run:
        out = write_csv(rows)
        print(f"Step 4/4  Dry run: wrote {out.relative_to(ROOT)}")
    else:
        print("Step 4/4  Loading into PostgreSQL (this can take a minute)")
        load_postgres(database_url, rows, rejects, stats)
    print_summary(rows, rejects, stats)
    print("\nDone.")


if __name__ == "__main__":
    main()
