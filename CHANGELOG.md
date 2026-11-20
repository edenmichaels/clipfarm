# ClipFarm changelog

## 0.3.0 — 20 Nov 2026

The app is simpler: five menu items, a three-step New Project, and campaigns that set themselves up.

### Campaigns import themselves (only when you ask)
- New **campaign browser** inside ClipFarm (Settings › Campaigns › Open campaign browser). It opens Content Rewards;
  you sign in once and stay signed in.
- Go to a campaign, or the Discover list, and press **Import this page** on the ClipFarm bar at the bottom of the
  window. ClipFarm reads only that page. On a list it imports every campaign on it at once.
- Your AI model (Ollama, or the one set in Settings) fills in anything the page rules missed: rate, minimum views,
  max per clip, prize pool and target, budget left, end date, platforms, hashtags, credit, disclosure, on-screen
  rules and whether Australians are paid. Anything it can't back up from the page text is marked **needs confirming**.
- Importing a campaign you already have updates it and shows what changed, e.g. "Rate: $1.00/1K → $1.50/1K".
- After each import: "Imported 6 campaigns: 2 new, 3 updated, 1 ended."
- Pasting a page still works (Settings › Campaigns › Paste instead).
- ClipFarm never visits Whop or Content Rewards by itself, never runs imports in the background, and never presses
  Join, Submit or any other button there. You do that yourself. Tests check all three.

### Clicking a streamer finds their campaign
- Click a streamer and their campaign is matched straight away ("Matched to KreekCraft GTA 6 Clipping").
- If ClipFarm isn't sure (for example a Kick channel with the same name), it asks you once and remembers the answer.
  Matches you've already confirmed are never asked again.
- The streamer panel shows the campaign, its rules, projected earnings and whether you've joined.

### Streamers sorted by campaign and money
- The streamer list is now three sections, each sorted by projected A$ per clip (hover for the low–high range):
  **Joined** (ready to clip), **Worth joining** (pays above your minimum, with a one-line reason and an
  **Open to join** button), and **No campaign** (collapsed; still asks before clipping).
- Campaigns that have ended, are out of budget, are ending soon or don't pay Australians drop out of Worth joining.
  Expand "not worth joining right now" to see why.
- After you press Join on the site, ClipFarm picks it up on your next import, or tick **I've joined**.
- "Why here?" still shows the full ranking breakdown.

### New Project in three steps, filled in for you
1. Pick a game, or **Auto-pilot: let ClipFarm choose**.
2. Pick streamers. Your best joined campaigns are already ticked.
3. Review and start: run length, clips per day and platforms, with one summary line of everything filled in, e.g.
   "3 streamers · 3 campaigns · posting to TikTok, Shorts, Reels · Whop: grandtheftclipper".
- Every project gets your Whop / Content Rewards account, every posting account (including ones you add later),
  each streamer's campaign with its hashtags, credit, disclosure and overlay rules, and only the platforms that
  campaign allows.
- Your last project's choices become the defaults for the next one.

### A simpler app
- Menu: **Home, New Project, My Clips, Earnings, Settings**.
  - Review and Post & Submit are tabs in **My Clips**.
  - Campaigns, Rewards programs, Setup health and Logs are tabs in **Settings**.
  - The "Fix my setup" banner still shows on Home when something's wrong.
- Home shows one **New Project** button, running projects with progress, clips waiting for review, clips to
  submit and earnings this week. Totals and storage are under "Show details".
- Same spacing, cards, buttons and colours on every screen; fewer borders; one main button per screen; empty
  screens say what to do next. Nothing was removed.

### Under the hood
- Database update 4 (automatic, keeps everything): remembers your match answers, what changed in each import and
  where it came from. Matches you confirmed in 0.2 count as answered.

## 0.2.0 — 19 Nov 2026

### Your keys survive updates and reinstalls
- API keys and sign-ins are still kept in Windows Credential Manager ("ClipFarm"), and now also in an encrypted
  backup at `%APPDATA%\ClipFarm\vault.dat`. The backup is locked to your Windows account, so it's useless on any
  other PC. If a key goes missing from Credential Manager, ClipFarm puts it back automatically on start.
- Keys you saved in 0.1 are copied into the backup the first time 0.2 starts.
- Uninstalling leaves your settings, database and keys alone. Only the uninstaller's "Delete the application
  data" box (unticked by default) removes them, and when it's ticked it removes everything, keys included.
- New versions install over the top, and in-app updates are ready to switch on. You never need to uninstall to update.
- Settings → Backup: export everything to one password-protected file and import it on a new PC.
- Keys never appear in logs, the database, settings, crash output or the diagnostics file, and a test checks this.

### Campaigns, projected earnings and auto-pilot
- New **Campaigns** screen. Paste a Content Rewards campaign page to import it; ClipFarm reads the rate, minimum
  and maximum payout, budget, pool size and view target, hashtags, on-screen rules, allowed platforms and the
  streamer's channel. Anything it couldn't read is flagged **needs confirming**.
