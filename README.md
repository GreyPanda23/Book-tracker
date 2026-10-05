# 📚 My Book Tracker

A personal app to keep track of the books you want to read, have read or dropped,
get suggestions for what to read next, and see where each book is cheapest in
Dubai and online in the UAE. Prices are checked automatically every Sunday.

- **On your phone** (and any browser) through Streamlit Community Cloud.
- **Price checks run on your Mac** every Sunday. If the Mac was off, they run the
  next time you log in.
- Your books live in a small database file that is kept in your **private** GitHub
  repository, so the phone app and your Mac always see the same data.

---

## What's in the app

Use the **Library** switch at the top of the sidebar to flip between **📚 Books** and
**🎌 Manga**. Every page below works the same way in both libraries and only shows the
one you picked. Manga data comes from AniList (free, no key); manga are listed by
series, so they have no ISBN. Weekly price checks for manga match by title; add the
ISBN of a specific volume (Edit) to price that exact one.

| Page | What you can do |
|---|---|
| 📚 **My Books** | Tabs for *To Read / Currently Reading / Read / Dropped*, change status anytime, star rating, notes, target price for alerts |
| ➕ **Add a Book** | Search by title, **author name** (lists their books) or ISBN (Google Books, falls back to Open Library). Title, author, ISBN, cover and genre fill in automatically |
| 🧭 **Discover by Genre** | Pick one or several genres (match *all* or *any*, plus an optional keyword) and browse popular books in them. Filter by release year, author, minimum rating, hide books you already have, sort by rating/popularity/date, then add. **🎲 Surprise me** gives 5 random well-liked books you don't have yet |
| 💡 **Suggestions** | 5 similar books for any book (same author + same subjects). Optional AI picks from Claude based on your ratings. One-click **Add to To Read** |
| 💰 **Prices** | For each book: **In-Store (Dubai)** and **Online** tables, cheapest highlighted, price-history chart, store health |
| 📊 **Stats** | Books read this year, drop rate, average rating, books per month, top genres |

### Stores

| Store | In-store (Dubai) | Online | How it's checked | Status |
|---|---|---|---|---|
| Magrudy's | ✅ 8 Dubai branches (list refreshed automatically) | ✅ | The website's own data feed, by ISBN | ✅ Tested live |
| Jashanmal | ✅ Dubai malls | ✅ | The website's own data feed, by ISBN | ✅ Tested live (the site slows down frequent visitors, so each check is paced) |
| Amazon.ae | – | ✅ | Headless browser, product page by ISBN | ✅ Tested live |
| Noon | – | ✅ | Headless browser, search + product page | ✅ Tested live (sometimes slow) |
| Kinokuniya | ✅ The Dubai Mall | ✅ | Headless browser, product page by ISBN | ⚠️ Blocks cloud servers, so it couldn't be tested while building. Run the store check on your Mac to confirm |
| Virgin Megastore | ✅ 8 Dubai stores | ✅ | Only books you paste a Virgin link for | ⚠️ Its robots.txt forbids searching, so it can't find books by itself |
| WHSmith | – | – | – | ❌ Not included: no UAE website with prices |

"In-store" prices use the store's website price, as agreed. Stock shown is what the
website reports; ring the branch before a special trip.

Every check is polite: it reads each site's `robots.txt` first and waits a few
seconds between requests. It also recognises "are you a robot?" pages and skips
that store for the rest of the week rather than retrying. Amazon's terms of use
don't allow automated access. The low volume (your To Read books, once a week)
keeps the risk small, but you can switch any store off in `config/stores.yaml`.

---

## One-time setup (about 45 minutes)

You'll do everything once, in this order:

