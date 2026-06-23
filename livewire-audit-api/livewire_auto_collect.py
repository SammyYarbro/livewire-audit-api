"""
LiveWire Intelligence Solutions — Auto Audit Collector
========================================================

Given just a URL, this script auto-populates the technical sections of a
findings JSON file by:

  1. Fetching the page HTML
  2. Scanning for JSON-LD structured data (schema markup)
  3. Running on-page SEO checks (titles, meta, H1s, alt text, etc.)
  4. Calling Google PageSpeed Insights API (mobile + desktop)
  5. Checking robots.txt and sitemap.xml
  6. Calculating readiness scores
  7. Auto-suggesting a prioritized action plan

The script leaves the QUALITATIVE sections empty for you to fill in manually:
  - ai_visibility (you test the AI assistants yourself)
  - headline_finding (your sales judgment)
  - schema_notes (your interpretation)
  - tier recommendation and justification

USAGE
-----
    python livewire_auto_collect.py https://acmeplumbing.com --company "Acme Plumbing"

    # Then edit the resulting JSON for the qualitative sections, and run:
    python livewire_audit_generator.py findings_acme_plumbing_20260620.json

OPTIONAL ENV VAR
----------------
    GOOGLE_PAGESPEED_API_KEY    — set this for higher rate limits (free at
                                  https://developers.google.com/speed/docs/insights/v5/get-started)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# =============================================================================
# CONFIG
# =============================================================================
PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# Standard Chrome-on-Windows User-Agent. Swap this out if a site blocks it —
# any current browser UA from https://www.whatismybrowser.com/guides/the-latest-user-agent/
# will work as a drop-in replacement.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30

# Schema.org type → report category
SCHEMA_CATEGORIES = {
    "Organization": "Organization",
    "Corporation": "Organization",
    "LocalBusiness": "LocalBusiness",
    "Restaurant": "LocalBusiness",
    "Store": "LocalBusiness",
    "Plumber": "LocalBusiness",
    "Electrician": "LocalBusiness",
    "HVACBusiness": "LocalBusiness",
    "AutoRepair": "LocalBusiness",
    "Dentist": "LocalBusiness",
    "MedicalBusiness": "LocalBusiness",
    "ProfessionalService": "LocalBusiness",
    "Service": "Service",
    "Product": "Product",
    "FAQPage": "FAQPage",
    "Question": "FAQPage",
    "Review": "Review / AggregateRating",
    "AggregateRating": "Review / AggregateRating",
    "BreadcrumbList": "BreadcrumbList",
    "Person": "Person (owner / staff)",
}

REPORT_SCHEMA_TYPES = [
    "Organization",
    "LocalBusiness",
    "Service",
    "Product",
    "FAQPage",
    "Review / AggregateRating",
    "BreadcrumbList",
    "Person (owner / staff)",
]

SCHEMA_IMPACT = {
    "Organization": "High",
    "LocalBusiness": "High",
    "Service": "High",
    "Product": "Med",
    "FAQPage": "Med",
    "Review / AggregateRating": "High",
    "BreadcrumbList": "Low",
    "Person (owner / staff)": "Med",
}


# =============================================================================
# UTILITIES
# =============================================================================
def normalize_url(url):
    """Add https:// if missing, strip trailing slash for consistency."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


def slugify(text):
    return re.sub(r"[^a-zA-Z0-9]+", "_", str(text)).strip("_").lower() or "client"


def print_step(num, total, message):
    print(f"[{num}/{total}] {message:<40}", end="", flush=True)


def print_result(symbol, detail):
    color = {"✓": "\033[92m", "✗": "\033[91m", "⚠": "\033[93m"}.get(symbol, "")
    reset = "\033[0m"
    print(f"{color}{symbol} {detail}{reset}")


