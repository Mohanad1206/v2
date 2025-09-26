#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming Accessories Scraper — text-only output

Features
- Handles static (httpx) and dynamic (Playwright) fetching automatically.
- Outputs plain .txt (one product per line), not CSV.
- Limits to first N products per site (default 50).
- Robust error handling, retry logic, and sane timeouts.
- Heuristic extraction (price, name, availability, product URL) + optional site-specific selectors via config.yaml.
- Logs to logs/scrape.log for debugging.

Usage
------
1) Install deps:
   python -m venv .venv && . .venv/bin/activate  # (Windows: .venv\Scripts\activate)
   pip install -r requirements.txt
   playwright install

2) Run:
   python scraper.py --first-n 50 --dynamic auto --sites sites.txt --out-dir output

Notes
- "--dynamic auto" tries httpx first, then falls back to Playwright when needed.
- Use "--dynamic always" to force dynamic rendering for all pages (slower, more robust).
- Use "--static-only" to skip Playwright entirely.

Output
------
output/YYYYmmdd_HHMMSS_scrape.txt  (UTF-8 text)
Each line (pipe-separated) is:
timestamp_iso | site_host | product_name | status | price_value | currency | product_url | raw_price_text
"""

import argparse, asyncio, re, sys, json, time, os, pathlib, datetime, logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
import httpx
from bs4 import BeautifulSoup
from selectolax.parser import HTMLParser
from tenacity import retry, stop_after_attempt, wait_fixed

# ---------------- Logging ----------------
LOGS_DIR = pathlib.Path("logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOGS_DIR / "scrape.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)

# ---------------- Regex ----------------
PRICE_RE = re.compile(r"(EGP|ج\.م|LE|جنيه)\s*[\d,.]+|[\d,.]+\s*(EGP|ج\.م|LE|جنيه)", re.IGNORECASE)
CURRENCY_MAP = {"EGP": "EGP", "LE": "EGP", "ج.م": "EGP", "جنيه": "EGP"}
AVAIL_OK = re.compile(r"(in stock|available|متاح|متوفّر|مُتاح)", re.IGNORECASE)
AVAIL_NO = re.compile(r"(out of stock|sold out|غير متاح|نفدت الكمية|غير متوفر)", re.IGNORECASE)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en,ar;q=0.9"
}

@dataclass
class Product:
    name: str
    url: str
    price_value: Optional[float]
    currency: Optional[str]
    raw_price_text: str
    status: str

def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat()

def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def parse_price(text: str) -> Tuple[Optional[float], Optional[str], str]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None, None, ""
    raw = m.group(0)
    # extract digits
    digits = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        val = float(digits) if digits else None
    except:
        val = None
    curr = None
    for k,v in CURRENCY_MAP.items():
        if k.lower() in raw.lower():
            curr = v
            break
    if not curr:
        curr = "EGP"  # default guess for Egyptian sites
    return val, curr, raw

def guess_availability(text: str) -> str:
    if AVAIL_NO.search(text or ""):
        return "Out of stock"
    if AVAIL_OK.search(text or ""):
        return "Available"
    return "Unknown"

def make_httpx_client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True)

@retry(stop=stop_after_attempt(2), wait=wait_fixed(1))
def fetch_static(url: str) -> str:
    with make_httpx_client() as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text

async def fetch_dynamic(url: str, wait_ms: int = 1200) -> str:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(java_script_enabled=True, user_agent=HEADERS["User-Agent"])
        await page.goto(url, timeout=30000)
        await page.wait_for_timeout(wait_ms)
        html = await page.content()
        await browser.close()
        return html

def discover_product_links(base_url: str, html: str, include_paths: List[str]) -> List[str]:
    # Look for anchor tags that include keywords in URL or have nearby prices
    parser = HTMLParser(html)
    links = set()
    for a in parser.css("a"):
        href = a.attributes.get("href") or ""
        if not href or href.startswith("#") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        full = httpx.URL(base_url).join(href).human_repr()
        # Filter by include_paths if provided
        if include_paths and not any(p in full for p in include_paths):
            continue
        # Heuristic: if anchor text or surrounding text contains a price → likely a product
        txt = (a.text() or "") + " " + (a.parent.text() if a.parent else "")
        if PRICE_RE.search(txt) or any(k in full.lower() for k in ["/product", "/products", "/item", "/p/", "/sku", "/collections", "/category"]):
            links.add(full)
    return list(links)

def extract_from_card(card) -> Optional[Product]:
    text = card.get_text(" ", strip=True)
    price_val, curr, raw_price = parse_price(text)
    # Try to find a clickable name and URL
    a = card.find("a", href=True)
    url = a["href"] if a else ""
    name = ""
    if a and a.get_text(strip=True):
        name = a.get_text(strip=True)
    else:
        # Fallback: header tag text
        h = card.find(["h1", "h2", "h3", "h4", "h5"])
        if h:
            name = h.get_text(strip=True)
    status = guess_availability(text)
    if not url and not name and not raw_price:
        return None
    return Product(name=norm_space(name), url=url, price_value=price_val, currency=curr, raw_price_text=raw_price, status=status)

def extract_products(html: str, base_url: str) -> List[Product]:
    soup = BeautifulSoup(html, "lxml")
    products: List[Product] = []

    # Common product card containers to try
    selectors = [
        ".product-item", ".product", ".grid-product", ".card-product", ".product-card", ".product-grid-item",
        "li.product", "article.product", "div[class*=product]", "div[class*=card]"
    ]
    for sel in selectors:
        for card in soup.select(sel):
            p = extract_from_card(card)
            if p:
                # absolutize URL
                if p.url:
                    p.url = httpx.URL(base_url).join(p.url).human_repr()
                products.append(p)

    # If nothing found, try link-based heuristic with price nearby
    if not products:
        for a in soup.find_all("a", href=True):
            context = a.get_text(" ", strip=True) + " " + (a.find_parent().get_text(" ", strip=True) if a.find_parent() else "")
            if PRICE_RE.search(context):
                url = httpx.URL(base_url).join(a["href"]).human_repr()
                name = a.get_text(strip=True) or "N/A"
                pv, curr, raw = parse_price(context)
                status = guess_availability(context)
                products.append(Product(name=norm_space(name), url=url, price_value=pv, currency=curr, raw_price_text=raw, status=status))

    # Deduplicate by URL
    uniq = {}
    for p in products:
        key = p.url or p.name
        if key and key not in uniq:
            uniq[key] = p
    # Normalize empty currency if price exists
    final = []
    for p in uniq.values():
        if p.price_value and not p.currency:
            p.currency = "EGP"
        final.append(p)
    return final

def load_config(path="config.yaml") -> Dict[str, dict]:
    try:
        import yaml
    except Exception:
        return {}
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # Index by host
    out = {}
    for item in (data.get("sites") or []):
        host = (item.get("host") or "").replace("www.", "").strip()
        if host:
            out[host] = item
    return out

def host_of(url: str) -> str:
    try:
        return httpx.URL(url).host.replace("www.", "")
    except Exception:
        return ""

async def fetch_html(url: str, mode: str) -> str:
    try:
        if mode == "static":
            return fetch_static(url)
        elif mode == "always":
            return await fetch_dynamic(url)
        elif mode == "auto":
            # Try static first; if too small or no prices → fallback to dynamic
            html = ""
            try:
                html = fetch_static(url)
            except Exception as e:
                logging.info(f"Static fetch failed for {url}: {e}. Falling back to dynamic.")
                return await fetch_dynamic(url)
            if len(html) < 30000 or not PRICE_RE.search(html):
                try:
                    logging.info(f"Static content seems thin/no-price for {url}; trying dynamic.")
                    dyn = await fetch_dynamic(url)
                    if len(dyn) > len(html):
                        return dyn
                except Exception as e:
                    logging.info(f"Dynamic fetch failed for {url}: {e}. Keeping static.")
            return html
        else:
            return fetch_static(url)
    except Exception as e:
        logging.error(f"Fetch failed for {url}: {e}")
        return ""

async def process_site(url: str, args, cfg_by_host: Dict[str, dict], out_fp):
    t0 = time.time()
    base_host = host_of(url)
    cfg = cfg_by_host.get(base_host, {})
    include_paths = cfg.get("include_paths", []) if cfg else []
    mode = "static" if args.static_only else ("always" if args.dynamic == "always" else "auto")

    logging.info(f"[{base_host}] Fetching ({mode}) → {url}")
    html = await fetch_html(url, "static" if args.static_only else ("always" if args.dynamic == "always" else "auto"))
    if not html:
        logging.warning(f"[{base_host}] Empty HTML")
        return

    # Discover candidate product links if the landing page is a homepage; else treat as product/category directly
    candidates = discover_product_links(url, html, include_paths)
    if not candidates:
        candidates = [url]

    collected = 0
    for link in candidates[: args.first_n]:
        html2 = await fetch_html(link, mode)
        if not html2:
            continue
        products = extract_products(html2, link)
        for p in products:
            line = " | ".join([
                now_iso(),
                base_host or "unknown",
                p.name or "N/A",
                p.status or "Unknown",
                f"{p.price_value:.2f}" if p.price_value is not None else "N/A",
                p.currency or "N/A",
                p.url or link,
                p.raw_price_text or "N/A",
            ])
            out_fp.write(line + "\n")
            collected += 1
        if collected >= args.first_n:
            break

    dt = time.time() - t0
    logging.info(f"[{base_host}] Wrote {collected} products in {dt:.1f}s")

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", type=str, default="sites.txt", help="Path to file with one URL per line")
    ap.add_argument("--out-dir", type=str, default="output", help="Directory to write .txt output")
    ap.add_argument("--first-n", type=int, default=50, help="Max products per site")
    ap.add_argument("--dynamic", type=str, default="auto", choices=["auto", "always"], help="Dynamic rendering behavior")
    ap.add_argument("--static-only", action="store_true", help="Force static only (no Playwright)")
    return ap.parse_args()

async def main():
    args = parse_args()
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{ts}_scrape.txt"
    cfg_by_host = load_config("config.yaml")

    with open(args.sites, "r", encoding="utf-8") as f:
        urls = [u.strip() for u in f if u.strip() and not u.strip().startswith("#")]

    with open(out_path, "w", encoding="utf-8") as out_fp:
        out_fp.write("timestamp_iso | site_name | product_name | status | price_value | currency | product_url | raw_price_text\n")
        for url in urls:
            try:
                await process_site(url, args, cfg_by_host, out_fp)
            except Exception as e:
                logging.exception(f"Unhandled error for {url}: {e}")

    print(f"Wrote text output to: {out_path}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Aborted by user.")
