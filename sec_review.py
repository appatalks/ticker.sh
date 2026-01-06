#!/usr/bin/env python3
"""
Stock Alert Monitor: AI-powered SEC filing analysis using GPT-4.1

Uses OpenAI GPT-4.1 to analyze SEC filings for any material events,
market-moving developments, or significant business changes.

Usage:
    python stock_alert.py TICKER [--report-dir DIR] [--no-alert]

Run via cron daily (or every 6 hours).
Sends alerts via: Email (SMTP) OR Slack webhook OR Pushover.

Requires: OPENAI_API_KEY in ~/.env
"""

from __future__ import annotations
import os
import sys
import re
import json
import time
import math
import html
import argparse
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple

# Conditional imports for optional dependencies
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# ----------------------------
# Config
# ----------------------------

# Load API keys from script directory .env first, then fall back to ~/.env
if load_dotenv:
    script_dir = Path(__file__).parent
    env_file = script_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv(Path.home() / ".env")

# OpenAI API setup
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
# AI verification is always enabled by default
USE_AI_VERIFICATION = os.getenv("USE_AI_VERIFICATION", "true").lower() != "false"
# Model to use for SEC filing analysis (default: gpt-4.1)
SEC_OPENAI_MODEL = os.getenv("SEC_OPENAI_MODEL", "gpt-4.1")

# Quiet mode for compact display (set at runtime)
QUIET_MODE = False
# Debug mode for verbose logging (set at runtime)
DEBUG_MODE = False

# Ticker will be set via command line (no default fallback)
TICKER = os.environ.get("STOCK_TICKER", None)

# EDGAR requires a descriptive User-Agent with contact
USER_AGENT = os.environ.get("EDGAR_USER_AGENT", "Stock Alert Monitor (contact: you@example.com)")

# Report artifacts go to system tmp (no more state caching)
REPORT_DIR = os.environ.get("REPORT_DIR", os.path.join(os.environ.get("TMPDIR", "/tmp"), "ticker_reports"))

# Webhook URL for alerts (optional)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

# SEC filing filters (configurable via .env)
ONLY_FORMS_STR = os.getenv("SEC_ONLY_FORMS", "8-K,10-Q,10-K")
ONLY_FORMS = set(form.strip() for form in ONLY_FORMS_STR.split(",") if form.strip())  # filings to monitor
MAX_FILINGS_TO_REVIEW = int(os.getenv("SEC_MAX_FILINGS", "3"))  # Review no more than N filings

# ----------------------------
# Helpers: HTTP
# ----------------------------

def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8", errors="ignore"))

def http_get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="ignore")

# ----------------------------
# EDGAR: ticker -> CIK, filings
# ----------------------------

def get_cik_for_ticker(ticker: str) -> str:
    # Official mapping JSON
    mapping = http_get_json("https://www.sec.gov/files/company_tickers.json")
    t = ticker.upper()
    for _, row in mapping.items():
        if row["ticker"].upper() == t:
            cik = str(row["cik_str"]).zfill(10)
            return cik
    raise RuntimeError(f"CIK not found for ticker {ticker}")

@dataclass
class Filing:
    accession: str
    form: str
    filed: str
    primary_doc: str

@dataclass
class FilingAlert:
    trigger_type: str  # "A", "B", "C+", "C-"
    title: str
    body: str
    filing_form: str
    filing_date: str
    accession: str
    ai_verified: bool = False
    ai_confidence: Optional[str] = None  # "high", "medium", "low"
    ai_interpretation: Optional[str] = None

@dataclass
class Report:
    ticker: str
    timestamp: str
    filings_checked: int
    new_filings: int
    alerts: List[FilingAlert] = field(default_factory=list)
    buy_signal: str = "NEUTRAL"  # "POSITIVE", "NEGATIVE", "NEUTRAL"
    buy_signal_reasons: List[str] = field(default_factory=list)

def get_recent_filings(cik10: str, limit: int = 30) -> List[Filing]:
    sub = http_get_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession = recent.get("accessionNumber", [])
    filed = recent.get("filingDate", [])
    primary = recent.get("primaryDocument", [])

    out: List[Filing] = []
    for i in range(min(limit, len(forms))):
        if forms[i] in ONLY_FORMS:
            out.append(Filing(
                accession=accession[i].replace("-", ""),
                form=forms[i],
                filed=filed[i],
                primary_doc=primary[i],
            ))
    return out