# =============================================================================
# STEP 1: FETCH SITE HTML
# =============================================================================
def fetch_site(url):
    """Fetch the page, return (html, final_url, soup)."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT,
                            allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return resp.text, resp.url, soup
    except requests.exceptions.RequestException as e:
        print_result("✗", f"Fetch failed: {e}")
        sys.exit(1)


# =============================================================================
# STEP 2: SCHEMA DETECTION
# =============================================================================
def detect_schema(soup):
    """Find all JSON-LD blocks and categorize them."""
    found_types = set()
    raw_blocks = []

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            raw_blocks.append(data)
            _walk_for_types(data, found_types)
        except (json.JSONDecodeError, TypeError):
            continue

    # Also check for microdata as a fallback
    microdata_items = soup.find_all(attrs={"itemtype": True})
    for item in microdata_items:
        itemtype = item.get("itemtype", "")
        if "schema.org/" in itemtype:
            schema_type = itemtype.split("schema.org/")[-1].rstrip("/")
            found_types.add(schema_type)

    # Map to report categories
    report_coverage = []
    found_categories = {SCHEMA_CATEGORIES.get(t) for t in found_types
                        if t in SCHEMA_CATEGORIES}

    for cat in REPORT_SCHEMA_TYPES:
        present = cat in found_categories
        report_coverage.append({
            "type": cat,
            "present": present,
            "status": "Present" if present else "Missing",
            "impact": SCHEMA_IMPACT[cat],
        })

    return report_coverage, len(raw_blocks), found_types


def _walk_for_types(data, found):
    """Recursively walk a JSON-LD structure to find all @type values."""
    if isinstance(data, dict):
        t = data.get("@type")
        if isinstance(t, str):
            found.add(t)
        elif isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    found.add(x)
        for v in data.values():
            _walk_for_types(v, found)
    elif isinstance(data, list):
        for item in data:
            _walk_for_types(item, found)


# =============================================================================
# STEP 3: ON-PAGE SEO CHECKS
# =============================================================================
def check_seo(soup, url):
    """Run on-page SEO checks. Returns list of finding dicts."""
    findings = []

    # Title
    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    if not title_text:
        findings.append({
            "element": "Title tag",
            "finding": "Missing title tag entirely",
            "severity": "High",
        })
    elif len(title_text) < 30:
        findings.append({
            "element": "Title tag",
            "finding": f"Title is too short ({len(title_text)} chars) — should be 50-60",
            "severity": "Med",
        })
    elif len(title_text) > 70:
        findings.append({
            "element": "Title tag",
            "finding": f"Title is too long ({len(title_text)} chars) — will be truncated in SERPs",
            "severity": "Med",
        })
    else:
        findings.append({
            "element": "Title tag",
            "finding": f"Present ({len(title_text)} chars) — review for keyword targeting",
            "severity": "Low",
        })

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    desc_content = (meta_desc.get("content", "").strip() if meta_desc else "")
    if not desc_content:
        findings.append({
            "element": "Meta description",
            "finding": "Missing meta description",
            "severity": "High",
        })
    elif len(desc_content) < 70:
        findings.append({
            "element": "Meta description",
            "finding": f"Too short ({len(desc_content)} chars) — should be 140-160",
            "severity": "Med",
        })
    else:
        findings.append({
            "element": "Meta description",
            "finding": f"Present ({len(desc_content)} chars)",
            "severity": "Low",
        })

    # H1 hierarchy
    h1_tags = soup.find_all("h1")
    if len(h1_tags) == 0:
        findings.append({
            "element": "H1 heading",
            "finding": "No H1 tag found on page",
            "severity": "High",
        })
    elif len(h1_tags) > 1:
        findings.append({
            "element": "H1 heading",
            "finding": f"Multiple H1 tags found ({len(h1_tags)}) — should have only one",
            "severity": "Med",
        })
    else:
        findings.append({
            "element": "H1 heading",
            "finding": "Single H1 present (correct)",
            "severity": "Low",
        })

    # Image alt text
    images = soup.find_all("img")
    if images:
        with_alt = sum(1 for img in images if img.get("alt", "").strip())
        pct = (with_alt / len(images)) * 100
        if pct < 50:
            severity = "High"
        elif pct < 80:
            severity = "Med"
        else:
            severity = "Low"
        findings.append({
            "element": "Image alt text",
            "finding": f"{with_alt} of {len(images)} images have alt text ({pct:.0f}%)",
            "severity": severity,
        })

    # HTTPS
    is_https = url.startswith("https://")
    findings.append({
        "element": "HTTPS / SSL",
        "finding": "Site uses HTTPS" if is_https else "Site is NOT using HTTPS",
        "severity": "Low" if is_https else "High",
    })

    # Open Graph (social sharing)
    og_title = soup.find("meta", property="og:title")
    if not og_title:
        findings.append({
            "element": "Open Graph tags",
            "finding": "Missing Open Graph metadata for social sharing",
            "severity": "Med",
        })

    # Google Business Profile placeholder (cannot auto-detect)
    findings.append({
        "element": "Google Business Profile",
        "finding": "[Manual check required — verify claim status, photos, categories, reviews]",
        "severity": "High",
    })

    return findings


# =============================================================================
# STEP 4: ROBOTS.TXT + SITEMAP
# =============================================================================
def check_crawl_files(base_url):
    """Check robots.txt and sitemap.xml. Returns list of findings."""
    findings = []
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # robots.txt
    try:
        r = requests.get(f"{origin}/robots.txt", timeout=10,
                         headers={"User-Agent": USER_AGENT})
        if r.status_code == 200 and r.text.strip():
            findings.append({
                "element": "Robots.txt",
                "finding": "Present and accessible",
                "severity": "Low",
            })
        else:
            findings.append({
                "element": "Robots.txt",
                "finding": f"Missing or empty (HTTP {r.status_code})",
                "severity": "Med",
            })
    except requests.exceptions.RequestException:
        findings.append({
            "element": "Robots.txt",
            "finding": "Could not fetch robots.txt",
            "severity": "Med",
        })

    # sitemap.xml
    try:
        r = requests.get(f"{origin}/sitemap.xml", timeout=10,
                         headers={"User-Agent": USER_AGENT})
        if r.status_code == 200 and "<urlset" in r.text.lower():
            url_count = r.text.lower().count("<url>")
            findings.append({
                "element": "Sitemap.xml",
                "finding": f"Present with ~{url_count} URLs listed",
                "severity": "Low",
            })
        else:
            findings.append({
                "element": "Sitemap.xml",
                "finding": f"Missing or invalid (HTTP {r.status_code})",
                "severity": "Med",
            })
    except requests.exceptions.RequestException:
        findings.append({
            "element": "Sitemap.xml",
            "finding": "Could not fetch sitemap.xml",
            "severity": "Med",
        })

    return findings


# =============================================================================
# STEP 5: PAGESPEED INSIGHTS
# =============================================================================
def run_pagespeed(url, strategy="mobile"):
    """Call PageSpeed Insights API for the given strategy."""
    params = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
    }
    api_key = os.environ.get("GOOGLE_PAGESPEED_API_KEY")
    if api_key:
        params["key"] = api_key

    try:
        r = requests.get(PAGESPEED_API, params=params, timeout=90)
        r.raise_for_status()
        data = r.json()
        return _parse_pagespeed(data, strategy)
    except requests.exceptions.RequestException as e:
        return {"error": str(e), "strategy": strategy}


def _parse_pagespeed(data, strategy):
    """Extract the metrics we care about."""
    result = {"strategy": strategy, "error": None}
    try:
        lh = data["lighthouseResult"]
        result["score"] = int(round(lh["categories"]["performance"]["score"] * 100))

        audits = lh["audits"]
        result["lcp"] = audits.get("largest-contentful-paint", {}).get("displayValue", "—")
        result["cls"] = audits.get("cumulative-layout-shift", {}).get("displayValue", "—")
        result["tbt"] = audits.get("total-blocking-time", {}).get("displayValue", "—")
        result["fcp"] = audits.get("first-contentful-paint", {}).get("displayValue", "—")
        result["si"] = audits.get("speed-index", {}).get("displayValue", "—")

        # Raw numeric values for pass/fail
        result["lcp_seconds"] = audits.get("largest-contentful-paint", {}).get("numericValue", 0) / 1000.0
        result["cls_value"] = audits.get("cumulative-layout-shift", {}).get("numericValue", 0)
        result["tbt_ms"] = audits.get("total-blocking-time", {}).get("numericValue", 0)
    except (KeyError, TypeError) as e:
        result["error"] = f"Could not parse PageSpeed response: {e}"
    return result


def build_performance_section(mobile, desktop):
    """Combine mobile+desktop into the report's performance section."""
    rows = []

    def status(metric, value, threshold, lower_is_better=True):
        if value is None or metric.get("error"):
            return "—"
        try:
            v = float(value)
            t = float(threshold)
            if lower_is_better:
                return "Pass" if v < t else "Fail"
            return "Pass" if v >= t else "Fail"
        except (ValueError, TypeError):
            return "—"

    if not mobile.get("error"):
        rows.append({
            "metric": "Largest Contentful Paint (LCP) [mobile]",
            "score": mobile.get("lcp", "—"),
            "target": "< 2.5s",
            "status": status(mobile, mobile.get("lcp_seconds"), 2.5),
        })
        rows.append({
            "metric": "Cumulative Layout Shift (CLS) [mobile]",
            "score": mobile.get("cls", "—"),
            "target": "< 0.1",
            "status": status(mobile, mobile.get("cls_value"), 0.1),
        })
        rows.append({
            "metric": "Total Blocking Time (TBT) [mobile]",
            "score": mobile.get("tbt", "—"),
            "target": "< 200ms",
            "status": status(mobile, mobile.get("tbt_ms"), 200),
        })
        rows.append({
            "metric": "Mobile PageSpeed Score",
            "score": str(mobile.get("score", "—")),
            "target": "> 80",
            "status": "Pass" if mobile.get("score", 0) > 80 else "Fail",
        })

    if not desktop.get("error"):
        rows.append({
            "metric": "Desktop PageSpeed Score",
            "score": str(desktop.get("score", "—")),
            "target": "> 90",
            "status": "Pass" if desktop.get("score", 0) > 90 else "Fail",
        })

    return rows


