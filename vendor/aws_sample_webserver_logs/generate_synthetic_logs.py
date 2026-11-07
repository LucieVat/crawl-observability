#!/usr/bin/env python3
"""
Generate a synthetic 10K web server log dataset formatted as an OpenSearch bulk index file.
This replaces the original zanbil.ir dataset to avoid licensing concerns.
All data is fabricated - no real user data is included.
"""

import json
import random
import string
from datetime import datetime, timedelta

# Configuration
NUM_RECORDS = 10000
OUTPUT_FILE = "synthetic-webserver-logs-10k.txt"
INDEX_NAME = "webserver-logs"

# --- Synthetic data pools ---

# Fictional e-commerce site paths
STATIC_PATHS = [
    "/static/images/logo.png",
    "/static/images/banner-sale.jpg",
    "/static/images/icons/cart.svg",
    "/static/images/icons/search.svg",
    "/static/images/guarantees/freeShipping.png",
    "/static/images/guarantees/returns.png",
    "/static/images/guarantees/secure.png",
    "/static/css/main.min.css",
    "/static/css/fonts/roboto-regular.woff2",
    "/static/js/app.bundle.js",
    "/static/js/vendor.bundle.js",
    "/favicon.ico",
    "/apple-touch-icon.png",
    "/robots.txt",
    "/sitemap.xml",
]

API_PATHS = [
    "/api/v1/products",
    "/api/v1/products/search",
    "/api/v1/cart",
    "/api/v1/cart/items",
    "/api/v1/user/profile",
    "/api/v1/user/orders",
    "/api/v1/recommendations",
    "/api/v1/inventory/check",
    "/api/v1/shipping/estimate",
    "/api/v1/reviews",
]

BROWSE_PATHS = [
    "/browse/electronics",
    "/browse/home-appliances",
    "/browse/laptops",
    "/browse/smartphones",
    "/browse/tablets",
    "/browse/cameras",
    "/browse/audio",
    "/browse/gaming",
    "/browse/kitchen",
    "/browse/furniture",
    "/browse/clothing",
    "/browse/shoes",
    "/browse/sports",
    "/browse/books",
    "/browse/toys",
]

FILTER_PATHS = [
    "/filter/brand={brand}&category={cat}",
    "/filter/price=100-500&sort=popular",
    "/filter/price=500-1000&sort=newest",
    "/filter/rating=4&instock=true",
    "/filter/color=black&size=medium",
]

PRODUCT_PATH_TEMPLATES = [
    "/product/{pid}/detail",
    "/product/{pid}/reviews",
    "/image/{pid}/productModel/200x200",
    "/image/{pid}/productModel/100x100",
    "/image/{pid}/productModel/400x400",
    "/image/{pid}/thumbnail",
    "/image/{pid}/gallery/{n}",
]

PAGE_PATHS = [
    "/",
    "/about",
    "/contact",
    "/help",
    "/shipping-policy",
    "/return-policy",
    "/privacy",
    "/terms",
    "/deals",
    "/new-arrivals",
]

# User agents (realistic but generic)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.6099.119 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-A536B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.193 Mobile Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "Googlebot-Image/1.0",
    "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)",
]

# Referers (fictional)
REFERERS = [
    "https://www.example.com/",
    "https://www.example.com/browse/electronics",
    "https://www.example.com/browse/home-appliances",
    "https://www.example.com/deals",
    "https://www.example.com/search?q=laptop",
    "https://www.example.com/search?q=headphones",
    "https://www.example.com/cart",
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://www.facebook.com/",
    None,  # No referer
    None,
    None,
]

# HTTP methods with weights
METHODS = ["GET", "GET", "GET", "GET", "GET", "GET", "GET", "GET", "POST", "PUT", "DELETE", "HEAD"]

# Status codes with realistic distribution
STATUS_WEIGHTS = [
    (200, 65),
    (304, 15),
    (404, 8),
    (301, 4),
    (302, 3),
    (500, 2),
    (403, 2),
    (503, 1),
]

BRANDS = ["acme", "zenith", "nova", "apex", "pulse", "vertex", "orbit", "nexus"]
CATEGORIES = ["laptops", "phones", "tablets", "audio", "cameras", "tvs", "wearables"]


def generate_ip():
    """Generate a random IP address (avoiding reserved ranges)."""
    first = random.choice([i for i in range(1, 224) if i not in (10, 127, 169, 172, 192)])
    return f"{first}.{random.randint(1, 254)}.{random.randint(0, 254)}.{random.randint(1, 254)}"