def filing_folder_index_json_url(cik10: str, accession_nodash: str) -> str:
    cik_nolead = str(int(cik10))
    return f"https://www.sec.gov/Archives/edgar/data/{cik_nolead}/{accession_nodash}/index.json"

def guess_text_doc_urls_from_index(cik10: str, accession_nodash: str) -> List[str]:
    idx = http_get_json(filing_folder_index_json_url(cik10, accession_nodash))
    files = idx.get("directory", {}).get("item", [])
    cik_nolead = str(int(cik10))
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_nolead}/{accession_nodash}/"
    # prefer .htm/.html and exhibit-looking docs
    candidates = []
    for f in files:
        name = f.get("name", "")
        if name.lower().endswith((".htm", ".html", ".txt")):
            candidates.append(base + name)
    # Put primary doc first if present
    return candidates

# ----------------------------
# Text extraction
# ----------------------------

def normalize_text(raw: str) -> str:
    # strip html tags lightly (good enough for AI analysis)
    raw = html.unescape(raw)
    raw = re.sub(r"<script.*?>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style.*?>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()

# ----------------------------
# Alerting
# ----------------------------

def send_alert(title: str, body: str) -> None:
    """Send alert via webhook if configured, otherwise print to stdout"""
    try:
        if not WEBHOOK_URL:
            # No webhook configured, print to stdout
            print(f"\n=== {title} ===\n{body}\n")
            return
        
        # Send to webhook as JSON payload
        payload = json.dumps({
            "title": title,
            "body": body,
            "timestamp": datetime.now().isoformat()
        }).encode("utf-8")
        
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=20).read()
        
    except Exception as e:
        print(f"⚠️  Alert failed: {e}")
        # Fall back to stdout on webhook failure
        print(f"\n=== {title} ===\n{body}\n")

# ----------------------------
# AI Analysis
# ----------------------------

def analyze_with_ai(ticker: str, filing_form: str, filing_date: str, filing_text: str, price_data: Optional[dict] = None) -> Dict[str, Any]:
    """
    Use AI to analyze SEC filing and optionally combine with price data.
    Returns dict with: signal (BUY/SELL/HOLD), confidence (1-10), reasoning (str), key_points (list)
    """
    if not openai_client or not USE_AI_VERIFICATION:
        return {
            "signal": "NEUTRAL",
            "confidence": 1,
            "reasoning": "AI analysis disabled - set OPENAI_API_KEY to enable",
            "key_points": ["AI verification not available"]
        }
    
    # Limit filing text to reasonable size for analysis
    text_sample = filing_text[:150000] if len(filing_text) > 150000 else filing_text
    
    # Build context about price action if provided
    price_context = ""
    if price_data:
        price_change = price_data.get('percentChange', 0)
        current_price = price_data.get('currentPrice', 0)
        price_context = f"""

Current Market Context:
- Stock Price: ${current_price:.2f}
- Daily Change: {price_change:+.2f}%
- Price Momentum: {'Positive' if price_change > 0 else 'Negative' if price_change < 0 else 'Flat'}

Consider whether the filing content aligns with or contradicts the market's price action.
"""
    
    prompt = f"""You are an expert financial analyst reviewing SEC filings to make investment recommendations.

Company: {ticker}
Filing: {filing_form} filed on {filing_date}{price_context}

Analysis Instructions:
1. Focus on MATERIAL events that would impact stock value:
   - Revenue/earnings changes and guidance
   - Major contracts, acquisitions, or partnerships  
   - Management changes (CEO, CFO departures/appointments)
   - Financial health indicators (debt, liquidity, cash flow)
   - Legal issues, regulatory problems, or risks
   - Strategic shifts or business model changes

2. Determine Investment Signal:
   - BUY: Strong positive catalysts, improving fundamentals, reduced risks
   - SELL: Significant negative developments, deteriorating fundamentals, increased risks
   - HOLD: Mixed signals, minor updates, or insufficient information

3. Assign Confidence (1-10):
   - 8-10: Clear, material developments with strong conviction
   - 5-7: Notable but moderate signals  
   - 1-4: Weak signals or high uncertainty

4. Provide Reasoning:
   - Be concise (2-3 sentences max)
   - Focus on the most important finding
   - Use plain language, avoid jargon
   - State the "so what" - why does this matter to investors?

Filing Content:
{text_sample}

Respond ONLY with valid JSON:
{{
  "signal": "BUY" or "SELL" or "HOLD",
  "confidence": integer 1-10,
  "reasoning": "concise explanation",
  "key_points": ["point 1", "point 2", "point 3"]
}}"""
    
    try:
        if not QUIET_MODE:
            print(f"  \ud83e\udd16 AI analyzing {filing_form} filed {filing_date}...")
        
        if DEBUG_MODE:
            print(f"\n=== SEC DEBUG: AI Analysis Request ===", file=sys.stderr)
            print(f"Ticker: {ticker}", file=sys.stderr)
            print(f"Filing: {filing_form} ({filing_date})", file=sys.stderr)
            print(f"Model: {SEC_OPENAI_MODEL}", file=sys.stderr)
            print(f"Text sample length: {len(text_sample):,} chars", file=sys.stderr)
            if price_data:
                print(f"Price data: ${price_data.get('currentPrice', 0):.2f} ({price_data.get('percentChange', 0):+.2f}%)", file=sys.stderr)
            print(f"\n--- Full Prompt/Payload ---", file=sys.stderr)
            print(f"{prompt}", file=sys.stderr)
            print(f"=" * 50, file=sys.stderr)
        
        response = openai_client.chat.completions.create(
            model=SEC_OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=500
        )
        
        content = response.choices[0].message.content.strip()
        result = json.loads(content)
        
        if DEBUG_MODE:
            print(f"\n=== SEC DEBUG: AI Response ===", file=sys.stderr)
            print(json.dumps(result, indent=2), file=sys.stderr)
            print(f"=" * 50 + "\n", file=sys.stderr)
            print(f"\n=== SEC DEBUG: AI Response ===", file=sys.stderr)
            print(json.dumps(result, indent=2), file=sys.stderr)
            print(f"=" * 50 + "\n", file=sys.stderr)
        
        return result
    
    except Exception as e:
        if not QUIET_MODE:
            print(f"  \u26a0\ufe0f  AI analysis failed: {e}")
        return {
            "signal": "NEUTRAL",
            "confidence": 1,
            "reasoning": f"AI analysis error: {str(e)[:100]}",
            "key_points": ["Analysis unavailable"]
        }

