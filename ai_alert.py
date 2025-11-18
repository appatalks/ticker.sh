#!/usr/bin/env python3
"""
Helper script to compute indicators and query the OpenAI API for buy/sell/hold alerts.
Usage: ai_alert.py SYMBOL TIMEFRAME
Reads OPENAI_API_KEY and OPENAI_MODEL from environment or .env
"""
import os
import sys
import json
import math
import statistics
import argparse
from typing import List, Dict
import time

try:
    import requests
except Exception:
    requests = None

# Minimal RSI, EMA, MACD, PPO implementations

def ema(values: List[float], period: int) -> List[float]:
    if not values or period <= 0:
        return []
    k = 2 / (period + 1)
    emas = [values[0]]
    for price in values[1:]:
        emas.append((price - emas[-1]) * k + emas[-1])
    return emas


def sma(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    res = []
    for i in range(period - 1, len(values)):
        res.append(sum(values[i - period + 1:i + 1]) / period)
    return res


def rsi(values: List[float], period: int = 14) -> List[float]:
    if len(values) < period + 1:
        return []
    gains = []
    losses = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis = [100 - (100 / (1 + (avg_gain / (avg_loss or 1e-9))))]
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsis.append(100 - (100 / (1 + (avg_gain / (avg_loss or 1e-9)))))
    return rsis


def macd(values: List[float], fast=12, slow=26, signal=9) -> Dict[str, List[float]]:
    if len(values) < slow:
        return {"macd": [], "signal": [], "hist": []}
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [f - s for f, s in zip(ema_fast[-len(ema_slow):], ema_slow)]
    signal_line = ema(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line[-len(signal_line):], signal_line)]
    return {"macd": macd_line[-len(signal_line):], "signal": signal_line, "hist": hist}


def ppo(values: List[float], fast=12, slow=26) -> List[float]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    # Align lengths
    length = min(len(ema_fast), len(ema_slow))
    res = []
    for i in range(-length, 0):
        f = ema_fast[i]
        s = ema_slow[i]
        if s == 0:
            res.append(0.0)
        else:
            res.append(((f - s) / s) * 100)
    return res


def load_env_from_dotenv(path: str = '.env'):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ.setdefault(k, v)


def call_openai(prompt: str, model: str, api_key: str, api_base: str = 'https://api.openai.com/v1', max_completion_tokens: int = 800, reasoning: Dict = None, text_cfg: Dict = None, use_responses_api: bool = False):
    if requests is None:
        raise RuntimeError('requests library is required')
    # Validate api_base looks reasonable to avoid malformed URLs
    if not isinstance(api_base, str) or not api_base.startswith('http'):
        raise RuntimeError(f'OPENAI_API_BASE looks invalid: {api_base!r}')
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    if use_responses_api:
        # New Responses API: include reasoning/text tuning and use max_output_tokens
        url = f"{api_base.rstrip('/')}/responses"
        payload = {
            'model': model,
            'input': prompt,
            'max_output_tokens': int(max_completion_tokens),
        }
        if isinstance(reasoning, dict):
            payload['reasoning'] = reasoning
        if isinstance(text_cfg, dict):
            payload['text'] = text_cfg
    else:
        # Legacy chat completions endpoint: do not send reasoning/text as they may be rejected
        url = f"{api_base.rstrip('/')}/chat/completions"
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_completion_tokens': int(max_completion_tokens),
        }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        # Provide a clearer error message and include response body when available
        resp_text = None
        status = None
        if hasattr(e, 'response') and e.response is not None:
            try:
                status = e.response.status_code
                resp_text = e.response.text
            except Exception:
                resp_text = '<unable to read response body>'
        msg = f'OpenAI request failed ({e.__class__.__name__}): {e}'
        if status is not None:
            msg += f'; status={status}'
        if resp_text:
            # Truncate long bodies to keep messages short
            msg += f'; body={resp_text[:1000]}'
        raise RuntimeError(msg)
    try:
        data = resp.json()
    except Exception:
        # Return raw text if JSON decode fails
        return resp.text, None, None, None

    # Prefer to extract assistant message text from parsed response
    assistant_text = extract_assistant_text_from_parsed(data)

    # get finish_reason and completion_tokens when available for truncation detection
    finish_reason = None
    completion_tokens = None
    try:
        choices = data.get('choices') or []
        if isinstance(choices, list) and len(choices) > 0:
            first = choices[0]
            finish_reason = first.get('finish_reason')
            usage = data.get('usage') or first.get('usage') or {}
            completion_tokens = usage.get('completion_tokens') if isinstance(usage, dict) else None
    except Exception:
        finish_reason = None
        completion_tokens = None

    if assistant_text:
        return assistant_text, data, finish_reason, completion_tokens

    # Fall back to old behavior: try choices -> message -> content
    for choice in data.get('choices', []):
        msg = choice.get('message', {}).get('content')
        if msg:
            return msg, data, finish_reason, completion_tokens

    # Last resort: return the pretty JSON string and parsed data
    return json.dumps(data), data, finish_reason, completion_tokens


