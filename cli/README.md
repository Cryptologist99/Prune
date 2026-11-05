# Prune command-line version

The original, simpler version of Prune with no browser interface. It finds the accounts you follow that match the rules in `config.toml`, writes them to a spreadsheet for you to review, then unfollows the ones you approve — slowly, so X doesn't flag you.

Most people will want the main app in the folder above. This version suits running from a terminal or a script.

## Setup (once)

1. Install Python 3.11 or newer, then the two libraries:
   ```
   pip install requests requests-oauthlib
   ```
2. Get your four keys from the X Developer Console, as described in the main README's **Getting your keys**.
3. Create a file named `.env` next to the script, filling in your keys:
   ```
   X_API_KEY=your Consumer Key
   X_API_SECRET=your Secret Key
   X_ACCESS_TOKEN=your Access Token
   X_ACCESS_TOKEN_SECRET=your Access Token Secret
   ```
   Keep this file private. It's excluded from git, so it won't be uploaded by accident.

## Use

1. **Set your rules** in `config.toml` (inactivity, follow-back, follower counts, keywords, and accounts to always keep).
2. **Scan:** `python unfollow_tool.py scan`
   Writes `candidates.csv`. Nothing is unfollowed.
3. **Review** `candidates.csv` in any spreadsheet app. Change `yes` to `no` for anyone you want to keep, and save it as CSV.
4. **Preview:** `python unfollow_tool.py run --dry-run`
5. **Run:** `python unfollow_tool.py run`
   Unfollows up to the daily cap (100 per rolling 24 hours by default) with random pauses. Run it again the next day to continue. It skips anyone already done.

Every action is recorded in `unfollow_log.csv`. The run stops immediately if X returns a rate-limit or any unexpected error.

## Notes

- On X's pay-per-use plan, each scan is billed per account read, about $0.001 for each account you follow, plus the same for each follower if follow-back rules or mutual protection are on. There are no snapshots here, so every scan reads both lists again; the main app avoids that.
- For protected accounts X doesn't share the last post, so they're never flagged as inactive.
- Re-scanning keeps your previous review as `candidates.previous.csv`.
- `candidates.csv`, `candidates.previous.csv` and `unfollow_log.csv` list accounts you follow, so they're excluded from git too.
