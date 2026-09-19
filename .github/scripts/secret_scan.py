#!/usr/bin/env python3
"""Fail a pull request when newly added lines contain likely credentials."""

import argparse
import re
import subprocess
import sys
from typing import Iterable, List, Tuple


PREFIX_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|DSA|PGP) PRIVATE KEY-----"),
    ),
)
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password|private[_-]?key)\b\s*[:=]\s*(\S+)"
)
SAFE_VALUE_PREFIXES = ("${{", "$", "<", "example", "your_", "replace_")


def added_lines(base: str, head: str) -> Iterable[Tuple[str, int, str]]:
    command = ["git", "diff", "--no-ext-diff", "--unified=0", base, head, "--"]
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or "unable to read the pull request diff"
        raise RuntimeError(message) from error

    path = "unknown"
    line_number = 0
    for line in output.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@ "):
            match = re.search(r"\+(\d+)(?:,\d+)?", line)
            line_number = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            yield path, line_number, line[1:]
            line_number += 1
        elif not line.startswith("-") and not line.startswith("\\") and line_number:
            line_number += 1


def find_secrets(line: str) -> List[str]:
    findings = [name for name, pattern in PREFIX_PATTERNS if pattern.search(line)]
    assignment = SENSITIVE_ASSIGNMENT.search(line)
    if assignment:
        value = assignment.group(1).strip("\"'")
        if value and not value.lower().startswith(SAFE_VALUE_PREFIXES):
            findings.append("sensitive assignment")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base commit SHA")
    parser.add_argument("--head", required=True, help="pull request head SHA")
    args = parser.parse_args()

    try:
        findings = [
            (path, line_number, rule)
            for path, line_number, line in added_lines(args.base, args.head)
            for rule in find_secrets(line)
        ]
    except RuntimeError as error:
        print(f"secret scan failed: {error}", file=sys.stderr)
        return 2

    if not findings:
        print("secret scan passed: no likely credentials found in added lines")
        return 0

    for path, line_number, rule in findings:
        print(f"secret scan failed: {path}:{line_number}: {rule}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