def generate_timestamp(start_date, end_date):
    """Generate a random timestamp between start and end dates."""
    delta = end_date - start_date
    random_seconds = random.randint(0, int(delta.total_seconds()))
    dt = start_date + timedelta(seconds=random_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_path():
    """Generate a realistic request path."""
    roll = random.random()
    if roll < 0.25:
        return random.choice(STATIC_PATHS)
    elif roll < 0.40:
        return random.choice(API_PATHS)
    elif roll < 0.55:
        return random.choice(BROWSE_PATHS)
    elif roll < 0.65:
        return random.choice(PAGE_PATHS)
    elif roll < 0.80:
        pid = random.randint(10000, 99999)
        template = random.choice(PRODUCT_PATH_TEMPLATES)
        return template.format(pid=pid, n=random.randint(1, 5))
    else:
        template = random.choice(FILTER_PATHS)
        return template.format(brand=random.choice(BRANDS), cat=random.choice(CATEGORIES))


def generate_bytes_sent(status):
    """Generate realistic bytes_sent based on status code."""
    if status == 304:
        return 0
    elif status in (301, 302):
        return random.randint(0, 500)
    elif status in (403, 404):
        return random.randint(200, 5000)
    elif status in (500, 503):
        return random.randint(100, 2000)
    else:
        # 200 OK - varies by content type
        return random.choice([
            random.randint(200, 1000),      # Small API responses
            random.randint(1000, 10000),     # HTML pages
            random.randint(10000, 100000),   # Images/JS/CSS
            random.randint(2000, 50000),     # Medium content
        ])


def generate_status():
    """Generate a status code based on realistic distribution."""
    statuses, weights = zip(*STATUS_WEIGHTS)
    return random.choices(statuses, weights=weights, k=1)[0]


def generate_log_entry(log_id, start_date, end_date):
    """Generate a single synthetic log entry."""
    status = generate_status()
    method = random.choice(METHODS)
    
    # POST/PUT/DELETE are more likely on API paths
    if method in ("POST", "PUT", "DELETE"):
        path = random.choice(API_PATHS)
    else:
        path = generate_path()
    
    entry = {
        "log_id": str(log_id),
        "ip": generate_ip(),
        "timestamp": generate_timestamp(start_date, end_date),
        "method": method,
        "path": path,
        "protocol": "HTTP/1.1",
        "status": status,
        "bytes_sent": generate_bytes_sent(status),
        "user_agent": random.choice(USER_AGENTS),
    }
    
    # Add referer for ~60% of requests
    referer = random.choice(REFERERS)
    if referer:
        entry["referer"] = referer
    
    return entry


def main():
    random.seed(42)  # Reproducible output
    
    # Date range: 5 days of logs (similar to original)
    start_date = datetime(2024, 3, 18, 0, 0, 0)
    end_date = datetime(2024, 3, 22, 23, 59, 59)
    
    print(f"Generating {NUM_RECORDS} synthetic web server log entries...")
    
    with open(OUTPUT_FILE, "w") as f:
        # Write index creation command
        f.write("// Step 1: Create a new index\n\n")
        f.write(f"PUT /{INDEX_NAME}\n")
        f.write("{\n")
        f.write('  "settings": {\n')
        f.write('    "index": {\n')
        f.write('      "number_of_shards": 4,\n')
        f.write('      "number_of_replicas": 1\n')
        f.write("    }\n")
        f.write("  }\n")
        f.write("}\n\n")
        f.write("// Step 2: Populate index with synthetic webserver log data\n\n")
        f.write("POST /_bulk\n")
        
        # Generate log IDs (random 7-digit numbers, shuffled)
        log_ids = random.sample(range(1000000, 9999999), NUM_RECORDS)
        
        for i, log_id in enumerate(log_ids):
            entry = generate_log_entry(log_id, start_date, end_date)
            
            # Write bulk index action + document
            f.write(json.dumps({"index": {"_index": INDEX_NAME}}) + "\n")
            f.write(json.dumps(entry) + "\n")
            
            if (i + 1) % 1000 == 0:
                print(f"  Generated {i + 1}/{NUM_RECORDS} entries...")
    
    print(f"\nDone! Output written to: {OUTPUT_FILE}")
    print(f"File contains {NUM_RECORDS} log entries in OpenSearch bulk format.")


if __name__ == "__main__":
    main()