def analyze_filing(ticker: str, cik10: str, filing: Filing, price_data: Optional[dict] = None) -> Tuple[List[FilingAlert], Dict[str, Any]]:
    """
    Analyze a filing using AI only (no pattern matching).
    Returns (list_of_alerts, ai_analysis_dict).
    """
    if DEBUG_MODE:
        print(f"\n=== SEC DEBUG: Starting Analysis ===", file=sys.stderr)
        print(f"Ticker: {ticker}", file=sys.stderr)
        print(f"Filing: {filing.form} ({filing.filed})", file=sys.stderr)
        print(f"Accession: {filing.accession}", file=sys.stderr)
    
    alerts: List[FilingAlert] = []
    urls = guess_text_doc_urls_from_index(cik10, filing.accession)
    # Limit downloads to a handful
    urls = urls[:5]
    
    if DEBUG_MODE:
        print(f"Found {len(urls)} document URLs", file=sys.stderr)

    combined_text = ""
    for u in urls:
        try:
            raw = http_get_text(u)
        except Exception:
            continue
        txt = normalize_text(raw)
        if len(txt) > 2000:
            combined_text += " " + txt[:100000]  # cap per doc for speed
    
    if DEBUG_MODE:
        print(f"Combined text length: {len(combined_text):,} chars", file=sys.stderr)
        print(f"=" * 50, file=sys.stderr)

    if not combined_text:
        return alerts, {"signal": "NEUTRAL", "confidence": 1, "reasoning": "Could not fetch filing text", "key_points": []}

    # Use AI to analyze the filing
    ai_result = analyze_with_ai(ticker, filing.form, filing.filed, combined_text, price_data)
    
    # Create alert based on AI analysis
    signal = ai_result.get("signal", "HOLD")
    confidence = ai_result.get("confidence", 5)
    reasoning = ai_result.get("reasoning", "Analysis complete")
    key_points = ai_result.get("key_points", [])
    
    # Determine alert type based on signal
    if signal == "BUY":
        trigger_type = "BUY"
        title = f"{ticker} BUY Signal: {filing.form} filed {filing.filed}"
    elif signal == "SELL":
        trigger_type = "SELL"
        title = f"{ticker} SELL Signal: {filing.form} filed {filing.filed}"
    else:
        trigger_type = "HOLD"
        title = f"{ticker} HOLD: {filing.form} filed {filing.filed}"
    
    # Build alert body
    body_parts = [f"AI Analysis (Confidence: {confidence}/10):", reasoning]
    if key_points:
        body_parts.append("\nKey Points:")
        for point in key_points:
            body_parts.append(f"  • {point}")
    
    alert = FilingAlert(
        trigger_type=trigger_type,
        title=title,
        body="\n".join(body_parts),
        filing_form=filing.form,
        filing_date=filing.filed,
        accession=filing.accession,
        ai_verified=True,
        ai_confidence=str(confidence),
        ai_interpretation=reasoning
    )
    alerts.append(alert)
    
    return alerts, ai_result

    if n >= 1e9:
        return f"${n/1e9:.2f}B"
    if n >= 1e6:
        return f"${n/1e6:.0f}M"
    return f"${n:,.0f}"

