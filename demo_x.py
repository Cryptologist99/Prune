"""
A pretend X API for trying Prune without a real account.

Run Prune with --demo (or use the "Start Prune demo" launcher) and it starts this
in the background. Everything here is made up: about 260 fake accounts you "follow",
a few hundred fake followers, and fake recent posts. Nothing touches the real X and
nothing costs money. Unfollowing a demo account removes it from the fake following
list, so a refresh reflects it.

Edit freely: change ACCOUNT_COUNT, add bios, or add NFT-style accounts below.
"""
import json
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ACCOUNT_COUNT = 240          # ordinary made-up accounts you follow
SEED = 7                     # change for a different, but repeatable, set of accounts
TWITTER_EPOCH_MS = 1288834974657
ME = {"id": "42", "username": "demo_you", "name": "Demo You"}

FIRST = ["Maya", "Jon", "Priya", "Leo", "Ana", "Sam", "Iris", "Theo", "Noor", "Ben", "Cleo", "Ravi",
         "Zoe", "Omar", "Tess", "Kai"]
LAST = ["Okafor", "Lindqvist", "Rao", "Moreau", "Silva", "Chen", "Novak", "Haddad", "Brennan", "Ito"]
BIOS = ["Product designer. Coffee, cycling, type.", "Writing about cities and transit.",
        "Software engineer at a startup. Opinions mine.", "Science journalist covering climate.",
        "Indie game dev. Making a tiny farming game.", "Photographer, Western Massachusetts.",
        "Runner, reader, dad.", "Economics PhD student.", "Making bread and bad jokes.", ""]
NFT_PROJECTS = [
    ("Cosmic Apes Club", "10k generative PFP collection on Ethereum. Minted out. Holders get utility + roadmap. discord.gg/cosmicapes",
     "https://opensea.io/collection/cosmic-apes"),
    ("Pixel Pals", "Free mint live now. Whitelist open, join our Discord. #NFT #web3", ""),
    ("Moonvault DAO", "Genesis collection on Solana. Holders-only alpha. Floor price watchers.",
     "https://magiceden.io/marketplace/moonvault"),
    ("Glitch Gardens", "Generative art on-chain. 3,333 pieces. Allowlist via Discord.", "https://zora.co/glitch"),
    ("degenfren.eth", "gm. web3 builder. collector.", ""),
    ("Ordinal Owls", "Bitcoin ordinals inscriptions. Sold out in 4 min.", ""),
]


def _post_id(days_ago):
    ms = int((time.time() - days_ago * 86400) * 1000)
    return str((ms - TWITTER_EPOCH_MS) << 22)


def _iso(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - days_ago * 86400))


def _build():
    rnd = random.Random(SEED)
    users, next_id = [], [1000]

    def add(name, handle, bio, url, days, posts, followers, following, protected=False):
        next_id[0] += 1
        age = rnd.choice([3, 20, 45, 80, 200, 700, 2000, 4500])
        if days is not None and posts > 0 and age <= days:
            age = days + rnd.randint(30, 2000)  # an account can't have posted before it existed
        u = {"id": str(next_id[0]), "username": handle, "name": name, "description": bio,
             "protected": protected, "verified": rnd.random() < 0.08, "profile_image_url": "",
             "created_at": _iso(age),
             "public_metrics": {"followers_count": followers, "following_count": following,
                                "tweet_count": posts, "listed_count": 0}}
        if url:
            u["entities"] = {"url": {"urls": [{"url": "https://t.co/x", "expanded_url": url}]}}
        if days is not None and not protected and posts > 0:
            u["most_recent_tweet_id"] = _post_id(days)
        users.append(u)

    for name, bio, url in NFT_PROJECTS:
        for k, suffix in enumerate(["", " Labs", " Official"]):
            add(name + suffix, re.sub(r"[^a-z]", "", name.lower())[:10] + str(k), bio, url,
                rnd.choice([900, 700, 400, 120, 30, 2]), rnd.randint(200, 4000),
                rnd.randint(800, 60000), rnd.randint(5, 3000))
    for _ in range(ACCOUNT_COUNT):
        name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        posts = 0 if rnd.random() < 0.04 else rnd.randint(5, 30000)
        add(name, name.split()[0].lower() + str(rnd.randint(1, 999)), rnd.choice(BIOS), "",
            rnd.choice([0, 1, 3, 8, 20, 45, 90, 150, 200, 320, 500, 900, 1400]), posts,
            int(10 ** rnd.uniform(1, 6.5)), rnd.randint(20, 3000), protected=rnd.random() < 0.04)
    add("NASA", "nasa", "Exploring the universe.", "", 0, 60000, 88000000, 200)

    mutuals = [{"id": u["id"]} for u in users if rnd.random() < 0.35]
    strangers = [{"id": str(900000 + i)} for i in range(rnd.randint(150, 300))]  # follow you, you don't follow back
    return users, mutuals + strangers


FOLLOWING, FOLLOWERS = _build()
_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q, path = parse_qs(url.query), url.path
        if path == "/2/users/me":
            with _lock:
                metrics = {"following_count": len(FOLLOWING), "followers_count": len(FOLLOWERS), "tweet_count": 50}
            return self._json(200, {"data": dict(ME, public_metrics=metrics)})
        m = re.match(r"/2/users/42/(following|followers)$", path)
        if m:
            time.sleep(0.3)  # feel like a network
            with _lock:
                src = list(FOLLOWING if m.group(1) == "following" else FOLLOWERS)
            size = int(q.get("max_results", ["1000"])[0])
            start = int(q.get("pagination_token", ["0"])[0])
            page = src[start:start + size]
            meta = {"result_count": len(page)}
            if start + size < len(src):
                meta["next_token"] = str(start + size)
            return self._json(200, {"data": page, "meta": meta})
        m = re.match(r"/2/users/(\d+)/tweets$", path)
        if m:
            uid = m.group(1)
            rnd = random.Random(int(uid))
            n = int(q.get("max_results", ["20"])[0])
            repost_share = rnd.choice([0.05, 0.1, 0.2, 0.6, 0.9])
            reply_share = rnd.random() * (1 - repost_share)
            posts = []
            for _ in range(n):
                p, x = {"id": _post_id(rnd.randint(0, 300)), "text": "..."}, rnd.random()
                if x < repost_share:
                    p["referenced_tweets"] = [{"type": "retweeted", "id": "1"}]
                elif x < repost_share + reply_share:
                    p["referenced_tweets"] = [{"type": "replied_to", "id": "1"}]
                    p["in_reply_to_user_id"] = uid if rnd.random() < 0.3 else "777"
                posts.append(p)
            return self._json(200, {"data": posts, "meta": {"result_count": n}})
        self._json(404, {"title": "Not Found"})

    def do_DELETE(self):
        m = re.match(r"/2/users/42/following/(\d+)$", urlparse(self.path).path)
        if not m:
            return self._json(404, {"title": "Not Found"})
        with _lock:
            before = len(FOLLOWING)
            FOLLOWING[:] = [u for u in FOLLOWING if u["id"] != m.group(1)]
            if len(FOLLOWING) == before:
                return self._json(404, {"title": "Not Found Error", "detail": "Could not find user"})
        self._json(200, {"data": {"following": False}})


def start():
    """Start the pretend X in a background thread and return its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/2"


if __name__ == "__main__":
    print("Demo X API at", start(), "(Ctrl+C to stop)")
    threading.Event().wait()