def extract_assistant_text_from_parsed(parsed):
    """Try common response shapes and return assistant text if found, else None."""
    # If it's already a string
    if isinstance(parsed, str):
        return parsed
    if not isinstance(parsed, dict):
        return None

    def extract_text_recursive(obj):
        """Recursively collect text from nested structures."""
        if obj is None:
            return ''
        if isinstance(obj, str):
            return obj
        texts = []
        if isinstance(obj, dict):
            # check common keys
            # Prefer explicit content keys first
            for key in ('content', 'text', 'parts', 'message'):
                if key in obj:
                    t = extract_text_recursive(obj[key])
                    if t:
                        texts.append(t)
            # also inspect all values but filter out short/role tokens like 'assistant'
            for k, v in obj.items():
                if k in ('role', 'type', 'id', 'object', 'model', 'created', 'index'):
                    continue
                t = extract_text_recursive(v)
                # consider text useful if it's longer than 10 chars or contains whitespace/punctuation
                if t and (len(t) > 10 or any(ch.isspace() for ch in t) or any(p in t for p in '.:,?"\'()[]{}')):
                    texts.append(t)
        elif isinstance(obj, list):
            for item in obj:
                t = extract_text_recursive(item)
                if t:
                    texts.append(t)
        return ' '.join([s for s in texts if s])

    # Common: choices -> message -> content
    choices = parsed.get('choices') or parsed.get('outputs') or parsed.get('output')
    if isinstance(choices, list) and len(choices) > 0:
        first = choices[0]
        if isinstance(first, dict):
            # Try extracting recursively from common locations
            msg = first.get('message') or first.get('delta') or first
            text = extract_text_recursive(msg)
            if text:
                return text
            # fallback: try the whole first element
            text2 = extract_text_recursive(first)
            if text2:
                return text2

    # Other possible top-level fields
    for key in ('output', 'response', 'result', 'message'):
        v = parsed.get(key)
        t = extract_text_recursive(v)
        if t:
            return t

    return None


def extract_short_rationale_from_text(text: str):
    """Return a short rationale string from free-form assistant text.
    If the text looks like JSON, try to parse and extract common rationale keys.
    Otherwise return the first sentence (period/!? terminated) or first line.
    Avoid splitting on numeric decimals by preferring sentence-ending punctuation.
    """
    if not text:
        return None
    t = text.strip()
    # If it looks like JSON, try to parse and extract a rationale field
    if t.startswith('{') or t.startswith('['):
        try:
            j = json.loads(t)
            for k in ('rationale', 'reason', 'explanation'):
                v = j.get(k)
                if isinstance(v, str) and v.strip():
                    return ' '.join(v.split())
        except Exception:
            # not valid JSON or can't extract; fall through
            pass
        return None
    # Prefer sentence ending punctuation (., !, ?). This avoids splitting on numeric decimals.
    import re
    m = re.search(r"([^.!?]+[.!?])", t)
    if m:
        return m.group(1).strip()
    # Fallback to first line
    return t.splitlines()[0].strip()


