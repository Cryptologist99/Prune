# Prune

Review everyone you follow on X in one sortable table, pick out the accounts you no longer want, and unfollow them at a safe, human pace.

Prune runs on your own computer and opens in your web browser. Your API keys, your account list and your unfollow history never leave your computer except to talk to X.

## What you need

- **Python 3.9 or newer.** Macs and most Linux machines already have it. On Windows, install it from [python.org/downloads](https://www.python.org/downloads/) and tick **Add python.exe to PATH** during setup. Nothing else needs installing.
- **Your own X developer app keys** (four values: Consumer Key, Secret Key, Access Token, Access Token Secret). Each person who uses Prune needs their own.

### Getting your keys

1. Sign in to the [X Developer Console](https://console.x.com) and open **Apps**.
2. Pick the app Prune will use:
   - **Create a new app** with **Create App**. This is the simplest choice, and it keeps Prune separate from anything else you've built.
   - **Or use an existing app** by opening it from the list. Regenerating its keys below stops anything else that uses that app until you give it the new keys.
3. On the app's **Keys & Tokens** tab, generate the **Consumer Key**, or choose **Regenerate** if the app already has one. Copy the **Consumer Key** and **Secret Key** right away; X only shows the secret once.
4. On the same tab, generate or regenerate the **Access Token**, and copy the **Access Token** and **Access Token Secret**.

If unfollowing ever fails with a permission error: on **Keys & Tokens**, open **User authentication settings**, set **App permissions** to **Read and write**, and save. The form also asks for an app type and two web addresses; any address works, such as your X profile link. Then regenerate the Access Token and enter the new token and secret in Prune. (X's documentation says unfollowing needs Read and write, but in testing it worked without it.)

You don't need the **Bearer Token**. It only allows reading public data, so it can't unfollow on your behalf.

Reading your following list requires an X API plan that includes it. If your plan doesn't, Prune shows X's error message when you load your list.

## Starting Prune

- **Mac:** double-click `Start Prune (Mac).command`. The first time, macOS may block it; right-click it, choose **Open**, then **Open** again.
- **Windows:** double-click `Start Prune (Windows).bat`.
- **Any system:** open a terminal in this folder and run `python3 prune.py`.

Your browser opens to Prune. Keep the small terminal window open while you use it; closing it quits Prune.

## Trying the demo

Double-click `Start Prune demo (Mac).command` or `Start Prune demo (Windows).bat`, or run `python3 prune.py --demo`. Prune opens with keys already filled in and about 260 made-up accounts, including some NFT projects, quiet accounts and accounts that don't follow back. Nothing touches your real X account or costs money, and unfollowing runs 10× faster so you can watch it work.

The demo keeps its own saved data in a `Demo data` folder, separate from your real data, and runs at a different address, so you can have both open at once. The fake accounts are defined in `demo_x.py` if you want to change them.

## Using it

1. **Connect.** Paste your four keys. Leave **Remember these on this computer** on to skip this next time.
2. **Load from X.** Nothing is read from X until you press **Load**. Prune shows how many accounts each list has and what reading it will cost. **Who I follow** is always loaded. **Who follows me back** is optional: it fills the **Follows you** column and the "don't follow me back" rule. **Snapshots and refresh** later offers the same choices, plus **Use my current snapshot** for who you follow, so you can load just your followers without paying to reload your following list. If you skip followers on a refresh, Prune keeps using the list from last time.
3. **Filter.** Turn on any of the rules in the sentence at the top, and edit the numbers:
   - haven't posted in *N* days (accounts that have never posted count too)
   - don't follow me back
   - joined less than *N* days ago (new accounts; the **Account age** column shows how old every account is)
   - have over / under *N* followers
   - mostly reply, *N*% or more (see below)
   - mostly repost, *N*% or more (see below)
   - look like NFT projects (see below)
   - mention your own words in their bio: type words or phrases separated by commas. Matching ignores capitals and looks at the start of words, so "crypto" also finds "cryptocurrency", but "art" won't match "startup". Matches are highlighted in the table.

   With two or more rules on, choose whether accounts must match **any** or **all** of them. The **Start from** buttons set up common combinations, including **NFT projects**. Cells that triggered a match are highlighted.
   Drag any number in the sentence left or right to change it, or click it to type. Turning on "don't follow me back" before your followers list is loaded opens the load options, with costs, so you can add it; nothing runs until you choose.
4. **Sort** by clicking any column heading. **Hide kept** and **Hide never posted** take those accounts out of view.
   **Quick select** picks the first *N* accounts shown, ranked by longest since last post, newest accounts, or oldest accounts. It respects your filters and skips kept accounts.
5. **Keep** accounts you never want to unfollow. Kept accounts can't be selected.
6. **Select** accounts with the checkboxes (the top checkbox selects everything shown), then press **Unfollow**.

### Snapshots: reopening data without paying again

Every time you load from X, Prune saves a dated snapshot on your computer. The line above the filters always says which snapshot you're looking at and how old it is. Accounts you've followed since then won't appear, and anyone you've unfollowed through Prune is left out automatically.

**Snapshots and refresh** lists your saved snapshots. Choose **Open, free** to switch to one without calling X. **Export** saves a snapshot as a file, and **Import a snapshot file** loads one on another computer (or after a reinstall) so you can skip the paid load there. Snapshots only open for the X account they were taken from.

Prune unfollows in the background, pausing between each one and taking breaks, and never does more than your daily limit in any 24 hours. If you select more than the limit, it carries on the next day as long as Prune is still open. You can pause or cancel at any time. If you quit, unfinished unfollows wait for you to press **Resume** next time.

**Yeet the rest** unfollows everything still waiting right away: it skips the daily limit and the breaks, pausing only 5–20 seconds between each. It asks you to confirm first, because unfollowing this many this quickly is the kind of bulk activity X says can get accounts limited. Prune still stops if X rate-limits or returns an error.

### What it costs

On X's pay-per-use plan, reading your own following and follower lists is billed as "owned reads", at $0.001 per account. A following list of 2,000 accounts costs about $2.00.

X bills each account only once per UTC day (which starts at 8 PM Eastern, or 7 PM during standard time). So a second load the same day is free, and anyone in both your following and followers lists is only billed once. Prune remembers what it read today and shows **Free today** where that applies.

### Followers from your X archive (free)

X's free data archive includes your full followers list. Request it in X under **Settings → Your account → Download an archive of your data**; it usually arrives within a day or two as a .zip file. In **Snapshots and refresh** (or on the first load screen), choose **Choose archive file** and pick the .zip, or the `data/follower.js` file inside it. Prune fills the **Follows you** column from it without any X charges, and the line above the filters notes the archive's date. The archive only lists account IDs for the people you follow, without their details, so that list still loads from X. Unfollowing has never shown up as a charge in testing. Prune keeps a running estimate in **Settings**; the X developer console at [console.x.com](https://console.x.com) has the exact figure. If X's rates change, update them in Settings so the estimates stay accurate.

### Reposts and Replies columns (off by default)

These are hidden at first because they're expensive: measuring them reads each account's recent posts at $0.005 per post, about $0.05 per account at the default 10 posts each. Turn them on with **Show posting-style tools** in Settings. Before anything runs, Prune shows the cost and asks you to confirm.

X doesn't report how an account posts, so Prune measures it. **Check posting style** reads each shown account's most recent posts (10 by default; the confirmation lets you pick 10, 20, 50 or 100, with the cost shown for each) and fills in two columns:

- **Reposts:** the share of those posts that are reposts of someone else's post. Quote posts, which add the account's own comment, don't count.
- **Replies:** the share that are replies to *other* people. Thread posts, which are technically replies to the account's own earlier post, don't count.

Both use the same total, so "Reposts 60%, Replies 25%" means the remaining 15% are the account's own original posts. Hover over a percentage to see the counts. The **Mostly reposts** preset shows accounts where at least half of recent posts are reposts.

This costs one API request per account, so filter down first if you follow a lot of people. Private and suspended accounts show as unavailable.

### NFT signs column

Prune looks through each account's name, bio and links for signs of an NFT project, such as "mint", "whitelist", "pfp", "10k", "holders", OpenSea or Magic Eden links, and Discord invites. **Likely** means several strong signs; **Weak** means only a passing mention, like "web3" in a personal bio. The matched words are listed so you can judge for yourself. The **NFT projects** preset shows every likely NFT account. To narrow it to dead projects, also turn on "haven't posted in N days" and switch to **all**.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| Unfollows per 24 hours | 100 | Hard limit in any rolling 24 hours (max 400) |
| Shortest / longest pause | 20–60 seconds | Random wait between unfollows |
| Break after this many | 25 | Take a longer break after this many in a row |
| Break length | 30 minutes | How long that break lasts |
| Posts to check per account | 10 | Default for the posting-style check; you can pick 10, 20, 50 or 100 each time |
| Cost per account in your lists | $0.001 | Used for load estimates and the spend total |
| Cost per post read | $0.005 | Used for posting-style estimates |
| Show posting-style tools | Off | Shows the Reposts and Replies columns and rules |

X doesn't publish an unfollow limit, but it does penalize "following and/or unfollowing a large number of unrelated accounts in a short time period". The defaults sit at the careful end of what people report as safe. Raising them is at your own risk.

## Privacy and security

- Prune only accepts connections from your own computer, and only from the page it served.
- Everything Prune saves (your keys if you tick **Remember these**, snapshots, kept accounts and history) is in the **Your data - do not share** folder inside your Prune folder. Delete that folder to remove it all. **Don't share it**: it holds your API keys, which aren't encrypted. If you pass Prune on to someone, share a fresh copy of the download instead of your own folder. When you update to a new version of Prune, copy this folder into the new Prune folder to keep your data. Settings shows the folder's full location.
- Earlier versions of Prune saved to a hidden `.prune-for-x` folder in your home directory; Prune moves that data into the new folder the first time it starts. In Settings, **Forget keys** deletes your saved keys but keeps your snapshots, kept accounts and history. **Erase data** does the opposite: it deletes snapshots, kept accounts, unfollow history and the spend tally, but keeps your keys. Export any snapshot you want to keep first.
- Kept accounts are saved separately from snapshots, so they stay kept whichever snapshot you open, and they travel with exported snapshots. **Hide kept accounts** above the table hides them from view.
- Prune talks only to X's official API (`api.x.com`). Profile pictures load from X, and fonts load from Google Fonts.
