#!/usr/bin/env python3
"""
claude_quota_logger.py

Polls Anthropic's internal OAuth usage endpoint and appends ONE raw sample
per poll to samples.csv. Per-window history (how much of the quota you used
before each window reset) is *derived* from those raw samples by
render_report.py, grouping by `resets_at`.

This append-only design is robust: even if the poller misses the exact moment
a window rolls over (laptop asleep, cron skipped), the last sample we did take
inside that window still gives a good "final / peak utilization" for it.

Data source (undocumented, may change without notice):
    GET https://api.anthropic.com/api/oauth/usage

No third-party dependencies. Python 3.8+.

Usage:
    python3 claude_quota_logger.py            # poll once (good for launchd/cron)
    python3 claude_quota_logger.py --loop     # poll forever, every POLL_SECONDS

Output (in ./data/ next to this script):
    samples.csv   one row per poll (the source of truth)
"""

import csv
import json
import os
import sys
import time
import datetime
import pathlib
import subprocess
import urllib.request
import urllib.error

USAGE_URL    = "https://api.anthropic.com/api/oauth/usage"
CRED_PATH    = pathlib.Path.home() / ".claude" / ".credentials.json"
DATA_DIR     = pathlib.Path(__file__).resolve().parent / "data"
SAMPLES_PATH = DATA_DIR / "samples.csv"
POLL_SECONDS = 300  # 5 minutes; lower this for tighter accuracy near the reset

SAMPLE_FIELDS = [
    "recorded_at_utc",     # when this poll happened
    "fh_resets_at",        # five_hour.resets_at  -> the window identity
    "fh_util",             # five_hour.utilization (%)
    "wk_resets_at",        # seven_day.resets_at
    "wk_util",             # seven_day.utilization (%)
    "wk_opus_util",        # seven_day_opus.utilization (%)
    "wk_sonnet_util",      # seven_day_sonnet.utilization (%)
]


def _token_from_keychain():
    """On macOS, Claude Code stores creds in the login keychain, not a file."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)["claudeAiOauth"]["accessToken"]
    except Exception:
        return None


def get_token():
    """Env var, then the credentials file, then the macOS Keychain.

    We re-read on every poll (no caching) so that whenever Claude Code's
    daemon refreshes the OAuth token, the next poll picks up the new one.
    """
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok.strip()
    try:
        data = json.loads(CRED_PATH.read_text())
        return data["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    tok = _token_from_keychain()
    if tok:
        return tok
    sys.exit(
        f"Could not read OAuth token.\n"
        f"  - Set $CLAUDE_CODE_OAUTH_TOKEN, or\n"
        f"  - make sure {CRED_PATH} exists, or\n"
        f"  - (macOS) sign in to Claude Code so the 'Claude Code-credentials'\n"
        f"    keychain item exists."
    )


def fetch_usage(token):
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": "2023-06-01",
            "User-Agent": "claude-quota-logger/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def append_sample(row):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not SAMPLES_PATH.exists()
    with SAMPLES_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(SAMPLE_FIELDS)
        w.writerow(row)


def now_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _util(d):
    """utilization out of a sub-object that may be None."""
    return (d or {}).get("utilization")


def poll_once():
    token = get_token()
    try:
        usage = fetch_usage(token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"[{ts()}] auth failed ({e.code}) — token likely expired. "
                  f"Run any Claude Code command to refresh credentials.",
                  file=sys.stderr)
            return
        raise

    fh = usage.get("five_hour") or {}
    fh_util  = fh.get("utilization")
    fh_reset = fh.get("resets_at")
    if fh_util is None or fh_reset is None:
        print(f"[{ts()}] no five_hour data in response: {usage}", file=sys.stderr)
        return

    wk = usage.get("seven_day") or {}
    append_sample([
        now_utc_iso(),
        fh_reset,
        fh_util,
        wk.get("resets_at"),
        wk.get("utilization"),
        _util(usage.get("seven_day_opus")),
        _util(usage.get("seven_day_sonnet")),
    ])
    print(f"[{ts()}] 5h={fh_util}% (resets {fh_reset})"
          f"  7d={wk.get('utilization')}%")


def main():
    loop = "--loop" in sys.argv or "--watch" in sys.argv
    if loop:
        print(f"Polling every {POLL_SECONDS}s -> {SAMPLES_PATH}")
        while True:
            try:
                poll_once()
            except Exception as e:
                print(f"[{ts()}] poll error: {e}", file=sys.stderr)
            time.sleep(POLL_SECONDS)
    else:
        poll_once()


if __name__ == "__main__":
    main()