def calculate_buy_signal(report: Report) -> None:
    """Calculate buy signal based on triggers and update report in-place."""
    # Collect all trigger types
    trigger_types = [a.trigger_type for a in report.alerts]
    
    # Categorize triggers
    positive_triggers = [t for t in trigger_types if t in ("A", "B", "C+", "D+", "E+", "F+", "G+", "I+", "K+")]
    negative_triggers = [t for t in trigger_types if t in ("C-", "D-", "E-", "F-", "K-")]
    neutral_triggers = [t for t in trigger_types if t in ("H", "J")]  # M&A and management changes can go either way
    
    # Default to neutral
    report.buy_signal = "NEUTRAL"
    report.buy_signal_reasons = []
    
    # Negative signals override positive ones
    if negative_triggers:
        report.buy_signal = "NEGATIVE"
        
        # Add reasons for negative signals
        if "C-" in negative_triggers:
            report.buy_signal_reasons.append("Distress markers detected (Trigger C-)")
        if "D-" in negative_triggers:
            report.buy_signal_reasons.append("Earnings miss or decline (Trigger D-)")
        if "E-" in negative_triggers:
            report.buy_signal_reasons.append("Revenue decline (Trigger E-)")
        if "F-" in negative_triggers:
            report.buy_signal_reasons.append("Margin compression (Trigger F-)")
        if "K-" in negative_triggers:
            report.buy_signal_reasons.append("Credit rating downgrade or debt issues (Trigger K-)")
    
    # Positive signals (if no negative signals)
    elif positive_triggers:
        report.buy_signal = "POSITIVE"
        
        # Add reasons for positive signals
        if "A" in positive_triggers:
            report.buy_signal_reasons.append("EBITDA guidance reaffirmed/raised (Trigger A)")
        if "B" in positive_triggers:
            report.buy_signal_reasons.append("Liquidity flat/improving (Trigger B)")
        if "C+" in positive_triggers:
            report.buy_signal_reasons.append("Credit markets open without distress (Trigger C+)")
        if "D+" in positive_triggers:
            report.buy_signal_reasons.append("Earnings beat or strong growth (Trigger D+)")
        if "E+" in positive_triggers:
            report.buy_signal_reasons.append("Revenue growth acceleration (Trigger E+)")
        if "F+" in positive_triggers:
            report.buy_signal_reasons.append("Margin expansion (Trigger F+)")
        if "G+" in positive_triggers:
            report.buy_signal_reasons.append("Share buyback or dividend increase (Trigger G+)")
        if "I+" in positive_triggers:
            report.buy_signal_reasons.append("Major contract win (Trigger I+)")
        if "K+" in positive_triggers:
            report.buy_signal_reasons.append("Debt refinancing improvement or credit upgrade (Trigger K+)")
    
    # Neutral triggers (when only neutral triggers present)
    elif neutral_triggers:
        if "H" in neutral_triggers:
            report.buy_signal_reasons.append("M&A activity detected - requires deeper analysis (Trigger H)")
        if "J" in neutral_triggers:
            report.buy_signal_reasons.append("Management change - requires deeper analysis (Trigger J)")
    
    # No triggers
    if not report.buy_signal_reasons:
        report.buy_signal_reasons.append("No significant triggers detected")

