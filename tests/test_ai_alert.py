import json
import subprocess
import os
import sys

# Simple smoke tests for ai_alert.py: offline behavior (no OPENAI_API_KEY)
AI_HELPER = os.path.join(os.path.dirname(__file__), '..', 'ai_alert.py')


def run_helper(args):
    cmd = [sys.executable, AI_HELPER] + args
    # ensure no OPENAI_API_KEY leaks into child env
    env = os.environ.copy()
    env.pop('OPENAI_API_KEY', None)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def test_no_key_debug_payload():
    code, out, err = run_helper(['HNST', '1m', '--debug'])
    assert code == 0
    assert 'debug_payload' in out


def test_no_key_heuristic_output():
    # Use a symbol/timeframe that the script will fetch; if network fails this could be flaky
    code, out, err = run_helper(['HNST', '1m'])
    # Expect compact single-line like BUY:4|F or HOLD:5|F
    assert code == 0
    assert '|' in out
    rec, rest = out.split(':', 1)
    assert rec in ('BUY', 'SELL', 'HOLD')
    assert rest.endswith('F') or rest.endswith('S')