1. [GitHub: private repo + access token](#1-github-private-repo--access-token)
2. [Get the app onto your Mac](#2-get-the-app-onto-your-mac)
3. [Your keys and passwords (the `.env` file)](#3-your-keys-and-passwords-the-env-file)
4. [Try it on your Mac](#4-try-it-on-your-mac)
5. [Turn on the weekly price check](#5-turn-on-the-weekly-price-check)
6. [Put it on your phone (Streamlit Community Cloud)](#6-put-it-on-your-phone-streamlit-community-cloud)

> **Terminal** is the Mac app where you type commands (press ⌘+Space, type
> *Terminal*, press Enter). In the steps below, type or paste each grey line into it
> and press Enter.

### 1. GitHub: private repo + access token

**a) Make sure the repository is private.**
Open https://github.com/GreyPanda23/Book-tracker, then **Settings** → scroll to the
bottom → *Danger Zone* → **Change visibility** → **Private**. (Skip this if it already
shows a *Private* label next to the name.)

**b) Create an access token** so the app can save your book list to the repo:

1. GitHub → click your picture (top right) → **Settings** → **Developer settings**
   (at the very bottom of the left menu) → **Personal access tokens** →
   **Fine-grained tokens** → **Generate new token**.
2. **Token name:** `book-tracker`. **Expiration:** the longest offered (put a reminder
   in your calendar to renew it).
3. **Repository access:** *Only select repositories* → pick **Book-tracker**.
4. **Permissions** → *Repository permissions* → **Contents** → **Read and write**.
5. **Generate token**, then copy the token (starts with `github_pat_`). You only see
   it once, so keep it in your password manager.

> Never paste tokens, keys or passwords into chats or emails. They only go into
> the `.env` file on your Mac and the *Secrets* box on Streamlit.

### 2. Get the app onto your Mac

1. **Install Python:** download the latest *macOS 64-bit universal installer* from
   https://www.python.org/downloads/ and run it. Then, in the Python folder it opens,
   double-click **Install Certificates.command**.
2. **Install GitHub Desktop** from https://desktop.github.com and sign in with GitHub.
3. In GitHub Desktop: **File → Clone Repository** → choose **GreyPanda23/Book-tracker**
   → set *Local path* to your home folder so it becomes `/Users/<you>/Book-tracker` → **Clone**.
   (Keep it out of *Documents* or *Desktop*: macOS privacy rules can stop the
   weekly job from reading those folders.)
4. In Terminal, run these one at a time:

   ```bash
   cd ~/Book-tracker
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

   The last line downloads the headless browser used for Amazon/Noon/Kinokuniya/Virgin
   (about 150 MB).

### 3. Your keys and passwords (the `.env` file)

In Terminal:

```bash
cd ~/Book-tracker
cp .env.example .env
open -e .env
```

TextEdit opens. Fill in the lines, save and close:

| Line | What to put | Needed? |
|---|---|---|
| `GITHUB_TOKEN=` | the `github_pat_…` token from step 1 | **Yes** (for phone sync) |
| `GITHUB_REPO=` | already filled: `GreyPanda23/Book-tracker` | Yes |
| `GOOGLE_BOOKS_API_KEY=` | your Google Books key (`AIza…`) | Optional: without it Open Library is used |
| `ANTHROPIC_API_KEY=` | a Claude API key (see below) | Optional: only for AI suggestions |
| `EMAIL_ADDRESS=` | `pranavcirs10@gmail.com` | Optional: for price-drop emails |
| `EMAIL_APP_PASSWORD=` | a Gmail App Password (see below) | Optional: for price-drop emails |
| `APP_PASSWORD=` | any password you choose for opening the app | Recommended |

**Google Books key** (you already made one): it's under
https://console.cloud.google.com → *APIs & Services* → *Credentials*.

**Gmail App Password** (for price-drop emails):
1. Turn on 2-Step Verification: https://myaccount.google.com/signinoptions/two-step-verification
2. Go to https://myaccount.google.com/apppasswords, type the name `Book Tracker`, then click **Create**.
3. Copy the 16-letter password into `EMAIL_APP_PASSWORD=`. Spaces don't matter.

**Claude API key** (optional, pay-as-you-go, typically well under 1 US cent per
recommendation request):
1. Sign up at https://console.anthropic.com and add a small amount of credit under *Billing*.
2. **API Keys** → **Create Key** → copy it into `ANTHROPIC_API_KEY=`.

### 4. Try it on your Mac

```bash
cd ~/Book-tracker
source .venv/bin/activate
streamlit run app.py
```

Your browser opens the app. Add a few books and mark some **To Read**. Press
**Ctrl+C** in Terminal to stop it.

Check which stores work from your Mac (takes 3–5 minutes, nothing is saved):

```bash
python -m scripts.check_stores
```

Each store prints its prices, or a line starting with `!` explaining what went wrong.
If a store shows `blocked`, it refused the automated visit. The others still work,
and you can switch that store off in `config/stores.yaml` (`enabled: false`).

Run a real price update now instead of waiting for Sunday:

```bash
python -m scripts.update_prices
```

### 5. Turn on the weekly price check

```bash
cd ~/Book-tracker
bash scheduler/macos/install.sh
```

After that:
- it runs every **Sunday at 09:00**. If the Mac is asleep, it runs when it wakes.
- if the Mac was **switched off** on Sunday, it runs the next time you log in
  (it also checks every 3 hours, and only ever runs once per week).
- the log is in `~/Book-tracker/logs/update_prices.log`.
- to run it immediately: `launchctl kickstart -k gui/$(id -u)/com.booktracker.weekly`
- to remove it: `bash scheduler/macos/uninstall.sh`

### 6. Put it on your phone (Streamlit Community Cloud)

1. The app code must be on your repo's **main** branch. It's currently on the branch
   `claude/exciting-heisenberg-r0m887`; merge it on GitHub via the *Pull requests* tab
   (or ask Claude to open the pull request for you).
2. Go to https://share.streamlit.io and **sign in with GitHub**. Allow access to
   private repositories when asked.
3. **Create app** → deploy from GitHub → Repository **GreyPanda23/Book-tracker**,
   Branch **main**, Main file path **app.py**. Pick a nice URL, e.g. `my-books`.
   (If Streamlit asks who may view the app, either choice is fine: the app has its
   own password from `APP_PASSWORD`.)
4. **Advanced settings** → Python version **3.11** → in **Secrets**, paste (with your values):

   ```toml
   GITHUB_TOKEN = "github_pat_..."
   GITHUB_REPO = "GreyPanda23/Book-tracker"
   APP_PASSWORD = "your-app-password"
   GOOGLE_BOOKS_API_KEY = "AIza..."
   ANTHROPIC_API_KEY = ""
   ```

5. **Deploy**. It takes a few minutes the first time.
6. On your iPhone, open the app's address in **Safari** → **Share** →
   **Add to Home Screen**. It now opens like an app.

If the app hasn't been used for a while, Streamlit puts it to sleep. Tap
*"Yes, get this app back up"* and wait about 30 seconds.

---

## Everyday use

- **Add a book:** ➕ Add a Book → search → pick the status → **Add**.
- **Change status / rating / notes / target price:** 📚 My Books → the book → **Edit**.
- **Price alerts:** set *"Alert me below (AED)"* on a To Read book. When the weekly
  check finds it cheaper (and in stock), you get one email per new drop.
- **Virgin Megastore:** find the book on virginmegastore.ae, copy the address, then
  💰 Prices → the book → *🔗 Virgin Megastore product link* → paste → **Save link**.
- **Wrong edition priced?** Edit the book and change its ISBN to the edition you want.
  Prices marked *"Title (other edition?)"* were matched by title because that exact
  ISBN wasn't sold there.

## Troubleshooting

| Problem | Fix |
|---|---|
| Phone app shows *"Saved on this device, but not uploaded to GitHub"* | The token is wrong or expired. Make a new one (step 1b) and update Streamlit Secrets and `.env` |
| No prices appear | Only **To Read** books are checked. Look at `logs/update_prices.log`, or run `python -m scripts.check_stores` |
| A store keeps saying `blocked` | The site is refusing automated visits. Try again next week, or set `enabled: false` for it in `config/stores.yaml` |
| `Chromium not installed` in the log | Run `source .venv/bin/activate` then `python -m playwright install chromium` |
| The weekly job didn't run | Check `logs/launchd.err.log`. Re-run `bash scheduler/macos/install.sh`. Make sure the folder isn't inside Documents/Desktop |
| Book search says *Google Books unavailable* | Normal without a key or if its daily limit is reached. Open Library is used automatically |

---

## For the curious: how it's built

```
app.py                     Streamlit entry point (navigation + password)
views/                     the five pages
booktracker/
  db.py                    SQLite tables: books, prices (history), store_links, runs, fetch_log
  sync.py                  keeps books.db on the repo's "data" branch (phone <-> Mac)
  book_search.py           Google Books + Open Library
  suggestions.py           similar books + optional Claude picks
  fetchers/                one module per store + shared politeness/matching code
    base.py  matching.py  browser.py  registry.py  apify_stub.py
    magrudys.py  jashanmal.py  amazon_ae.py  noon.py  kinokuniya.py  virgin.py
  prices.py                runs all stores; one broken store never stops the others
  alerts.py  stats.py  charts.py
config/stores.yaml         store list, Dubai branches, on/off switches, delays
scripts/update_prices.py   the weekly job      scripts/check_stores.py  store health check
scheduler/macos/           launchd schedule + install/uninstall scripts
tests/                     automated tests (pytest)
```

- Run the tests: `python -m pytest` (add `-m live` to also call real websites).
- **Moving one store to Apify later:** set `backend: apify` for that store in
  `config/stores.yaml` and fill in `booktracker/fetchers/apify_stub.py`. Nothing else changes.
- The database lives on the repo's `data` branch (`books.db`), so saving never
  redeploys the app. Every save is a commit, which gives you a full backup history.