# =============================================================================
# SCORE CALCULATION
# =============================================================================
def calculate_scores(schema_coverage, seo_findings, performance, mobile, desktop):
    """Calculate the four headline scores."""
    # Schema score: % of types present, weighted by impact
    weight_map = {"High": 3, "Med": 2, "Low": 1}
    total_weight = sum(weight_map[s["impact"]] for s in schema_coverage)
    earned = sum(weight_map[s["impact"]] for s in schema_coverage if s["present"])
    schema_score = int(round((earned / total_weight) * 100)) if total_weight else 0

    # SEO score: penalize per severity
    seo_score = 100
    for f in seo_findings:
        if f["severity"] == "High":
            seo_score -= 15
        elif f["severity"] == "Med":
            seo_score -= 7
    seo_score = max(0, seo_score)

    # Speed score: average of mobile + desktop, mobile-weighted
    m_score = mobile.get("score") or 0
    d_score = desktop.get("score") or 0
    if m_score and d_score:
        speed_score = int(round(m_score * 0.6 + d_score * 0.4))
    else:
        speed_score = m_score or d_score or 0

    # AI Search score: weighted blend (schema is the strongest signal)
    ai_score = int(round(schema_score * 0.6 + seo_score * 0.3 + speed_score * 0.1))

    return {
        "ai_search": ai_score,
        "seo": seo_score,
        "schema": schema_score,
        "speed": speed_score,
    }