- Your 12 joined campaigns are pre-loaded. KreekCraft, Dylan Page and AsianJeff are pay-per-view ($1/1K), not
  prize pools, because that's what their campaign pages say.
- Campaigns are matched to the right Twitch/YouTube/Kick channel automatically. When ClipFarm isn't sure, it asks
  you, and that streamer isn't clipped until you say yes. Matched streamers become "Program member".
- **Projected earnings** in A$ for every streamer × campaign × platform: per clip, per day, next 7 and 30 days,
  each with a low–high range. Based on your `market_forecast.py` model (launch hype fading, the clipper flood,
  per-clip minimums and caps, approval rate, budget left, prize-pool odds, fees and the exchange rate).
- Pay-per-view campaigns rank above prize pools unless a pool really projects higher.
- **Auto-pilot** (Campaigns screen): picks which campaign streamers to clip, how many clips each, and where to post,
  within your limits. It switches when a campaign runs out of budget, ends, or a better one appears, and logs why.
  It never clips a streamer with no campaign. Clipping one by hand asks you first.
- **Earnings** screen: projected vs actual per campaign and platform, plus pending, approved and paid. Once you have
  2+ weeks of results, "Refit from my results" tunes the model to your accounts.
- Each streamer has a "Why?" button that breaks its score into parts.

### Clips follow each campaign's rules
- Required hashtags (e.g. #DylanPage), credit and disclosure are added to every clip's caption file.
- If a campaign needs the streamer's overlay kept in frame (KreekCraft), ClipFarm switches to the full-frame
  layout. If it needs the streamer visible (Dylan Page), it keeps the facecam. If it can't be sure, the clip is
  flagged for review.

### Posting and submitting
- Your accounts are pre-loaded: TikTok @vira1gta6clips, YouTube @gta6viralclips-v5y, Instagram @gta6.viralclipsss,
  and Content Rewards user grandtheftclipper. No passwords are stored.
- New **Post & Submit** screen. Approve a clip, let ClipFarm pick natural posting times, post it, paste the link,
  then tick "Submit to [campaign] on Content Rewards". A post can never be submitted twice.
- Safety rules: the same clip (or the same video re-rendered) is never posted twice to one account. Posts are
  spread through the day within your daily limit, and YouTube stays under Google's ~6 uploads/day quota.
- YouTube Shorts upload automatically when your channel is connected. TikTok, Instagram and X are posted by you
  until their APIs allow it (see Setup Health for why).

### Setup Health and "Fix my setup"
- New **Setup Health** screen. Every check has a green, yellow or red light, a plain-English explanation and,
  where possible, a one-click fix. Checks cover the engine, storage, keys, FFmpeg, the caption font, GPU, the
  speech model, Twitch, YouTube, the YouTube upload sign-in, Kick, TikTok, Instagram, X, Ollama and campaigns.
- Checks run quietly on every start. If something needs fixing, one banner appears at the top of the app.
- "Copy diagnostics" gives you a report to paste when asking for help (keys removed).

### Fixes
- **The engine stopping work over time.** When a job failed for the last time, the error notification crashed
  its worker thread, so ClipFarm slowly stopped processing jobs. Workers now survive any error.
- **VOD clips failing to render.** Highlight start and end times were saved as raw bytes (numpy numbers), so
  rendering a VOD moment crashed.
- **The app going blank when the engine crashes.** The engine now restarts automatically, and the app shows
  "Reconnecting" in the meantime.
- **Captions failing for some Windows usernames.** Usernames with an apostrophe (e.g. O'Brien) broke FFmpeg's
  caption filter. Paths are no longer passed through the filter.
- **YouTube quota running out within an hour of a project.** Looking up YouTube VODs used the expensive search
  call every 10 minutes for every streamer. It now uses the uploads playlist, about 30× cheaper, and YouTube
  discovery is cached.
- **Kick never searched.** A game picked from Twitch had no Kick category, so Kick discovery never ran. ClipFarm
  now looks the game up on every connected platform.
- **Reconnecting YouTube could save the sign-in under the wrong account.**
- **Repeated fetch jobs.** Fetch jobs no longer pile up when a project tick comes round before the last fetch ran.
- **One bad caption failing the whole clip.** A transcription failure (offline on first use, no model) no longer
  fails the clip; it renders without captions instead.
- **Clear error messages.** Expired or revoked Google sign-ins, rate limits, used-up quotas, wrong keys and being
  offline now each say what happened and what to do.
- The speech model now downloads to `%APPDATA%\ClipFarm\models`, so it can be checked and re-downloaded.
- FFmpeg and fonts are found from the install folder the app passes to the engine. CI checks this by installing
  the built installer into a folder with spaces and an apostrophe and running a self-test with no Python or
  FFmpeg on PATH.

### Database
- Migration v2/v3 only adds columns and tables. Nothing is deleted or rewritten. Programs and streamer links from
  0.1 keep working: they're treated as joined and confirmed.