def build_prompt(symbol: str, timeframe: str, indicators: Dict[str, object]) -> str:
    # Deterministic prompt requesting JSON output
    return (
        f"Analyze the following indicators for {symbol} on the timeframe {timeframe}.\n"
        "Return a single valid JSON object with keys: recommendation (buy/sell/hold), confidence (1-10 integer), indicators (object with rsi, macd, ppo values), rationale (short string).\n"
        f"Indicators:\n{json.dumps(indicators)}\n"
        "Use only the provided data to form your recommendation. Be concise."
    )


def compute_confidence_from_indicators(indicators: Dict[str, object]) -> int:
    """Compute a 1-10 confidence score from multiple indicators (rsi, macd_hist, ppo).
    Returns an int between 1 and 10. The function normalizes each indicator to a 0..1
    contribution and combines them with weights. Missing values reduce the score
    gracefully.
    """
    try:
        r = indicators.get('rsi')
        macd_h = indicators.get('macd_hist')
        ppo_v = indicators.get('ppo')
    except Exception:
        return 5

    # RSI contribution: distance from neutral (50) scaled to 0..1
    r_score = 0.0
    if isinstance(r, (int, float)):
        r_score = min(1.0, abs(r - 50.0) / 50.0)

    # MACD histogram contribution: larger absolute histogram -> stronger signal
    macd_score = 0.0
    if isinstance(macd_h, (int, float)):
        # scale into 0..1 using a soft normalization; avoids division by tiny numbers
        macd_score = min(1.0, abs(macd_h) / (abs(macd_h) + 1.0))

    # PPO contribution: percent-based indicator; treat values around 10% as strong
    ppo_score = 0.0
    if isinstance(ppo_v, (int, float)):
        ppo_score = min(1.0, abs(ppo_v) / 10.0)

    # Weights chosen to favor RSI while still using MACD histogram and PPO
    w_rsi = 0.5
    w_macd = 0.3
    w_ppo = 0.2

    combined = (w_rsi * r_score) + (w_macd * macd_score) + (w_ppo * ppo_score)

    # Map combined 0..1 to 1..10 integer
    conf = int(max(1, min(10, round(combined * 9) + 1)))
    return conf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('symbol')
    parser.add_argument('timeframe')
    parser.add_argument('--debug', action='store_true', help='Include raw response in output')
    parser.add_argument('--rationale', action='store_true', help='Include rationale in compact output')
    parser.add_argument('--model', default=None)
    parser.add_argument('--api_key', default=None)
    args = parser.parse_args()

    load_env_from_dotenv()
    api_key = args.api_key or os.environ.get('OPENAI_API_KEY')
    model = args.model or os.environ.get('OPENAI_MODEL', 'gpt-5-mini')
    api_base = os.environ.get('OPENAI_API_BASE', 'https://api.openai.com/v1')

    # For prototype: fetch recent prices from Yahoo using their chart API
    session = requests.Session()
    headers = {
        'User-Agent': 'Chrome/115.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
    }
    try:
        # Preflight to populate cookies like the main ticker.sh does
        session.get('https://finance.yahoo.com', headers=headers, timeout=10)
    except Exception:
        # Non-fatal; continue to try fetching the chart
        pass

    # Map timeframe to Yahoo range/interval parameters (best-effort)
    tf = args.timeframe.lower()
    # Defaults
    range_param = '1mo'
    interval_param = '1d'
    if tf.endswith('m') and tf != '1m':
        interval_param = tf
        range_param = '1d'
    elif tf == '1m':
        interval_param = '1m'
        range_param = '1d'
    elif tf.endswith('h'):
        interval_param = '60m'
        range_param = '5d'
    elif tf == '1d':
        interval_param = '1d'
        range_param = '1mo'
    elif tf == '1w':
        interval_param = '1d'
        range_param = '3mo'
    elif tf == '1y' or tf.endswith('y'):
        interval_param = '1d'
        range_param = '1y'

    # Adaptive fetch: try several range values for the requested interval until
    # we collect at least OPENAI_MIN_SAMPLES closes. This avoids single-point
    # responses (e.g. interval=1d with range=1d) which are unusable for
    # indicators like RSI/MACD/PPO.
    # Base min samples (can be overridden by OPENAI_MIN_SAMPLES env)
    default_min_samples = int(os.environ.get('OPENAI_MIN_SAMPLES', '30'))
    # Per-interval recommended minimums (helps intraday and hourly stability)
    INTERVAL_MIN_SAMPLES = {
        '1m': 120,
        '5m': 60,
        '15m': 50,
        '30m': 40,
        '60m': 40,
        '1h': 50,
        '1d': 60,
        '1w': 52,
    }
    # Candidate ranges per interval (ordered from shortest to longest)
    CANDIDATE_RANGES = {
        '1m': ['1d', '5d'],
        '5m': ['1d', '5d', '1mo'],
        '15m': ['1d', '5d', '1mo'],
        '30m': ['5d', '1mo', '3mo'],
        '60m': ['5d', '1mo'],
        '1h': ['5d', '1mo'],
        '1d': ['1mo', '3mo', '1y'],
        '1w': ['3mo', '1y'],
    }

    # Determine min_samples: env override or interval-specific or default
    min_samples = default_min_samples
    try:
        if interval_param in INTERVAL_MIN_SAMPLES:
            min_samples = int(os.environ.get('OPENAI_MIN_SAMPLES', INTERVAL_MIN_SAMPLES[interval_param]))
    except Exception:
        min_samples = default_min_samples

    candidates = CANDIDATE_RANGES.get(interval_param, [range_param])
    closes = []
    last_meta = {}
    last_range_tried = None
    for rng in candidates:
        last_range_tried = rng
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{args.symbol}?range={rng}&interval={interval_param}"
        try:
            r = session.get(url, headers={'User-Agent': headers['User-Agent']}, timeout=15)
            r.raise_for_status()
            data = r.json()
            last_meta = data['chart']['result'][0].get('meta', {})
            closes_raw = data['chart']['result'][0]['indicators']['quote'][0].get('close', [])
            closes = [c for c in closes_raw if c is not None]
        except Exception:
            closes = []
            # try next candidate
            continue
        # If the dataGranularity returned differs from our requested interval, keep trying
        dg = last_meta.get('dataGranularity')
        # Stop if we have enough samples
        if len(closes) >= min_samples:
            break

    if not closes:
        print(json.dumps({'error': 'failed to fetch prices or no close data'}))
        sys.exit(3)

    # If we still don't have enough samples, print debug info and return a
    # deterministic heuristic (status F) rather than calling the model on poor data.
    if len(closes) < min_samples:
        if args.debug:
            print(json.dumps({
                'warning': 'insufficient_samples',
                'symbol': args.symbol,
                'requested_interval': interval_param,
                'last_range_tried': last_range_tried,
                'samples': len(closes),
                'meta': last_meta,
            }, indent=2))
        # Heuristic fallback (same logic as the no-API-key path) with status F
        # Compute last indicators and derive a multi-indicator confidence
        r_val = rsi(closes)[-1] if rsi(closes) else None
        macd_res_local = macd(closes)
        macd_hist_val = macd_res_local['hist'][-1] if macd_res_local['hist'] else None
        ppo_val = ppo(closes)[-1] if ppo(closes) else None
        indicators_local = {'rsi': r_val, 'macd_hist': macd_hist_val, 'ppo': ppo_val}
        if r_val is None and macd_hist_val is None and ppo_val is None:
            if args.rationale:
                print('HOLD:1|F|No indicator data available')
            else:
                print('HOLD:1|F')
            sys.exit(0)
        conf = compute_confidence_from_indicators(indicators_local)
        # Determine a basic recommendation from RSI if available
        if isinstance(r_val, (int, float)) and r_val > 70:
            out = f"SELL:{conf}|F"
            rationale = 'RSI over 70 indicates overbought'
        elif isinstance(r_val, (int, float)) and r_val < 30:
            out = f"BUY:{conf}|F"
            rationale = 'RSI below 30 indicates oversold'
        else:
            out = f"HOLD:{conf}|F"
        if args.rationale:
            r_text = ' '.join(rationale.split()) if rationale else ''
            print(f"{out}|{r_text}")
        else:
            print(out)
        sys.exit(0)

    # (OPENAI_API_KEY check moved below after indicators are computed)

    if len(closes) < 30:
        # pad or fail
        closes = closes

    computed = {}
    computed['rsi'] = rsi(closes)[-1] if rsi(closes) else None
    macd_res = macd(closes)
    computed['macd'] = macd_res['macd'][-1] if macd_res['macd'] else None
    computed['macd_signal'] = macd_res['signal'][-1] if macd_res['signal'] else None
    computed['macd_hist'] = macd_res['hist'][-1] if macd_res['hist'] else None
    computed['ppo'] = ppo(closes)[-1] if ppo(closes) else None

    prompt = build_prompt(args.symbol, args.timeframe, computed)

    # Build a payload preview (without API key) for debugging/inspection
    max_tokens_env = int(os.environ.get('OPENAI_MAX_COMPLETION_TOKENS', 800))
    reasoning_cfg = {'effort': os.environ.get('OPENAI_REASONING_EFFORT', 'minimal')}
    text_cfg = {'verbosity': os.environ.get('OPENAI_TEXT_VERBOSITY', 'low')}
    use_responses_api = os.environ.get('OPENAI_USE_RESPONSES', '0') not in ('0', 'false', 'False', '')
    payload_preview = {
        'use_responses_api': use_responses_api,
        'model': model,
    'input_preview': (prompt if len(prompt) < 2000 else prompt[:2000] + '...[truncated]'),
        'max_completion_tokens': max_tokens_env,
        'reasoning': reasoning_cfg,
        'text': text_cfg,
    'samples': len(closes),
    'dataGranularity': last_meta.get('dataGranularity') if isinstance(last_meta, dict) else None,
    'last_range_tried': last_range_tried,
    }
    # Unconditionally print the payload preview when debug is requested (do not include the API key)
    if args.debug:
        print(json.dumps({'debug_payload': payload_preview}, indent=2))

    # If no API key provided, optionally show the would-be payload in debug mode, otherwise print heuristic
    if not api_key:
        # if debug already printed payload above, exit to avoid accidental network calls
        if args.debug:
            sys.exit(0)

        # For testing: return a compact summary based on indicators (no OpenAI call)
        # Heuristic: RSI > 70 -> sell; RSI < 30 -> buy; else hold. Confidence scaled from distance.
        # Multi-indicator heuristic when no API key: compute confidence from indicators
        conf = compute_confidence_from_indicators(computed)
        r = computed.get('rsi')
        rationale = None
        if isinstance(r, (int, float)) and r > 70:
            rationale = 'RSI over 70 indicates overbought'
            out = f"SELL:{conf}|F"
        elif isinstance(r, (int, float)) and r < 30:
            rationale = 'RSI below 30 indicates oversold'
            out = f"BUY:{conf}|F"
        else:
            rationale = 'RSI neutral'
            out = f"HOLD:{conf}|F"
        if args.rationale:
            # sanitize rationale to a single line
            r_text = ' '.join(rationale.split()) if rationale else ''
            print(f"{out}|{r_text}")
        else:
            print(out)
        sys.exit(0)
        sys.exit(0)

    try:
        # retry-on-truncation loop
        max_retries = 2
        backoff = 1.5
        attempt = 0
        assistant_text = None
        raw = None
        finish_reason = None
        completion_tokens = None
        ans = ''
        while attempt <= max_retries:
            try:
                ans_tmp, raw, finish_reason, completion_tokens = call_openai(prompt, model, api_key, api_base=api_base, max_completion_tokens=max_tokens_env, reasoning=reasoning_cfg, text_cfg=text_cfg, use_responses_api=use_responses_api)
                assistant_text = ans_tmp if isinstance(ans_tmp, str) else None
                ans = ans_tmp
            except Exception as e:
                # If the model complains about max tokens, try one higher allowance before falling back
                err = str(e).lower()
                if 'max_tokens' in err or 'could not finish the message' in err:
                    try:
                        ans_tmp, raw, finish_reason, completion_tokens = call_openai(prompt, model, api_key, api_base=api_base, max_completion_tokens=1200, reasoning=reasoning_cfg, text_cfg=text_cfg, use_responses_api=use_responses_api)
                        assistant_text = ans_tmp if isinstance(ans_tmp, str) else None
                        ans = ans_tmp
                    except Exception:
                        # mark as truncated to attempt compact retry
                        assistant_text = None
                        raw = None
                        finish_reason = 'length'
                        completion_tokens = None
                else:
                    # re-raise unknown errors
                    raise

            truncated = (finish_reason == 'length') or (assistant_text is None) or (assistant_text.strip() == '')
            if not truncated:
                break
            attempt += 1
            if attempt > max_retries:
                break
            compact_prompt = (
                f"Respond with a single-line JSON object only: {{\"recommendation\": \"buy|sell|hold\", \"confidence\": 1-10, \"indicators\": {{\"rsi\": number, \"macd\": number, \"ppo\": number}}, \"rationale\": \"short\"}}\n"
                f"Use the data: {json.dumps(computed)}\n"
            )
            # reduce token allowance on retry to encourage compact output
            try:
                time.sleep(backoff * attempt)
                ans_tmp, raw, finish_reason, completion_tokens = call_openai(compact_prompt, model, api_key, api_base=api_base, max_completion_tokens=200, reasoning=reasoning_cfg, text_cfg=text_cfg, use_responses_api=use_responses_api)
                assistant_text = ans_tmp if isinstance(ans_tmp, str) else None
                ans = ans_tmp
            except Exception:
                assistant_text = None
                raw = None
                break

        # Now attempt to parse assistant_text as JSON recommendation and validate it
        parsed_rec = None
        if assistant_text:
            try:
                parsed_rec = json.loads(assistant_text)
            except Exception:
                parsed_rec = None

        # If parsing failed, try to extract a JSON object containing the "recommendation" key
        def extract_json_from_text(s: str, key: str = 'recommendation'):
            if not s or not isinstance(s, str):
                return None
            idx = s.find(key)
            if idx == -1:
                return None
            # find nearest '{' before the key
            start = s.rfind('{', 0, idx)
            if start == -1:
                return None
            # scan forward to find matching closing brace
            depth = 0
            end = -1
            for i in range(start, len(s)):
                if s[i] == '{':
                    depth += 1
                elif s[i] == '}':
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end == -1:
                return None
            sub = s[start:end+1]
            try:
                return json.loads(sub)
            except Exception:
                return None

        if parsed_rec is None and assistant_text:
            # try to salvage a JSON recommendation embedded in assistant_text
            try_parsed = extract_json_from_text(assistant_text, 'recommendation')
            if try_parsed and isinstance(try_parsed, dict):
                parsed_rec = try_parsed

        def validate_parsed_rec(d: dict):
            """Return tuple (rec_str, conf_int) if valid, else (None, None)."""
            if not isinstance(d, dict):
                return None, None
            # Accept common keys
            rec = d.get('recommendation') or d.get('rec') or d.get('recommend')
            conf = d.get('confidence') or d.get('conf')
            if rec is None or conf is None:
                return None, None
            try:
                rec_s = str(rec).strip().upper()
            except Exception:
                return None, None
            if rec_s not in ('BUY', 'SELL', 'HOLD'):
                return None, None
            try:
                conf_i = int(conf)
            except Exception:
                return None, None
            if conf_i < 1 or conf_i > 10:
                return None, None
            # Optionally validate indicators presence
            inds = d.get('indicators')
            if isinstance(inds, dict):
                # rsi and ppo should be numeric or null; macd may be null
                for k in ('rsi', 'ppo'):
                    v = inds.get(k)
                    if v is not None and not isinstance(v, (int, float)):
                        return None, None
            return rec_s, conf_i
        out_rec = None
        out_conf = None
        out_rationale = None
        status = 'F'

        if isinstance(parsed_rec, dict):
            rec_s, conf_i = validate_parsed_rec(parsed_rec)
            # Determine success (S) or failure (F) based on finish_reason and token usage
            status = 'S'
            if finish_reason == 'length' or (completion_tokens is not None and completion_tokens >= max_tokens_env):
                status = 'F'
            if rec_s and conf_i is not None:
                out_rec = rec_s
                out_conf = conf_i
                # extract rationale if provided
                rat = parsed_rec.get('rationale') or parsed_rec.get('reason') or parsed_rec.get('explanation')
                if isinstance(rat, str):
                    out_rationale = ' '.join(rat.split())

        # If we didn't get a validated parsed result, fall back to heuristics
        if out_rec is None:
            up = (assistant_text or ans or '')
            upu = up.upper()
            import re
            if 'BUY' in upu or 'SELL' in upu or 'HOLD' in upu:
                # Attempt to extract a numeric confidence from assistant text; if none,
                # synthesize from indicators we computed earlier.
                m = re.search(r"(\d{1,2})", upu)
                if m:
                    conf = int(m.group(1))
                else:
                    conf = compute_confidence_from_indicators(computed)
                if 'BUY' in upu:
                    status = 'S' if finish_reason != 'length' else 'F'
                    out_rec, out_conf = 'BUY', conf
                elif 'SELL' in upu:
                    status = 'S' if finish_reason != 'length' else 'F'
                    out_rec, out_conf = 'SELL', conf
                else:
                    status = 'S' if finish_reason != 'length' else 'F'
                    out_rec, out_conf = 'HOLD', conf
                out_rationale = extract_short_rationale_from_text(assistant_text or '')
            else:
                out_rec, out_conf, status = 'HOLD', compute_confidence_from_indicators(computed), 'F'

        # If the user requested rationale but none was returned, synthesize a short one from indicators
        if args.rationale and not out_rationale:
            try:
                parts = []
                r_val = computed.get('rsi')
                macd_h = computed.get('macd_hist') or computed.get('macd_hist')
                ppo_val = computed.get('ppo')
                if isinstance(macd_h, (int, float)) and macd_h > 0:
                    parts.append('MACD positive')
                elif isinstance(macd_h, (int, float)) and macd_h < 0:
                    parts.append('MACD negative')
                if isinstance(ppo_val, (int, float)) and ppo_val > 0:
                    parts.append('PPO positive')
                elif isinstance(ppo_val, (int, float)) and ppo_val < 0:
                    parts.append('PPO negative')
                if isinstance(r_val, (int, float)):
                    if r_val > 70:
                        parts.append('RSI overbought (>70)')
                    elif r_val < 30:
                        parts.append('RSI oversold (<30)')
                    else:
                        parts.append('RSI neutral')
                if parts:
                    out_rationale = '; '.join(parts)
            except Exception:
                out_rationale = None

        # Single consistent output: debug prints full JSON, non-debug prints compact single line
        if args.debug:
            o = {'recommendation': out_rec, 'confidence': int(out_conf), 'status': status, 'raw': raw}
            if out_rationale:
                o['rationale'] = out_rationale
            print(json.dumps(o))
        else:
            if args.rationale:
                # always include the rationale field when requested (may be empty)
                rfield = out_rationale or ''
                print(f"{out_rec}:{int(out_conf)}|{status}|{rfield}")
            else:
                print(f"{out_rec}:{int(out_conf)}|{status}")
    except Exception as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(4)

if __name__ == '__main__':
    main()
