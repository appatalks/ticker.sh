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


def call_openai(prompt: str, model: str, api_key: str, api_base: str = 'https://api.openai.com/v1') -> str:
    if requests is None:
        raise RuntimeError('requests library is required')
    url = f"{api_base}/chat/completions"
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 400,
        'temperature': 0.0,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    # Try to extract assistant content
    for choice in data.get('choices', []):
        msg = choice.get('message', {}).get('content')
        if msg:
            return msg
    return json.dumps(data)


def build_prompt(symbol: str, timeframe: str, indicators: Dict[str, object]) -> str:
    # Deterministic prompt requesting JSON output
    return (
        f"Analyze the following indicators for {symbol} on the timeframe {timeframe}.\n"
        "Return a single valid JSON object with keys: recommendation (buy/sell/hold), confidence (1-10 integer), indicators (object with rsi, macd, ppo values), rationale (short string).\n"
        f"Indicators:\n{json.dumps(indicators)}\n"
        "Use only the provided data to form your recommendation. Be concise."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('symbol')
    parser.add_argument('timeframe')
    parser.add_argument('--model', default=None)
    parser.add_argument('--api_key', default=None)
    args = parser.parse_args()

    load_env_from_dotenv()
    api_key = args.api_key or os.environ.get('OPENAI_API_KEY')
    model = args.model or os.environ.get('OPENAI_MODEL', 'gpt-5-mini')
    api_base = os.environ.get('OPENAI_API_BASE', 'https://api.openai.com/v1')

    if not api_key:
        print(json.dumps({'error': 'OPENAI_API_KEY not set'}))
        sys.exit(2)

    # For prototype: fetch recent prices from Yahoo using their chart API
    import subprocess
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{args.symbol}?range=1mo&interval=1d"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        # filter nulls
        closes = [c for c in closes if c is not None]
    except Exception as e:
        print(json.dumps({'error': f'failed to fetch prices: {e}'}))
        sys.exit(3)

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

    try:
        ans = call_openai(prompt, model, api_key, api_base=api_base)
        # print the raw assistant content
        print(ans)
    except Exception as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(4)

if __name__ == '__main__':
    main()
