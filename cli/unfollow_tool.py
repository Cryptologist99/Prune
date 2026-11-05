#!/usr/bin/env python3
"""
unfollow_tool.py - review and prune the accounts you follow on X, slowly and safely.

Two steps, with your review in between:

  python unfollow_tool.py scan     Fetch who you follow, apply the rules in config.toml,
                                   and write candidates.csv. Nothing is unfollowed.
  python unfollow_tool.py run      Unfollow the rows marked "yes" in candidates.csv,
                                   paced and capped per config.toml.

  python unfollow_tool.py run --dry-run    Show what today's run would do, without doing it.

Credentials are read from environment variables, or from a .env file next to this script:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
"""
import argparse
import csv
import datetime as dt
import os
import random
import sys
import time
import tomllib
from pathlib import Path

import requests
from requests_oauthlib import OAuth1

API = "https://api.x.com/2"
HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.toml"
CANDIDATES = HERE / "candidates.csv"
LOG = HERE / "unfollow_log.csv"

TWITTER_EPOCH_MS = 1288834974657  # post IDs encode their creation time relative to this
USER_FIELDS = "description,public_metrics,protected,verified,most_recent_tweet_id"
CANDIDATE_COLUMNS = ["unfollow", "username", "name", "reasons", "last_post",
                     "followers", "following", "posts", "follows_you", "user_id"]
LOG_COLUMNS = ["timestamp_utc", "user_id", "username", "result", "detail"]
YES = {"yes", "y", "x", "1", "true"}


# ---------------------------------------------------------------- setup

def load_config():
    if not CONFIG.exists():
        sys.exit(f"Can't find {CONFIG.name}. It should sit next to this script.")
    with CONFIG.open("rb") as f:
        cfg = tomllib.load(f)
    for section in ("criteria", "protect", "pace"):
        cfg.setdefault(section, {})
    return cfg


def load_env_file():
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_session():
    load_env_file()
    keys = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        sys.exit("Missing credentials: " + ", ".join(missing) +
                 "\nSet them as environment variables or put them in a .env file next to this script.")
    session = requests.Session()
    session.auth = OAuth1(*(os.environ[k] for k in keys))
    return session


def api_error(resp):
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("title") or body
    except ValueError:
        detail = resp.text[:300]
    return f"HTTP {resp.status_code}: {detail}"


def get_json(session, path, params=None):
    """GET that waits out read rate limits (safe for reads) and exits on other errors."""
    while True:
        resp = session.get(API + path, params=params, timeout=30)
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 900))
            wait = max(reset - time.time(), 0) + 5
            print(f"  Read rate limit reached; waiting {wait / 60:.0f} min before continuing...")
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            sys.exit(f"X API request failed ({path}). {api_error(resp)}")
        return resp.json()


def my_user_id(session):
    data = get_json(session, "/users/me")["data"]
    return data["id"], data["username"]


def fetch_all(session, path, label):
    users, token = [], None
    while True:
        params = {"max_results": 1000, "user.fields": USER_FIELDS}
        if token:
            params["pagination_token"] = token
        page = get_json(session, path, params)
        users.extend(page.get("data", []))
        print(f"  {label}: {len(users)} so far")
        token = page.get("meta", {}).get("next_token")
        if not token:
            return users


# ---------------------------------------------------------------- rules

def post_time(post_id):
    ms = (int(post_id) >> 22) + TWITTER_EPOCH_MS
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def is_protected(user, protect, follower_ids):
    keep = {u.lower().lstrip("@") for u in protect.get("usernames", [])}
    if user["username"].lower() in keep:
        return True
    if protect.get("mutuals", True) and user["id"] in follower_ids:
        return True
    if protect.get("verified", False) and user.get("verified"):
        return True
    return False


def reasons_for(user, crit, follower_ids, now):
    """Return the list of reasons this account matches, or [] if it shouldn't be flagged."""
    m = user.get("public_metrics", {})
    posts = m.get("tweet_count", 0)
    followers = m.get("followers_count", 0)
    checks = []  # (matched, reason)

    days = crit.get("inactive_days", 0)
    if days:
        if user.get("most_recent_tweet_id"):
            idle = (now - post_time(user["most_recent_tweet_id"])).days
            checks.append((idle >= days, f"no posts in {idle} days"))
        elif posts == 0:
            checks.append((True, "has never posted"))
        else:
            checks.append((False, ""))  # protected account: last post unknown, so don't guess

    if crit.get("not_following_back", False):
        checks.append((user["id"] not in follower_ids, "doesn't follow you back"))

    if crit.get("min_followers", 0):
        checks.append((followers < crit["min_followers"], f"only {followers} followers"))

    if crit.get("max_followers", 0):
        checks.append((followers > crit["max_followers"], f"{followers} followers"))

    keywords = [k.lower() for k in crit.get("keywords", []) if k.strip()]
    if keywords:
        text = f"{user.get('name', '')} {user.get('description', '')}".lower()
        hits = [k for k in keywords if k in text]
        checks.append((bool(hits), "name/bio mentions " + ", ".join(hits)))

    if not checks:
        return []
    matched = [why for ok, why in checks if ok]
    if crit.get("match", "any") == "all" and len(matched) != len(checks):
        return []
    return matched


# ---------------------------------------------------------------- scan

