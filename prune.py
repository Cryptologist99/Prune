#!/usr/bin/env python3
"""
Prune - review who you follow on X and unfollow in safe, paced batches.

Run:   python prune.py
Then use the page that opens in your browser. Needs Python 3.9+ and nothing else.

Everything stays on this computer: your keys, the list of accounts, and the unfollow history
live in the "Your data - do not share" folder next to this file. The page is only reachable from
this computer.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import zipfile
import random
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.0"
DEMO = "--demo" in sys.argv  # made-up accounts from demo_x.py; never touches the real X
API_BASE = os.environ.get("PRUNE_X_API_BASE", "https://api.x.com/2")
HERE = Path(__file__).resolve().parent
# Everything Prune saves (keys, snapshots, kept list, history) lives in one folder next to this
# script, so it's easy to find, back up, or delete. Don't share this folder: it holds your keys.
DATA_NAME = "Demo data" if DEMO else "Your data - do not share"
DATA_DIR = Path(os.environ.get("PRUNE_DATA_DIR", HERE / DATA_NAME))
OLD_DATA_DIR = Path.home() / (".prune-for-x-demo" if DEMO else ".prune-for-x")  # used by earlier versions
SNAP_DIR = DATA_DIR / "snapshots"


def _move_old_data():
    """Earlier versions saved in a hidden folder in your home directory; bring that data here."""
    if "PRUNE_DATA_DIR" in os.environ or DATA_DIR.exists() or not OLD_DATA_DIR.is_dir():
        return
    try:
        import shutil
        shutil.move(str(OLD_DATA_DIR), str(DATA_DIR))
        print(f"Moved your saved Prune data into: {DATA_DIR}")
    except OSError as e:
        print(f"Couldn't move your saved data from {OLD_DATA_DIR} ({e}). Prune will start fresh.")


_move_old_data()
UI_FILE = HERE / "prune_ui.html"
TWITTER_EPOCH_MS = 1288834974657
USER_FIELDS = ("created_at,description,entities,profile_image_url,protected,public_metrics,"
               "url,verified,most_recent_tweet_id")

DEFAULT_SETTINGS = {
    "daily_cap": 100,          # unfollows in any rolling 24 hours
    "min_delay": 20,           # seconds between unfollows (random in this range)
    "max_delay": 60,
    "batch_size": 25,          # after this many in a row...
    "batch_break": 30,         # ...rest this many minutes
    "posts_to_check": 10,      # recent posts read per account when checking posting style
    "price_per_account": 0.001,  # X "owned read" rate for each account in your own lists, in dollars
    "price_per_post": 0.005,     # X pay-per-use rate for each post returned, in dollars
    "show_posting_style": False, # Reposts/Replies tools are off by default because they read many posts
    "settings_version": 2,
}


# =========================================================================== X API

class XError(Exception):
    def __init__(self, status, message, reset=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.reset = int(reset) if reset and str(reset).isdigit() else None


def _pct(value):
    return urllib.parse.quote(str(value), safe="~")


def oauth_header(method, url, params, creds, nonce=None, timestamp=None):
    """OAuth 1.0a HMAC-SHA1 Authorization header for a user-context request."""
    oauth = {
        "oauth_consumer_key": creds["api_key"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": creds["access_token"],
        "oauth_version": "1.0",
    }
    pairs = sorted((_pct(k), _pct(v)) for k, v in list(params.items()) + list(oauth.items()))
    param_string = "&".join(f"{k}={v}" for k, v in pairs)
    base = "&".join([method.upper(), _pct(url), _pct(param_string)])
    key = f"{_pct(creds['api_secret'])}&{_pct(creds['access_secret'])}"
    oauth["oauth_signature"] = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def x_request(creds, method, path, params=None):
    url = API_BASE + path
    params = {k: str(v) for k, v in (params or {}).items()}
    full = url + ("?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote) if params else "")
    req = urllib.request.Request(full, method=method, headers={
        "Authorization": oauth_header(method, url, params, creds),
        "User-Agent": f"Prune/{VERSION}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            data = json.loads(raw)
            errors = data.get("errors") or [{}]
            message = data.get("detail") or data.get("title") or errors[0].get("message") or str(data)
        except ValueError:
            message = raw[:300].decode(errors="replace") or e.reason
        raise XError(e.code, message, e.headers.get("x-rate-limit-reset"))
    except urllib.error.URLError as e:
        raise XError(0, f"Couldn't reach X ({e.reason}). Check your internet connection.")


def post_time_ms(post_id):
    return (int(post_id) >> 22) + TWITTER_EPOCH_MS


def slim_user(u):
    m = u.get("public_metrics", {})
    entities = u.get("entities", {})
    links = [x.get("expanded_url") or x.get("url") for x in entities.get("url", {}).get("urls", [])]
    links += [x.get("expanded_url") or x.get("url") for x in entities.get("description", {}).get("urls", [])]
    return {
        "id": u["id"],
        "username": u.get("username", ""),
        "name": u.get("name", ""),
        "bio": u.get("description", ""),
        "avatar": u.get("profile_image_url", ""),
        "verified": bool(u.get("verified")),
        "protected": bool(u.get("protected")),
        "links": [x for x in links if x],
        "joined": u.get("created_at"),
        "followers": m.get("followers_count", 0),
        "following": m.get("following_count", 0),
        "posts": m.get("tweet_count", 0),
        "last_post": post_time_ms(u["most_recent_tweet_id"]) if u.get("most_recent_tweet_id") else None,
    }


# =========================================================================== storage

class Store:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(DATA_DIR, 0o700)
        except OSError:
            pass
        self.lock = threading.RLock()
        self.version = 0
        self.state = {
            "me": None, "accounts": {}, "order": [], "follower_ids": None, "scanned_at": None,
            "analysis": {}, "keep": [], "log": [], "queue": [], "settings": dict(DEFAULT_SETTINGS),
            "usage": {"accounts": 0, "posts": 0, "requests": 0, "since": time.time()},
            "followers_loaded_at": None,
            "followers_source": None,          # "x" or "archive"
            "read_today": {"day": "", "ids": []},  # X bills each account once per UTC day
        }
        loaded = self._read("state.json")
        if loaded:
            self.state.update(loaded)
            self.state["settings"] = {**DEFAULT_SETTINGS, **loaded.get("settings", {})}
            if loaded.get("settings", {}).get("settings_version", 1) < 2:
                if self.state["settings"]["posts_to_check"] == 20:  # the old default
                    self.state["settings"]["posts_to_check"] = 10
                self.state["settings"]["settings_version"] = 2
        self.creds = self._read("credentials.json")
        self.remember = bool(self.creds)

    def _read(self, name):
        try:
            return json.loads((DATA_DIR / name).read_text())
        except (OSError, ValueError):
            return None

    def _write(self, name, data, private=False):
        path = DATA_DIR / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        if private:
            os.chmod(tmp, 0o600)
        tmp.replace(path)

    def save(self):
        with self.lock:
            self.version += 1
            self._write("state.json", self.state)

    def set_creds(self, creds, remember):
        with self.lock:
            self.creds, self.remember = creds, remember
            path = DATA_DIR / "credentials.json"
            if remember:
                self._write("credentials.json", creds, private=True)
            elif path.exists():
                path.unlink()

    def forget(self):
        with self.lock:
            self.creds, self.remember = None, False
            (DATA_DIR / "credentials.json").unlink(missing_ok=True)

    def unfollowed_in_last_day(self):
        cutoff = time.time() - 86400
        return [e for e in self.state["log"] if e["result"] == "unfollowed" and e["at"] > cutoff]


store = Store()


# =========================================================================== background jobs

class Job:
    def __init__(self, kind):
        self.kind = kind
        self.state = "running"      # running | waiting | paused | done | error | stopped | idle
        self.done = 0
        self.total = 0
        self.message = ""
        self.stop = threading.Event()

    def view(self):
        return {"kind": self.kind, "state": self.state, "done": self.done,
                "total": self.total, "message": self.message}


read_job = None                 # scan or analyze
write_state = {"state": "idle", "message": "", "done": 0, "started_with": 0,
               "fast": False}  # "yeet the rest": no daily limit or breaks, 5-20 s between unfollows
write_wake = threading.Event()
write_paused = threading.Event()
write_thread = None


def clock(ts):
    return time.strftime("%-I:%M %p" if os.name != "nt" else "%I:%M %p", time.localtime(ts)).lstrip("0")


def wait_for_rate_limit(job, err):
    reset = err.reset or time.time() + 900
    job.state = "waiting"
    job.message = f"X asked us to slow down. Continuing at {clock(reset + 5)}."
    while time.time() < reset + 5:
        if job.stop.wait(2):
            return False
    job.state = "running"
    return True


def utc_day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def read_today_ids():
    rt = store.state["read_today"]
    return set(rt["ids"]) if rt["day"] == utc_day() else set()


def mark_read(ids):
    """Remember which accounts X has sent today; returns how many are new (and so billed)."""
    with store.lock:
        rt = store.state["read_today"]
        if rt["day"] != utc_day():
            rt["day"], rt["ids"] = utc_day(), []
        seen = set(rt["ids"])
        new = [i for i in ids if i not in seen]
        rt["ids"].extend(new)
        return len(new)


def load_estimate():
    """How many accounts a load would be billed for, given what X already sent today."""
    s = store.state
    me = s["me"] or {}
    read = read_today_ids()
    following_known = set(s["order"])
    followers_known = set(s["follower_ids"] or [])
    fc, fl = me.get("following_count"), me.get("followers_count")
    following_bill = None if fc is None else max(0, fc - len(read & following_known))
    # followers already sent today, or who are also in your following list (billed with it), are free
    followers_bill = None if fl is None else max(0, fl - len(followers_known & (read | following_known)))
    # followers on their own (following kept from a snapshot): mutuals aren't covered by a following load
    followers_alone = None if fl is None else max(0, fl - len(followers_known & read))
    return {"following": following_bill, "followers": followers_bill, "followers_alone": followers_alone,
            "following_free_today": bool(following_known) and following_known <= read,
            "followers_overlap_known": bool(followers_known)}


def record_usage(accounts=0, posts=0, requests=1):
    """Keep a running tally of what Prune has read from X, for the cost estimate."""
    with store.lock:
        u = store.state["usage"]
        u["accounts"] += accounts
        u["posts"] += posts
        u["requests"] += requests


def paginate(job, path, params, label):
    items, token = [], None
    while True:
        p = dict(params, max_results=1000)
        if token:
            p["pagination_token"] = token
        try:
            page = x_request(store.creds, "GET", path, p)
        except XError as e:
            if e.status == 429:
                if not wait_for_rate_limit(job, e):
                    return None
                continue
            raise
        items.extend(page.get("data", []))
        record_usage(accounts=mark_read([u["id"] for u in page.get("data", [])]))
        job.message = f"{label}: {len(items):,}"
        token = page.get("meta", {}).get("next_token")
        if not token or job.stop.is_set():
            return items


# ------------------------------------------------------------------ snapshots
# Every load from X is also saved as a dated snapshot file, so it can be reopened later
# (or on another computer) without paying X again.

def _snap_index():
    try:
        return json.loads((SNAP_DIR / "index.json").read_text())
    except (OSError, ValueError):
        return []


def _write_snap_index(index):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SNAP_DIR / "index.tmp"
    tmp.write_text(json.dumps(index))
    tmp.replace(SNAP_DIR / "index.json")


def _snap_summary(snap, name):
    return {"file": name, "account_id": snap["me"]["id"], "username": snap["me"]["username"],
            "following_at": snap["following_at"], "followers_at": snap.get("followers_at"),
            "following": len(snap["following"]), "followers_source": snap.get("followers_source"),
            "followers": len(snap["follower_ids"]) if snap.get("follower_ids") is not None else None}


def save_snapshot():
    """Write the currently loaded lists to a new snapshot file. Caller holds store.lock."""
    s = store.state
    snap = {
        "format": "prune-snapshot", "version": 1,
        "me": {"id": s["me"]["id"], "username": s["me"]["username"]},
        "following_at": s["scanned_at"],
        "followers_at": s["followers_loaded_at"] if s["follower_ids"] is not None else None,
        "following": [s["accounts"][uid] for uid in s["order"]],
        "follower_ids": s["follower_ids"],
        "followers_source": s.get("followers_source"),
    }
    return store_snapshot(snap)


def store_snapshot(snap):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SNAP_DIR, 0o700)
    except OSError:
        pass
    taken = max(snap["following_at"], snap.get("followers_at") or 0)
    base = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(taken))
    name, n = f"{base}.json", 1
    while (SNAP_DIR / name).exists():
        n += 1
        name = f"{base}_{n}.json"
    (SNAP_DIR / name).write_text(json.dumps(snap))
    index = [e for e in _snap_index() if e["file"] != name] + [_snap_summary(snap, name)]
    _write_snap_index(sorted(index, key=lambda e: max(e["following_at"], e.get("followers_at") or 0), reverse=True))
    store.state["snapshot_file"] = name
    return name


def open_snapshot(snap, name):
    """Make a snapshot the current data. Caller holds store.lock. No calls to X."""
    s = store.state
    s["accounts"] = {u["id"]: u for u in snap["following"]}
    s["order"] = [u["id"] for u in snap["following"]]
    s["follower_ids"] = snap.get("follower_ids")
    s["scanned_at"] = snap["following_at"]
    s["followers_loaded_at"] = snap.get("followers_at")
    s["followers_source"] = snap.get("followers_source") or ("x" if snap.get("follower_ids") is not None else None)
    s["snapshot_file"] = name
    s["analysis"] = {k: v for k, v in s["analysis"].items() if k in s["accounts"]}


def validate_snapshot(snap):
    if not isinstance(snap, dict) or snap.get("format") != "prune-snapshot":
        raise ValueError("That file isn't a Prune snapshot.")
    try:
        assert isinstance(snap["following"], list) and isinstance(snap["following_at"], (int, float))
        for u in snap["following"]:
            assert isinstance(u.get("id"), str) and isinstance(u.get("username"), str)
            for k, d in (("name", ""), ("bio", ""), ("avatar", ""), ("links", []), ("joined", None),
                         ("followers", 0), ("following", 0), ("posts", 0), ("last_post", None),
                         ("verified", False), ("protected", False)):
                u.setdefault(k, d)
        if snap.get("follower_ids") is not None:
            assert all(isinstance(x, str) for x in snap["follower_ids"])
    except (AssertionError, KeyError, TypeError):
        raise ValueError("That snapshot file is damaged or incomplete.")
    me = store.state["me"]
    if me and snap.get("me", {}).get("id") != me["id"]:
        raise ValueError(f"That snapshot is for @{snap.get('me', {}).get('username', 'another account')}, "
                         f"not @{me['username']}.")
    return snap


def run_scan(job, include_followers, include_following=True):
    me = store.state["me"]["id"]
    if include_following or not store.state["order"]:
        following = paginate(job, f"/users/{me}/following", {"user.fields": USER_FIELDS}, "Loading who you follow")
        if following is None:
            job.state, job.message = "stopped", "Stopped."
            return
    else:
        following = None  # keep the snapshot's following list, only refresh followers
    follower_ids = store.state["follower_ids"]  # keep an earlier followers list if this load skips it
    if include_followers:
        followers = paginate(job, f"/users/{me}/followers", {}, "Loading who follows you")
        if followers is None:
            job.state, job.message = "stopped", "Stopped."
            return
        follower_ids = [u["id"] for u in followers]
    with store.lock:
        if following is not None:
            store.state["accounts"] = {u["id"]: slim_user(u) for u in following}
            store.state["order"] = [u["id"] for u in following]
            store.state["me"]["following_count"] = len(following)
            store.state["scanned_at"] = time.time()
        store.state["follower_ids"] = follower_ids
        if include_followers:
            store.state["followers_loaded_at"] = time.time()
            store.state["followers_source"] = "x"
            store.state["me"]["followers_count"] = len(follower_ids)
        # drop analysis for accounts no longer followed
        store.state["analysis"] = {k: v for k, v in store.state["analysis"].items() if k in store.state["accounts"]}
        save_snapshot()
        store.save()
    job.state = "done"
    job.message = (f"Loaded {len(following):,} accounts you follow." if following is not None
                   else f"Loaded {len(follower_ids):,} followers. Who you follow is from your snapshot.")


def run_analyze(job, ids, n=None):
    n = n or store.state["settings"]["posts_to_check"]
    job.total = len(ids)
    for uid in ids:
        if job.stop.is_set():
            job.state, job.message = "stopped", f"Stopped after checking {job.done:,}."
            return
        while True:
            try:
                data = x_request(store.creds, "GET", f"/users/{uid}/tweets",
                                 {"max_results": max(5, min(100, n)), "tweet.fields": "created_at,referenced_tweets,in_reply_to_user_id"})
                posts = data.get("data", [])
                record_usage(posts=len(posts))
                kinds = [ {r["type"] for r in p.get("referenced_tweets", [])} for p in posts]
                result = {
                    "sample": len(posts),
                    # replies to other people; a thread's follow-up posts reply to the author and don't count
                    "replies": sum(1 for p, k in zip(posts, kinds)
                                   if "replied_to" in k and p.get("in_reply_to_user_id") != uid),
                    "self_replies": sum(1 for p, k in zip(posts, kinds)
                                        if "replied_to" in k and p.get("in_reply_to_user_id") == uid),
                    "reposts": sum(1 for k in kinds if "retweeted" in k),
                    "quotes": sum(1 for k in kinds if "quoted" in k and "replied_to" not in k),
                    "latest": max((post_time_ms(p["id"]) for p in posts), default=None),
                    "checked_at": time.time(),
                }
                break
            except XError as e:
                if e.status == 429:
                    if not wait_for_rate_limit(job, e):
                        job.state, job.message = "stopped", f"Stopped after checking {job.done:,}."
                        return
                    continue
                if e.status in (401, 402) or (e.status == 403 and job.done == 0 and "client" in e.message.lower()):
                    job.state = "error"
                    job.message = f"X refused the request: {e.message}"
                    return
                record_usage()
                result = {"error": e.message, "checked_at": time.time()}
                break
        with store.lock:
            store.state["analysis"][uid] = result
            job.done += 1
            if job.done % 10 == 0 or job.done == job.total:
                store.save()
        job.message = f"Checking posting style: {job.done:,} of {job.total:,}"
        job.state = "running"
    job.state = "done"
    job.message = f"Checked posting style for {job.done:,} accounts."


def start_read_job(kind, target, *args):
    global read_job
    if read_job and read_job.state in ("running", "waiting"):
        raise ValueError("Another task is still running. Wait for it to finish or stop it first.")
    job = Job(kind)

    def runner():
        try:
            target(job, *args)
        except XError as e:
            job.state, job.message = "error", explain_error(e)
        except Exception as e:  # keep the app alive whatever happens
            job.state, job.message = "error", f"Something went wrong: {e}"
        store.save()

    read_job = job
    threading.Thread(target=runner, daemon=True).start()


def interruptible_sleep(seconds):
    """Sleep, waking early if paused or cancelled. Returns False if woken."""
    write_wake.clear()
    if DEMO:
        seconds /= 10  # demo runs ten times faster so you can watch it work
    return not write_wake.wait(seconds)


def unfollow_worker():
    in_batch = 0
    while True:
        with store.lock:
            queue = store.state["queue"]
            s = store.state["settings"]
            if not queue:
                write_state.update(state="idle", message=f"Done. Unfollowed {write_state['done']:,}.", fast=False)
                return
            if write_paused.is_set():
                write_state.update(state="paused", message=write_state["message"] or "Paused.")
                return
            recent = store.unfollowed_in_last_day()
            target = queue[0]
        if not write_state["fast"] and len(recent) >= s["daily_cap"]:
            free_at = min(e["at"] for e in recent) + 86400 + 5
            write_state.update(state="waiting",
                               message=f"Reached the limit of {s['daily_cap']} in 24 hours. Continuing at {clock(free_at)} if Prune is still open.")
            while time.time() < free_at and not write_state["fast"]:
                if not interruptible_sleep(min(30, free_at - time.time())) and write_paused.is_set():
                    break
            in_batch = 0
            continue

        write_state.update(state="running", message=f"Unfollowing @{target['username']}")
        result = "unfollowed"
        try:
            record_usage()
            x_request(store.creds, "DELETE", f"/users/{store.state['me']['id']}/following/{target['id']}")
        except XError as e:
            if e.status == 404:
                result = "not_found"
            else:
                write_paused.set()
                if e.status == 429:
                    msg = "X rate-limited unfollowing, so Prune paused. Wait a few hours, then press Resume."
                else:
                    msg = f"Paused because X returned an error: {explain_error(e)}"
                write_state.update(state="paused", message=msg)
                return
        with store.lock:
            store.state["log"].append({"id": target["id"], "username": target["username"],
                                       "at": time.time(), "result": result})
            if store.state["queue"] and store.state["queue"][0]["id"] == target["id"]:
                store.state["queue"].pop(0)
            write_state["done"] += 1
            store.save()
        in_batch += 1
        if not store.state["queue"]:
            continue
        if write_state["fast"]:
            write_state.update(state="running", message="Unfollowing fast.")
            interruptible_sleep(random.uniform(5, 20))
        elif in_batch >= s["batch_size"]:
            mins = s["batch_break"] * random.uniform(0.9, 1.2)
            write_state.update(state="waiting", message=f"Taking a {mins:.0f}-minute break, like a person would.")
            interruptible_sleep(mins * 60)
            in_batch = 0
        else:
            interruptible_sleep(random.uniform(s["min_delay"], max(s["min_delay"], s["max_delay"])))


def start_unfollow_worker():
    global write_thread
    write_paused.clear()
    write_state["message"] = ""
    if write_thread and write_thread.is_alive():
        write_wake.set()
        return
    write_thread = threading.Thread(target=unfollow_worker, daemon=True)
    write_thread.start()


def explain_error(e):
    if e.status == 401:
        return "X didn't accept these keys. Check that all four are copied correctly and not expired."
    if e.status == 403 and ("oauth1" in e.message.lower() or "permission" in e.message.lower()):
        return ("Your app doesn't have permission to unfollow. In the X Developer Console, open your app's "
                "User authentication settings and set App permissions to 'Read and write', "
                "then regenerate your Access Token and Access Token Secret and enter them again.")
    if e.status == 402 or (e.status == 403 and "client" in e.message.lower()):
        return f"Your X API plan doesn't include this. X says: {e.message}"
    if e.status == 0:
        return e.message
    return f"X returned an error ({e.status}): {e.message}"


# =========================================================================== web server

APP_TOKEN = secrets.token_urlsafe(24)
PORT = 0


class Handler(BaseHTTPRequestHandler):
    server_version = "Prune"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        # Blocks other websites from reaching Prune through DNS tricks.
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _authorized(self):
        # Only the page Prune served carries this token, so other sites can't trigger actions.
        return secrets.compare_digest(self.headers.get("X-Prune-Token", ""), APP_TOKEN)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000_000:
            raise ValueError("Request too large.")
        return json.loads(self.rfile.read(n) or b"{}")

    def _import_archive(self):
        """Read your followers list from X's free data archive (the .zip, or its follower.js)."""
        require_connected()
        n = int(self.headers.get("Content-Length") or 0)
        if not n or n > 20_000_000_000:
            raise ValueError("Choose your X archive .zip, or the follower.js file inside it.")
        with tempfile.TemporaryFile() as tmp:
            left = n
            while left:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                tmp.write(chunk)
                left -= len(chunk)
            tmp.seek(0)
            return import_archive(tmp)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "Forbidden"})
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = UI_FILE.read_text(encoding="utf-8").replace("__PRUNE_TOKEN__", APP_TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if not self._authorized():
            return self._send(403, {"error": "Forbidden"})
        if path == "/api/status":
            return self._send(200, status())
        if path == "/api/accounts":
            return self._send(200, accounts_payload())
        if path == "/api/snapshots":
            me = store.state["me"]
            return self._send(200, {"snapshots": [e for e in _snap_index() if me and e["account_id"] == me["id"]],
                                    "current": store.state.get("snapshot_file")})
        if path == "/api/snapshot":
            name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("file", [""])[0]
            if name not in {e["file"] for e in _snap_index()}:
                return self._send(404, {"error": "Snapshot not found"})
            snap = json.loads((SNAP_DIR / name).read_text())
            snap["keep"] = store.state["keep"]  # carry your kept accounts along with an export
            return self._send(200, json.dumps(snap).encode())
        self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self._host_ok() or not self._authorized():
            return self._send(403, {"error": "Forbidden"})
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/archive/import":
            try:
                return self._send(200, self._import_archive())
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        try:
            body = self._body()
            handler = POST_ROUTES.get(path)
            if not handler:
                return self._send(404, {"error": "Not found"})
            return self._send(200, handler(body) or {"ok": True})
        except XError as e:
            return self._send(400, {"error": explain_error(e)})
        except ValueError as e:
            return self._send(400, {"error": str(e)})


def status():
    s = store.state
    recent = store.unfollowed_in_last_day()
    return {
        "version": store.version,
        "demo": DEMO,
        "data_dir": str(DATA_DIR),
        "connected": bool(store.creds and s["me"]),
        "remember": store.remember,
        "me": s["me"],
        "scanned_at": s["scanned_at"],
        "count": len(s["order"]),
        "read_job": read_job.view() if read_job else None,
        "write": {**write_state, "queue": len(s["queue"])},
        "budget": {
            "used": len(recent), "cap": s["settings"]["daily_cap"],
            "next_free_at": (min(e["at"] for e in recent) + 86400) if recent else None,
        },
        "settings": s["settings"],
        "total_unfollowed": sum(1 for e in s["log"] if e["result"] == "unfollowed"),
        "followers_loaded_at": s["followers_loaded_at"],
        "snapshot_file": s.get("snapshot_file"),
        "followers_source": s.get("followers_source"),
        "estimate": load_estimate(),
        "usage": {**s["usage"], "estimated_cost": round(
            s["usage"]["accounts"] * s["settings"]["price_per_account"]
            + s["usage"]["posts"] * s["settings"]["price_per_post"], 2)},
    }


def accounts_payload():
    with store.lock:
        s = store.state
        followers = set(s["follower_ids"]) if s["follower_ids"] is not None else None
        keep = set(s["keep"])
        queued = {q["id"] for q in s["queue"]}
        gone = {e["id"] for e in s["log"] if e["result"] in ("unfollowed", "not_found")}
        rows = []
        # X returns who you follow newest-first, so list position is the order you followed them
        # (1 = most recent). X doesn't publish follow dates or formally promise this order.
        for rank, uid in enumerate(s["order"], 1):
            if uid in gone:
                continue
            a = dict(s["accounts"][uid])
            a["follow_rank"] = rank
            a["follows_you"] = (uid in followers) if followers is not None else None
            a["keep"] = uid in keep
            a["queued"] = uid in queued
            a["analysis"] = s["analysis"].get(uid)
            rows.append(a)
        return {"version": store.version, "accounts": rows}


def api_connect(body):
    keys = ["api_key", "api_secret", "access_token", "access_secret"]
    creds = {k: str(body.get(k, "")).strip() for k in keys}
    missing = [k for k in keys if not creds[k]]
    if missing:
        raise ValueError("Fill in all four keys.")
    labels = {"api_key": "Consumer Key", "api_secret": "Secret Key",
              "access_token": "Access Token", "access_secret": "Access Token Secret"}
    for k, v in creds.items():
        if v.startswith("AAAAAAAAAAAAAAAAAAAA"):
            raise ValueError(f"The {labels[k]} box has your Bearer Token in it. Prune doesn't use the Bearer Token. "
                             "Generate an Access Token and Access Token Secret on the same page in the X developer portal.")
    me = x_request(creds, "GET", "/users/me", {"user.fields": "profile_image_url,public_metrics"})["data"]
    metrics = me.get("public_metrics", {})
    with store.lock:
        if store.state["me"] and store.state["me"]["id"] != me["id"]:
            # different account: start fresh
            settings = store.state["settings"]
            store.state.update(accounts={}, order=[], follower_ids=None, scanned_at=None, followers_loaded_at=None, followers_source=None,
                               analysis={}, keep=[], log=[], queue=[], settings=settings)
        store.state["me"] = {"id": me["id"], "username": me["username"], "name": me.get("name", ""),
                             "avatar": me.get("profile_image_url", ""),
                             "following_count": metrics.get("following_count", 0),
                             "followers_count": metrics.get("followers_count", 0)}
        record_usage(accounts=mark_read([me["id"]]))
        store.set_creds(creds, bool(body.get("remember")))
        store.save()
    return status()


def stop_activity():
    write_paused.set()
    write_wake.set()
    if read_job:
        read_job.stop.set()


def api_disconnect(body):
    """Forget the saved API keys. Snapshots, kept accounts and history stay."""
    stop_activity()
    store.forget()
    return status()


def api_erase_data(body):
    """Erase snapshots, kept accounts, unfollow history and the spend tally. Keys stay."""
    stop_activity()
    with store.lock:
        store.state.update(accounts={}, order=[], follower_ids=None, scanned_at=None,
                           followers_loaded_at=None, followers_source=None, analysis={}, keep=[], log=[],
                           queue=[], snapshot_file=None,
                           usage={"accounts": 0, "posts": 0, "requests": 0, "since": time.time()})
        write_state.update(state="idle", message="", done=0)
        store.save()
    if SNAP_DIR.exists():
        for f in SNAP_DIR.iterdir():
            f.unlink()
        SNAP_DIR.rmdir()
    return status()


def require_connected():
    if not (store.creds and store.state["me"]):
        raise ValueError("Connect your X account first.")


def api_scan(body):
    require_connected()
    include_following = body.get("following", True) is not False
    if not include_following and not store.state["order"]:
        raise ValueError("Load who you follow first; there's no snapshot to use yet.")
    if not include_following and not body.get("followers"):
        raise ValueError("Nothing to load.")
    start_read_job("scan", run_scan, bool(body.get("followers")), include_following)


def api_analyze(body):
    require_connected()
    ids = [i for i in body.get("ids", []) if i in store.state["accounts"]]
    if not ids:
        raise ValueError("No accounts to check.")
    posts = body.get("posts")
    posts = max(5, min(100, int(posts))) if posts else None
    start_read_job("analyze", run_analyze, ids, posts)


def api_stop_task(body):
    if read_job:
        read_job.stop.set()


def api_unfollow(body):
    require_connected()
    with store.lock:
        keep = set(store.state["keep"])
        already = {q["id"] for q in store.state["queue"]}
        added = 0
        for uid in body.get("ids", []):
            a = store.state["accounts"].get(uid)
            if a and uid not in keep and uid not in already:
                store.state["queue"].append({"id": uid, "username": a["username"]})
                added += 1
        store.save()
    if added:
        write_state["done"] = 0 if write_state["state"] == "idle" else write_state["done"]
        start_unfollow_worker()
    return {"added": added}


def api_pause(body):
    write_paused.set()
    write_wake.set()
    write_state.update(state="paused", message="Paused.")


def api_resume(body):
    require_connected()
    start_unfollow_worker()


def api_cancel(body):
    with store.lock:
        n = len(store.state["queue"])
        store.state["queue"] = []
        store.save()
    write_paused.set()
    write_wake.set()
    write_state.update(state="idle", message=f"Cancelled {n:,} unfollows that hadn't happened yet.", fast=False)


def api_yeet(body):
    """Unfollow everything still queued right away: no daily limit, no breaks, 5-20 s apart."""
    require_connected()
    if not store.state["queue"]:
        raise ValueError("Nothing is waiting to be unfollowed.")
    write_state["fast"] = True
    start_unfollow_worker()


def api_keep(body):
    with store.lock:
        keep = set(store.state["keep"])
        for uid in body.get("ids", []):
            (keep.add if body.get("keep") else keep.discard)(uid)
        store.state["keep"] = sorted(keep)
        if body.get("keep"):
            store.state["queue"] = [q for q in store.state["queue"] if q["id"] not in keep]
        store.save()


def api_settings(body):
    limits = {"daily_cap": (1, 400), "min_delay": (5, 600), "max_delay": (5, 900),
              "batch_size": (1, 200), "batch_break": (0, 240), "posts_to_check": (5, 100)}
    with store.lock:
        s = store.state["settings"]
        for k, (lo, hi) in limits.items():
            if k in body:
                s[k] = max(lo, min(hi, int(body[k])))
        if s["max_delay"] < s["min_delay"]:
            s["max_delay"] = s["min_delay"]
        for k in ("price_per_account", "price_per_post"):
            if k in body:
                s[k] = max(0.0, min(1.0, float(body[k])))
        if "show_posting_style" in body:
            s["show_posting_style"] = bool(body["show_posting_style"])
        store.save()
    return status()


def _ytd_json(text):
    """X archive files look like: window.YTD.follower.part0 = [ ... ]"""
    start = text.find("=")
    if start < 0:
        raise ValueError
    return json.loads(text[start + 1:].strip().rstrip(";"))


def import_archive(fileobj):
    head = fileobj.read(4)
    fileobj.seek(0)
    parts, manifest = [], None
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(fileobj) as z:
                for name in z.namelist():
                    if re.search(r"(^|/)data/follower(-part\d+)?\.js$", name):
                        parts.append(z.read(name).decode("utf-8-sig"))
                    elif re.search(r"(^|/)data/manifest\.js$", name):
                        manifest = z.read(name).decode("utf-8-sig")
        except zipfile.BadZipFile:
            raise ValueError("That .zip file is damaged. Try downloading your archive again.")
        if not parts:
            raise ValueError("That .zip doesn't contain a followers list (data/follower.js). Make sure it's your X archive.")
    else:
        text = fileobj.read().decode("utf-8-sig", errors="replace")
        if "window.YTD.follower." not in text:
            raise ValueError("That file isn't follower.js from an X archive.")
        parts.append(text)
    ids = []
    try:
        for text in parts:
            for row in _ytd_json(text):
                ids.append(str(row["follower"]["accountId"]))
    except (ValueError, KeyError, TypeError):
        raise ValueError("Couldn't read the followers list in that archive.")
    me = store.state["me"]
    archive_at, owner = None, None
    if manifest:
        try:
            m = _ytd_json(manifest) if "=" in manifest[:80] else json.loads(manifest)
            owner = str(m.get("userInfo", {}).get("accountId") or "") or None
            gen = m.get("archiveInfo", {}).get("generationDate")
            if gen:
                archive_at = time.mktime(time.strptime(gen[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except (ValueError, AttributeError, TypeError):
            pass
    if owner and owner != me["id"]:
        raise ValueError(f"That archive belongs to a different X account, not @{me['username']}.")
    with store.lock:
        s = store.state
        s["follower_ids"] = list(dict.fromkeys(ids))
        s["followers_loaded_at"] = archive_at or time.time()
        s["followers_source"] = "archive"
        s["me"]["followers_count"] = len(s["follower_ids"])
        if s["scanned_at"]:
            save_snapshot()
        store.save()
    return {"followers": len(ids), "archive_date": archive_at, **status()}


def api_snapshot_open(body):
    require_connected()
    name = body.get("file", "")
    if name not in {e["file"] for e in _snap_index()}:
        raise ValueError("That snapshot no longer exists.")
    snap = validate_snapshot(json.loads((SNAP_DIR / name).read_text()))
    with store.lock:
        open_snapshot(snap, name)
        store.save()
    return status()


def api_snapshot_import(body):
    require_connected()
    snap = validate_snapshot(body.get("snapshot"))
    keep = snap.pop("keep", None)
    with store.lock:
        if isinstance(keep, list):
            store.state["keep"] = sorted(set(store.state["keep"]) | {str(k) for k in keep})
        name = store_snapshot(snap)
        open_snapshot(snap, name)
        store.save()
    return status()


POST_ROUTES = {
    "/api/snapshot/open": api_snapshot_open, "/api/erase-data": api_erase_data, "/api/yeet": api_yeet, "/api/snapshot/import": api_snapshot_import,
    "/api/connect": api_connect, "/api/disconnect": api_disconnect, "/api/scan": api_scan,
    "/api/analyze": api_analyze, "/api/stop-task": api_stop_task, "/api/unfollow": api_unfollow,
    "/api/pause": api_pause, "/api/resume": api_resume, "/api/cancel": api_cancel,
    "/api/keep": api_keep, "/api/settings": api_settings,
}


def main():
    global PORT, API_BASE
    if DEMO:
        sys.path.insert(0, str(HERE))
        import demo_x
        API_BASE = demo_x.start()
    # Data loaded by an earlier version of Prune becomes the first snapshot.
    with store.lock:
        st = store.state
        if st["follower_ids"] is not None and not st["followers_loaded_at"]:
            st["followers_loaded_at"] = st["scanned_at"]  # earlier versions didn't record this
        if st["me"] and st["order"] and "following_count" not in st["me"]:
            st["me"]["following_count"] = len(st["order"])  # for cost estimates
            if st["follower_ids"] is not None:
                st["me"]["followers_count"] = len(st["follower_ids"])
        if st["me"] and st["scanned_at"] and st["order"] and not st.get("snapshot_file"):
            save_snapshot()
            store.save()
    if store.state["queue"]:
        write_paused.set()
        write_state.update(state="paused",
                           message=f"{len(store.state['queue']):,} unfollows were waiting when Prune last closed. Press Resume to continue.")
    start = int(os.environ.get("PRUNE_PORT", 8797 if DEMO else 8787))
    server = None
    for port in range(start, start + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            PORT = port
            break
        except OSError:
            continue
    if not server:
        sys.exit("Couldn't find a free port to run Prune on.")
    url = f"http://localhost:{PORT}/"
    if DEMO:
        print("DEMO MODE: made-up accounts, nothing touches your real X account or costs money.")
    print(f"Prune is running at {url}\nKeep this window open while you use it. Press Ctrl+C to quit.")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPrune stopped.")


if __name__ == "__main__":
    main()