def save_report(report: Report, report_dir: str) -> str:
    """Save report to JSON file and return the file path."""
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{report.ticker.lower()}_report_{timestamp}.json"
    filepath = os.path.join(report_dir, filename)
    
    # Convert dataclasses to dict for JSON serialization
    report_dict = {
        "ticker": report.ticker,
        "timestamp": report.timestamp,
        "filings_checked": report.filings_checked,
        "new_filings": report.new_filings,
        "buy_signal": report.buy_signal,
        "buy_signal_reasons": report.buy_signal_reasons,
        "ai_verification_enabled": USE_AI_VERIFICATION and openai_client is not None,
        "alerts": [
            {
                "trigger_type": a.trigger_type,
                "title": a.title,
                "body": a.body,
                "filing_form": a.filing_form,
                "filing_date": a.filing_date,
                "accession": a.accession,
                "ai_verified": a.ai_verified,
                "ai_confidence": a.ai_confidence,
                "ai_interpretation": a.ai_interpretation
            }
            for a in report.alerts
        ]
    }
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, sort_keys=True)
    
    return filepath

def print_report_summary(report: Report, compact: bool = False) -> str:
    """Print a human-readable summary of the report.
    
    Args:
        report: The Report object to summarize
        compact: If True, return compact single-line format for ticker display
        
    Returns:
        String summary (for compact mode) or empty string (for full mode)
    """
    if compact:
        # Compact format for inline ticker display
        if not report.alerts:
            return "[SEC: No filings]"
        
        # Get strongest signal from alerts and extract reasoning
        buy_alerts = [a for a in report.alerts if a.trigger_type == "BUY"]
        sell_alerts = [a for a in report.alerts if a.trigger_type == "SELL"]
        hold_alerts = [a for a in report.alerts if a.trigger_type == "HOLD"]
        
        # Determine signal and get reasoning
        if buy_alerts:
            avg_conf = sum(int(a.ai_confidence) for a in buy_alerts) / len(buy_alerts)
            reasoning = buy_alerts[0].ai_interpretation if buy_alerts else ""
            return f"[SEC: BUY {avg_conf:.0f}/10]|SEC: {reasoning}"
        elif sell_alerts:
            avg_conf = sum(int(a.ai_confidence) for a in sell_alerts) / len(sell_alerts)
            reasoning = sell_alerts[0].ai_interpretation if sell_alerts else ""
            return f"[SEC: SELL {avg_conf:.0f}/10]|SEC: {reasoning}"
        else:
            # HOLD signal
            reasoning = hold_alerts[0].ai_interpretation if hold_alerts else ""
            return f"[SEC: HOLD]|SEC: {reasoning}"
    else:
        # Full detailed format
        print("\n" + "="*70)
        print(f"Stock Alert Report: {report.ticker}")
        print(f"Timestamp: {report.timestamp}")
        if USE_AI_VERIFICATION and openai_client:
            print(f"🤖 AI Verification: ENABLED (GPT-4.1)")
        else:
            print(f"AI Verification: Disabled")
        print("="*70)
        print(f"Filings checked: {report.filings_checked}")
        print(f"New filings: {report.new_filings}")
        print(f"\nBUY SIGNAL: {report.buy_signal}")
        print(f"\nReasons:")
        for reason in report.buy_signal_reasons:
            print(f"  - {reason}")
        
        if report.alerts:
            print(f"\nAlerts ({len(report.alerts)}):")
            for i, alert in enumerate(report.alerts, 1):
                print(f"\n  [{i}] {alert.trigger_type}: {alert.filing_form} filed {alert.filing_date}")
                print(f"      {alert.title}")
                if alert.ai_verified and alert.ai_confidence:
                    print(f"      🤖 AI Confidence: {alert.ai_confidence.upper()}")
        else:
            print("\nNo alerts triggered.")
        
        print("="*70 + "\n")
        return ""