def cmd_scan(cfg, args):
    crit, protect = cfg["criteria"], cfg["protect"]
    session = make_session()
    me, handle = my_user_id(session)
    print(f"Signed in as @{handle}")

    following = fetch_all(session, f"/users/{me}/following", "Following")

    follower_ids = set()
    if crit.get("not_following_back") or protect.get("mutuals", True):
        followers = fetch_all(session, f"/users/{me}/followers", "Followers")
        follower_ids = {u["id"] for u in followers}

    now = dt.datetime.now(dt.timezone.utc)
    rows, kept = [], 0
    for user in following:
        if is_protected(user, protect, follower_ids):
            kept += 1
            continue
        reasons = reasons_for(user, crit, follower_ids, now)
        if not reasons:
            continue
        m = user.get("public_metrics", {})
        last = post_time(user["most_recent_tweet_id"]).date().isoformat() if user.get("most_recent_tweet_id") else ""
        rows.append({
            "unfollow": "yes",
            "username": "@" + user["username"],
            "name": user.get("name", ""),
            "reasons": "; ".join(reasons),
            "last_post": last,
            "followers": m.get("followers_count", 0),
            "following": m.get("following_count", 0),
            "posts": m.get("tweet_count", 0),
            "follows_you": "yes" if user["id"] in follower_ids else ("no" if follower_ids else "unknown"),
            "user_id": user["id"],
        })

    rows.sort(key=lambda r: r["last_post"] or "0000")  # never-posted and least recent first

    if CANDIDATES.exists():
        backup = CANDIDATES.with_name("candidates.previous.csv")
        CANDIDATES.replace(backup)
        print(f"Moved your earlier review to {backup.name}")
    with CANDIDATES.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nYou follow {len(following)} accounts. {kept} protected, {len(rows)} match your rules.")
    print(f"Review {CANDIDATES.name}: change 'yes' to 'no' for anyone you want to keep,")
    print("then run:  python unfollow_tool.py run")


# ---------------------------------------------------------------- run

def read_log():
    if not LOG.exists():
        return []
    with LOG.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_log(user_id, username, result, detail=""):
    new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow({"timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                         "user_id": user_id, "username": username, "result": result, "detail": detail})


def cmd_run(cfg, args):
    pace = cfg["pace"]
    cap = int(pace.get("daily_cap", 100))
    lo, hi = float(pace.get("min_delay_seconds", 20)), float(pace.get("max_delay_seconds", 60))
    batch = int(pace.get("batch_size", 25))
    brk = float(pace.get("batch_break_minutes", 30))

    if not CANDIDATES.exists():
        sys.exit("No candidates.csv yet. Run:  python unfollow_tool.py scan")
    with CANDIDATES.open(newline="", encoding="utf-8") as f:
        approved = [r for r in csv.DictReader(f) if r.get("unfollow", "").strip().lower() in YES]

    log = read_log()
    finished = {r["user_id"] for r in log if r["result"] in ("unfollowed", "not_found")}
    day_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    recent = sum(1 for r in log if r["result"] == "unfollowed"
                 and dt.datetime.fromisoformat(r["timestamp_utc"]) > day_ago)

    pending = [r for r in approved if r["user_id"] not in finished]
    allowed = max(cap - recent, 0)
    queue = pending[:allowed]

    print(f"{len(approved)} approved, {len(approved) - len(pending)} already done, {len(pending)} left.")
    print(f"Unfollowed in the last 24 hours: {recent} of {cap} allowed.")
    if not queue:
        print("Nothing to do right now." if pending else "All done.")
        return

    breaks = (len(queue) - 1) // batch
    est = ((len(queue) - 1) * (lo + hi) / 2 + breaks * brk * 60) / 60
    print(f"This run: {len(queue)} unfollows, about {est:.0f} minutes with pauses.")

    if args.dry_run:
        for r in queue:
            print(f"  would unfollow {r['username']}  ({r['reasons']})")
        return

    if not args.yes and input("Start? Type yes to continue: ").strip().lower() != "yes":
        print("Cancelled.")
        return

    session = make_session()
    me, _ = my_user_id(session)

    try:
        for i, r in enumerate(queue, 1):
            resp = session.delete(f"{API}/users/{me}/following/{r['user_id']}", timeout=30)
            if resp.status_code == 429:
                write_log(r["user_id"], r["username"], "rate_limited", api_error(resp))
                print(f"\nX rate-limited the unfollow. Stopping here; try again tomorrow.")
                return
            if resp.status_code == 404:
                write_log(r["user_id"], r["username"], "not_found", "account no longer exists")
                print(f"  [{i}/{len(queue)}] {r['username']} no longer exists; skipped")
                continue
            if resp.status_code >= 400:
                write_log(r["user_id"], r["username"], "error", api_error(resp))
                print(f"\nStopping: X returned an error for {r['username']}. {api_error(resp)}")
                return

            write_log(r["user_id"], r["username"], "unfollowed")
            print(f"  [{i}/{len(queue)}] unfollowed {r['username']}")

            if i < len(queue):
                if i % batch == 0:
                    print(f"  Batch done; resting {brk:.0f} min...")
                    time.sleep(brk * 60 * random.uniform(0.9, 1.2))
                else:
                    time.sleep(random.uniform(lo, hi))
    except KeyboardInterrupt:
        print("\nStopped. Progress is saved; run again to pick up where you left off.")
        return

    left = len(pending) - len(queue)
    print(f"\nDone for now. {left} approved accounts left" + (" - run again in 24 hours." if left else "."))


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Review and prune who you follow on X.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="find accounts that match your rules and write candidates.csv")
    run = sub.add_parser("run", help="unfollow the approved accounts in candidates.csv")
    run.add_argument("--dry-run", action="store_true", help="show what would happen without unfollowing")
    run.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    cfg = load_config()
    {"scan": cmd_scan, "run": cmd_run}[args.command](cfg, args)


if __name__ == "__main__":
    main()