# =============================================================================
# AUTO-SUGGESTED ACTION PLAN
# =============================================================================
def build_action_plan(schema_coverage, seo_findings, performance, scores):
    """Generate a suggested action plan based on findings."""
    critical, high, ongoing = [], [], []

    # Critical: missing high-impact schema
    missing_high_schema = [s["type"] for s in schema_coverage
                            if not s["present"] and s["impact"] == "High"]
    if missing_high_schema:
        types_str = ", ".join(missing_high_schema)
        critical.append(
            f"Implement JSON-LD schema for: {types_str} (highest ROI fix — "
            "directly impacts AI assistant recognition)"
        )

    # Critical: missing title or meta on key elements
    for f in seo_findings:
        if f["severity"] == "High" and "Title" in f["element"]:
            critical.append(f"Fix title tag issue: {f['finding']}")
        if f["severity"] == "High" and "Meta description" in f["element"]:
            critical.append(f"Add meta descriptions across all pages")
        if f["severity"] == "High" and "Google Business" in f["element"]:
            critical.append(
                "Complete Google Business Profile: claim, verify, add categories, "
                "20+ photos, and service area"
            )

    # High: medium-impact schema
    missing_med_schema = [s["type"] for s in schema_coverage
                           if not s["present"] and s["impact"] == "Med"]
    if missing_med_schema:
        types_str = ", ".join(missing_med_schema)
        high.append(f"Add JSON-LD schema for: {types_str}")

    # High: image alt text
    for f in seo_findings:
        if "Image alt text" in f["element"] and f["severity"] in ("High", "Med"):
            high.append("Add descriptive alt text to all images (accessibility + SEO)")
        if "H1" in f["element"] and f["severity"] in ("High", "Med"):
            high.append(f"Fix H1 hierarchy: {f['finding']}")

    # High: performance issues
    for p in performance:
        if p["status"] == "Fail" and "LCP" in p["metric"]:
            high.append(
                "Improve Largest Contentful Paint: compress hero images, lazy-load "
                "below-fold images, and ensure critical CSS is inlined"
            )
        if p["status"] == "Fail" and "CLS" in p["metric"]:
            high.append(
                "Fix Cumulative Layout Shift: set explicit width/height on images "
                "and reserve space for ads/embeds"
            )
        if p["status"] == "Fail" and "Mobile PageSpeed" in p["metric"]:
            high.append("Improve mobile performance score (currently below 80)")

    # Ongoing
    ongoing.append(
        "Monitor AI assistant mentions monthly across ChatGPT, Claude, Gemini, "
        "and Perplexity for your category and location"
    )
    ongoing.append(
        "Publish service-area landing pages and FAQs targeting long-tail "
        "questions your customers actually ask"
    )
    ongoing.append(
        "Refresh sitemap.xml and re-submit to Google Search Console quarterly"
    )
    ongoing.append(
        "Track Core Web Vitals monthly and address any regressions immediately"
    )

    # Ensure we have something in each tier
    if not critical:
        critical.append("[No critical issues auto-detected — review findings above]")
    if not high:
        high.append("[No high-priority issues auto-detected]")

    return {"critical": critical, "high": high, "ongoing": ongoing}


# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Auto-collect technical audit data for a prospect website."
    )
    parser.add_argument("url", help="Prospect website URL")
    parser.add_argument("--company", "-c", required=True,
                        help="Prospect company name (used in report)")
    parser.add_argument("--output", "-o", default=".",
                        help="Output directory for findings JSON (default: current dir)")
    parser.add_argument("--report-number", default=None,
                        help="Override report number (default: auto-generated)")
    args = parser.parse_args()

    url = normalize_url(args.url)
    print()
    print(f"\033[1m🔍 LiveWire Auto-Audit — {args.company}\033[0m")
    print(f"   {url}")
    print("─" * 62)

    # Step 1: Fetch HTML
    print_step(1, 5, "Fetching site HTML...")
    html, final_url, soup = fetch_site(url)
    size_kb = len(html) / 1024
    print_result("✓", f"{size_kb:.0f} KB fetched")

    # Step 2: Schema detection
    print_step(2, 5, "Scanning schema markup...")
    schema_coverage, jsonld_count, raw_types = detect_schema(soup)
    present_count = sum(1 for s in schema_coverage if s["present"])
    if present_count == 0:
        print_result("✗", "No structured data detected")
    elif present_count < 4:
        print_result("⚠", f"{present_count}/8 schema types present ({jsonld_count} JSON-LD blocks)")
    else:
        print_result("✓", f"{present_count}/8 schema types present ({jsonld_count} JSON-LD blocks)")

    # Step 3: SEO checks
    print_step(3, 5, "Running on-page SEO checks...")
    seo_findings = check_seo(soup, final_url)
    crawl_findings = check_crawl_files(final_url)
    all_seo = seo_findings + crawl_findings
    high_count = sum(1 for f in all_seo if f["severity"] == "High")
    if high_count == 0:
        print_result("✓", f"{len(all_seo)} checks — no critical issues")
    else:
        print_result("⚠", f"{len(all_seo)} checks — {high_count} critical")

    # Step 4: PageSpeed mobile
    print_step(4, 5, "Running mobile PageSpeed...")
    mobile = run_pagespeed(final_url, "mobile")
    if mobile.get("error"):
        print_result("⚠", f"Skipped: {mobile['error'][:50]}")
    else:
        print_result("✓", f"Score {mobile['score']}/100")

    # Step 5: PageSpeed desktop
    print_step(5, 5, "Running desktop PageSpeed...")
    desktop = run_pagespeed(final_url, "desktop")
    if desktop.get("error"):
        print_result("⚠", f"Skipped: {desktop['error'][:50]}")
    else:
        print_result("✓", f"Score {desktop['score']}/100")

    # Build sections
    performance = build_performance_section(mobile, desktop)
    scores = calculate_scores(schema_coverage, all_seo, performance, mobile, desktop)
    action_plan = build_action_plan(schema_coverage, all_seo, performance, scores)

    # Print results banner
    print()
    print("\033[1m📊 RESULTS\033[0m")
    for label, key in [("AI Search Readiness", "ai_search"),
                       ("SEO Health        ", "seo"),
                       ("Schema Coverage   ", "schema"),
                       ("Site Speed        ", "speed")]:
        s = scores[key]
        if s >= 80:
            icon, color = "🟢", "\033[92m"
        elif s >= 60:
            icon, color = "🟡", "\033[93m"
        else:
            icon, color = "🔴", "\033[91m"
        print(f"   {label}  {color}{s:>3}/100\033[0m  {icon}")

    # Build findings JSON
    report_number = args.report_number or f"LWIS-{datetime.now().strftime('%Y%m%d-%H%M')}"
    findings = {
        "client": {
            "company_name": args.company,
            "website": urlparse(final_url).netloc,
            "report_date": datetime.now().strftime("%m/%d/%Y"),
            "report_number": report_number,
        },
        "scores": scores,
        "headline_finding": (
            "[AUTO-DRAFT — REPLACE BEFORE SENDING] "
            f"{args.company} currently scores {scores['ai_search']}/100 on AI "
            "search readiness, with the most significant gap being in structured "
            "data coverage. Without proper schema markup, AI assistants like "
            "ChatGPT, Claude, and Gemini cannot reliably identify or recommend "
            "this business when potential customers ask for services in this "
            "category."
        ),
        "ai_visibility": [
            {"platform": "ChatGPT", "query": "[insert test query]",
             "mentioned": False, "notes": "[Manually test this AI and record results]"},
            {"platform": "Claude", "query": "[insert test query]",
             "mentioned": False, "notes": "[Manually test this AI and record results]"},
            {"platform": "Gemini", "query": "[insert test query]",
             "mentioned": False, "notes": "[Manually test this AI and record results]"},
            {"platform": "Perplexity", "query": "[insert test query]",
             "mentioned": False, "notes": "[Manually test this AI and record results]"},
        ],
        "schema_coverage": schema_coverage,
        "schema_notes": (
            f"Site contains {jsonld_count} JSON-LD block(s) covering "
            f"{present_count} of 8 priority schema types. "
            f"{'This is the single highest-impact, lowest-effort fix available. ' if present_count < 4 else ''}"
            "Detected schema types: " + (", ".join(sorted(raw_types)) if raw_types else "none.")
        ),
        "seo_findings": all_seo,
        "performance": performance,
        "action_plan": action_plan,
        "recommended_tier": "Growth" if scores["ai_search"] < 50 else "Starter",
        "tier_justification": (
            "[AUTO-DRAFT — REVIEW] Based on the breadth of issues identified "
            "above, the recommended tier balances immediate remediation with "
            "the 90-day window needed for AI assistant recognition to catch "
            "up to technical changes."
        ),
    }

    # Save
    os.makedirs(args.output, exist_ok=True)
    out_file = os.path.join(
        args.output,
        f"findings_{slugify(args.company)}_{datetime.now().strftime('%Y%m%d')}.json"
    )
    with open(out_file, "w") as f:
        json.dump(findings, f, indent=2)

    print()
    print(f"\033[92m✓ Findings saved:\033[0m {out_file}")
    print()
    print("\033[1mNEXT STEPS:\033[0m")
    print(f"  1. Open the JSON file and fill in the [AUTO-DRAFT] and [Manually test] fields:")
    print(f"     - headline_finding (your sales-grade summary)")
    print(f"     - ai_visibility (run live tests against ChatGPT, Claude, Gemini, Perplexity)")
    print(f"     - recommended_tier and tier_justification")
    print(f"     - Review the auto-generated action_plan")
    print(f"  2. Generate the PDF:")
    print(f"     python livewire_audit_generator.py {out_file} --output ./reports")
    print()


if __name__ == "__main__":
    main()