def run_monitor(ticker: str, report_dir: Optional[str] = None, enable_alerts: bool = True, price_data: Optional[dict] = None) -> Report:
    """Main monitoring logic. Returns a Report object.
    
    Args:
        ticker: Stock ticker symbol
        report_dir: Directory to save reports (optional)
        enable_alerts: Whether to send real-time alerts
        price_data: Optional dict with price info (currentPrice, priceChange, percentChange) for combined analysis
    """
    cik10 = get_cik_for_ticker(ticker)
    filings = get_recent_filings(cik10, limit=40)

    # Always review latest filings with AI analysis
    filings.sort(key=lambda x: x.filed, reverse=True)  # newest first
    
    # Limit to MAX_FILINGS_TO_REVIEW
    filings_to_review = filings[:MAX_FILINGS_TO_REVIEW]
    
    if DEBUG_MODE:
        print(f"\n=== SEC DEBUG: Filing Review ===", file=sys.stderr)
        print(f"Total filings found: {len(filings)}", file=sys.stderr)
        print(f"Reviewing latest {len(filings_to_review)} filing(s) (SEC_MAX_FILINGS={MAX_FILINGS_TO_REVIEW})", file=sys.stderr)
        print(f"=" * 50, file=sys.stderr)
    
    # Create report
    report = Report(
        ticker=ticker.upper(),
        timestamp=datetime.now().isoformat(),
        filings_checked=len(filings),
        new_filings=len(filings_to_review)
    )

    for f in filings_to_review:
        alerts, ai_result = analyze_filing(ticker, cik10, f, price_data)
        
        # Add alerts to report
        report.alerts.extend(alerts)
        
        # Send real-time alerts if enabled
        if enable_alerts:
            for alert in alerts:
                send_alert(alert.title, alert.body)
        
        # Add AI signal to buy signal reasons
        signal = ai_result.get("signal", "HOLD")
        confidence = ai_result.get("confidence", 5)
        if signal == "BUY" and confidence >= 6:
            report.buy_signal_reasons.append(f"AI: {signal} signal (confidence {confidence}/10) from {f.form}")
        elif signal == "SELL":
            report.buy_signal_reasons.append(f"AI: {signal} signal (confidence {confidence}/10) from {f.form}")

    # If price data provided (combined -f -r mode), factor it into buy signal
    if price_data:
        # Add price context to buy signal reasoning
        price_change = price_data.get('percentChange', 0)
        if price_change > 0:
            report.buy_signal_reasons.append(f"Price momentum: +{price_change:.2f}% supports positive SEC signals")
        elif price_change < -5:
            report.buy_signal_reasons.append(f"Price weakness: {price_change:.2f}% may indicate market skepticism")
    
    # Calculate buy signal
    calculate_buy_signal(report)
    
    # Save report if directory specified
    if report_dir:
        save_report(report, report_dir)

    return report

def main() -> None:
    global QUIET_MODE, DEBUG_MODE
    
    parser = argparse.ArgumentParser(
        description="Monitor SEC EDGAR filings for stock triggers and generate buy signal reports."
    )
    parser.add_argument("ticker", help="Stock ticker symbol (required, e.g., AAPL, MSFT)")
    parser.add_argument("--report-dir", default=REPORT_DIR, help="Directory to save reports (default: /tmp/ticker_reports)")
    parser.add_argument("--no-alert", action="store_true", help="Disable real-time alerts, only generate report")
    parser.add_argument("--no-report", action="store_true", help="Disable report saving")
    parser.add_argument("--compact", action="store_true", help="Print compact single-line summary for ticker display")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--price-data", type=str, help="JSON string with price data for combined analysis")
    
    args = parser.parse_args()
    
    # Enable quiet mode when compact mode is used
    QUIET_MODE = args.compact
    # Enable debug mode when --debug flag is used
    DEBUG_MODE = args.debug
    
    ticker = args.ticker
    if not ticker:
        print("Error: Ticker symbol is required")
        exit(1)
    
    try:
        report_dir_to_use = None if args.no_report else args.report_dir
        enable_alerts = not args.no_alert
        
        # Parse price data if provided
        price_data = None
        if args.price_data:
            try:
                price_data = json.loads(args.price_data)
            except:
                pass
        
        report = run_monitor(ticker, report_dir=report_dir_to_use, enable_alerts=enable_alerts, price_data=price_data)
        
        # Print summary (compact or full)
        summary = print_report_summary(report, compact=args.compact)
        if summary:
            print(summary)
        
        if report_dir_to_use and not args.compact:
            print(f"Report saved to: {report_dir_to_use}/")
            
    except Exception as e:
        if args.compact:
            print("[SEC: Error]")
        else:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        exit(1)

if __name__ == "__main__":
    main()

