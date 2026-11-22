# ClipFarm changelog

## 0.7.1: no more flashing console windows, no retry loops, proper pause and quit

### Fixed
- **A console window kept flashing and stealing focus while the engine ran.** The engine is a windowless app, so
  on Windows every program it starts (ffmpeg, ffprobe, the downloads yt-dlp runs) got its own new console
  window. With Windows Terminal as the default terminal, that window popped up every time.
  - Every external program now starts through one helper that hides its window (`CREATE_NO_WINDOW` plus a
    hidden `STARTUPINFO`) and never uses a shell. Errors go to the app's log instead of a console.
  - The same rule also covers programs that libraries start on their own.
- **"process_source: setting an array element with a sequence … inhomogeneous shape".**
  - Highlight detection crashed on any source shorter than about 9 seconds (short Twitch clips). One smoothing
    step returned more values than it was given, so the signals no longer lined up.
  - Every signal is now forced to the source's length, and short clips work.
- **Failing sources no longer loop.** A source that fails is retried at most 3 times (after 30 s, 2 min and
  10 min). It then shows as failed on Home, with its error and a **Retry** button, and the engine moves on.

### Pause and quit
- **Pause / Resume** button next to the engine status in the sidebar, and in the tray menu. Pausing stops
  starting new work and stops the ffmpeg runs in progress. Those jobs go back in the queue without using up a
  retry.
- **Settings › General › When I close the window:** keep running in the tray (the default) or quit. The first
  time it goes to the tray, a notification tells you where it went.
- **Quit** (tray menu, Settings, or the close button when set to quit) stops the engine and every ffmpeg it
  started. On Windows, child programs can't outlive the engine, even if it's killed.
- The Campaigns browser only opens when you click something in the main window. It closes when ClipFarm goes
  to the tray.
- Only one copy of ClipFarm and one engine can run at a time.

### Database
- Migration v9 (additive): sources gain `error`, `retry_at` and `failed_at`.

## 0.7.0 (Stage 4): music and sound, hooks and titles, effects, brand templates, learning, variants

### Hooks, titles and hashtags
- **Write hooks, titles and hashtags** (Hook & Title tab, and automatically in auto-pilot):
  - 3–5 hook options from the transcript and moment type, each a different angle (question, shock, stakes, a
    quote from the clip).
  - A title, description and hashtags written for each platform: TikTok, YouTube Shorts (#shorts),
    Instagram Reels and X.
  - The campaign's hashtags are always included, spelled the way the campaign spells them, plus the credit and
    disclosure.
- Works without an AI model too: hooks then come from the moment type and the best quote in the clip.
- When you export, Post & Submit gets the title, description and hashtags written for that platform.

### Zooms, effects, sound effects and B-roll
- **Auto zooms, effects and sounds** (Effects tab, and automatically in auto-pilot):
  - A punch-in on the payoff and small punch-ins on reactions ("NO WAY", "bro", "what"). These land on the beat
    when there's music.
  - Action adds a flash, shake, 💥, a boom and a whoosh before the payoff.
  - Funny adds 😂 and a pop. A fail adds 💀 and a bonk. A clutch adds 🏆, a riser and a ding.
  - Running it again only replaces the automatic ones; anything you added by hand stays.
- **Sound-effects pack:** 10 original sounds (whoosh, boom, impact, pop, ding, riser, bonk, rewind, shutter,
  sad horn), synthesised by ClipFarm and released CC0. Add any of them at the playhead from the Effects tab.
- **Your own SFX and B-roll folders** (Settings › Music). You confirm you have the rights. B-roll is added
  automatically when a word in the clip matches a file name (e.g. "cops" → `cops.mp4`), shown as a cut-away in
  the upper middle.
- Only the bundled pack and your folders are ever used: anything else is refused at render time.
- Reaction emoji buttons in the Effects tab (all bundled Noto images).

### Music
- **Pick a track for this moment** re-plans the music: a track matched to the moment type, with the drop on
  the payoff, beat markers and beat-aligned zooms. Choosing a track yourself re-plans it the same way.
- Music & Audio tab:
  - music volume, ducking (12–18 dB) and start point;
  - voice volume, speech enhancement and noise reduction;
  - a -14 LUFS final mix;
  - the trending-sound suggestion and "Export for trending sound".

### Brand templates (Settings › Brand)
- One template per account, covering:
  - caption style;
  - logo or watermark (corner, size, opacity);
  - intro card (first 0.8 s) and outro card (last 1.6 s) with your colours;
  - progress bar on/off and its colour;
  - campaign credit at the top or bottom.
- Auto-pilot applies the template of the account a clip is made for. In the Editor, pick a brand from the top
  bar.
- A brand logo also clears the "campaign needs a watermark" flag.

### Variants for A/B tests
- **Make variants** (Editor top bar) makes up to 3 extra versions of a clip. Each is its own clip with its own
  score and projected A$, labelled [B] [C] [D] and linked to the original:
  - **B:** shorter, starting closer to the payoff, with a different hook;
  - **C:** a different layout and a different hook;
  - **D:** a longer build-up.

### Learning (one system)
- ClipFarm now learns from:
  - 👍/👎 and the "why?" tags (new: bad captions, wrong music, bad framing);
  - what you keep, export and delete in the Editor;
  - real views compared with the prediction.
- After about 50 clips with feedback for a streamer or game, it reweights both the **highlight signals** and the
  **virality score** for them. The learned weights are blended with the defaults and trusted more as feedback
  grows.
- Settings › Highlights shows:
  - the virality weights, with "Reset to default";
  - what has been learned per streamer/game;
  - **predicted vs actual views**: the median ratio, how well the score tracks real views, and a per-clip table.

### Auto-pilot, still fully hands-off
- Detect → auto-edit → hooks and titles → brand → effects → Fix my clip → score → export → Post & Submit.

### Deliverables
- `docs/LICENCES.md` lists every bundled font, model, music track and sound effect with its licence. No B-roll is
  bundled.
- CI before/after samples now also include `reframe.mp4` (centre crop vs AI reframe) and `full_edit.mp4` (the
  full automatic edit).
- CI screenshots include the Editor in simple and advanced mode and its Captions, Layout and Campaign rules tabs.

### Database
- Migration v8 (additive): a `brand_templates` table, `accounts.brand_template_id`, and `clips.variant_of` /
  `clips.variant_label`.

## 0.6.0 (Stage 3): virality score, earnings per clip, and the Editor

### The Editor (replaces Review)
- New **Editor** in the menu, which is now six items: Home, New Project, Editor, My Clips, Earnings, Settings.
  My Clips keeps "All clips" (click one to open it in the Editor) and Post & Submit.
- Left: your clips, ranked by projected earnings (or by score, or newest), each with its score, projected A$,
  campaign, moment type and status (new / edited / ready / posted). Filter by campaign, streamer, moment type and
  status, or press **Post the 90+ first**.
- Centre: a 9:16 preview with live captions. Caption changes show instantly, with no render needed. Toggle the
  TikTok, Reels or Shorts safe zone to see where the app's buttons and text will sit.
- Simple mode (the default) has three big buttons: **Auto-edit**, **Fix my clip** and **Export**, plus the score
  card and any rule problems with their fix buttons. **Show more** opens the full editor:
  - Right-hand tabs: Transcript, Captions, Layout, Hook & Title, Music & Audio, Effects, Campaign rules.
  - Bottom: a timeline with video, captions, music, SFX and B-roll tracks. Drag the ends to trim, press S to
    split, select a part and press Delete to cut it out, and drag layout ◆ and zoom ● keyframes to move them.
- Top bar: undo/redo, preview quality, apply template, 👍/👎 with "why?" tags, export preset, Export, and
  **Send to Post & Submit**.
- Keyboard shortcuts: Space play/pause, J/K/L, S split, Delete, ←/→ one frame, Ctrl+Z / Ctrl+Y.
- Batch: tick several clips to apply a caption template, regenerate captions, fix them, export them all, or delete
  them.
- The trending-sound suggestion and "Export for trending sound" moved into Music & Audio.

### Virality score (0–100)
- Every clip gets a score from six parts: hook (does something happen or get said in the first 1–3 s), pacing
  (dead air, speech rate, cuts), retention (build-up, then the payoff, then a short ending), relevance (trending
  topics you add in Settings › Highlights), edit quality (captions, layout follows the rules, ducked music, right
  length for the platform) and the moment itself.
- Each score comes with a plain-English reason, e.g. "Strong hook, payoff at 0:12, ends 8 s after the payoff: cut
  the ending".
- The score updates after every edit, undo and redo.

### Earnings per clip
- Each clip shows projected A$ (low / likely / high) for its campaign. Expected views come from the clip's score
  (a 50 is a typical clip; a 90+ gets about 3.5× the views) on top of the platform's history. They go through the
  existing forecast, with the campaign's rate, minimum views, caps and fees.
- Clips are ranked for posting by projected earnings, and clips that break a campaign rule go last.
- Post & Submit uses the clip's own projection.

### Campaign rules in the Editor
- Red flags with one-click fixes for:
  - the overlay or streamer being cropped out (switches to a layout that keeps it in frame);
  - music on an original-audio-only campaign (removes it);
  - a platform the campaign doesn't pay for (switches the export);
  - a clip that's over the platform's length limit (trims it but keeps the payoff);
  - missing campaign hashtags or disclosure in the post text (adds them);
  - a hidden credit (shows it).
- A required watermark is flagged until a brand template supplies it (Stage 4).

### Fixes
- Highlights: chat during sub trains, gifted subs, raids, follows, bits and giveaways no longer counts as a
  reaction. The evaluation set now includes these traps, plus streamers shouting at chat and loud intermission
  music with chat vibing to it. Top-10 precision: **40% before → 100% after**.
- Rating a clip that has no streamer or game no longer fails.
- Saving only some highlight weights now keeps all of them adding up to 100%.
- "Remove silences" no longer reports gaps that were already cut.

### Database
- Migration v7 (additive): clips gain `virality`, `virality_reason`, `virality_parts`, `profit_low`,
  `profit_likely`, `profit_high`, `profit_platform`, `expected_views`, `rule_flags`, `scored_at` and `deleted_at`.
  There are also new tables `trend_topics` and `clip_signals` (keep / delete / edit / export, for learning in
  Stage 4).

## 0.5.0 (Stage 2): every clip is an editable project

### Edit projects
- Each clip is now an edit project: the source, the cuts, the layout, captions, hook, zooms, effects, music and
  campaign rules, saved as one list of edits. Nothing is baked in until you export, and you can always go back.
- Every change is saved as a version: undo, redo and a history list.
- Only what changed is re-rendered. Moving captions or swapping music reuses the already-cut video. A fast
  low-res preview is available for editing; exports use the full-quality GPU encoder (NVIDIA / AMD / Intel)
  and fall back to the CPU.
- Highlight detection and auto-pilot now make edit projects and export them straight away, so hands-off still
  works. Clips keep their source section so you can re-edit them later. Clips from 0.4 are converted the first
  time you open them.

### AI reframe
- Each shot is recognised as facecam + gameplay, gameplay only, people/IRL, or a screen/menu, and gets the
  right layout: split, smart crop that follows the action, speaker crop that follows whoever's talking,
  picture-in-picture facecam, or the whole frame on a blurred background.
- Layouts switch mid-clip when the scene changes; each switch is a keyframe you can change.
- The camera moves smoothly with no jitter: small moves are ignored and fast moves are eased.
- Facecam: found automatically, or set it yourself once and it's remembered for that streamer.
- A campaign's required overlay or streamer face is never cropped out. Breaking that rule is flagged with a
  one-click fix.
- **Fixed:** frame sampling for tracking used to snap to keyframes and see the same picture repeatedly, so the
  action crop barely moved. It now reads the exact frames.

### Transcript editing
- Delete words to cut them out of the video. "Remove filler words" and "Remove silences" are one click each.
- Change the clip's start and end from the transcript. Fix a wrong word and the captions update.

### Caption templates
- Every caption look is an editable template: font, text and highlight colours, outline, shadow, size,
  position, capitals, words on screen (1–3), animation (pop, bounce, karaoke, typewriter or none) and emoji.
- Save your own templates and set a default for each account or campaign.
- Captions follow layout switches (on the seam in split, normal position otherwise).
- Captions in other languages: ClipFarm detects the spoken language, and your AI model translates while keeping
  the timing.

### Export presets
- TikTok, YouTube Shorts, Instagram Reels and X (bitrate and length limits), plus "video + separate .srt captions".

### Under the hood
- Database update 6 (automatic): edit projects, version history and caption templates.
- 49 new tests: projects render identically after reloading, undo/redo, deleted words remove exactly the right
  audio, export presets, centring for every template × layout, reframe tracking and overlay safety, templates,
  translation timing, and old clips.

## 0.4.0 (Stage 1 of the editor rebuild): better captions, better highlights, better music

### Captions
- **Fixed:** captions ran off both edges of the screen when a line was long. Line wrapping was turned off, so
  a 3-word line could be wider than the video. Every caption now has an exact centre point and is measured
  with the real font before it's placed. A Windows check in the installer's self-test measures the result.
- New default look: big heavy text (bundled free fonts: Montserrat, Anton, Archivo Black), 1–3 words at a
  time, the spoken word highlighted with a quick pop, thick outline and soft shadow, never more than 2 lines,
  nothing on screen during silences.
- Kept clear of the TikTok / Reels / Shorts buttons and caption area. In split layouts the captions sit on
  the line between facecam and gameplay; if a campaign needs its overlay visible, captions go under the picture.
- Styles: Bold Pop, Hype Yellow, Clean White, Minimal (Settings › Captions). Options for capitals, swear words
  (keep / mask / mask + bleep), filler words, and emoji on keywords (off by default).
- The hook text uses the same style at the top of the screen.
- Review: see the captions on the clip, move them up or down, fix a wrong word, switch style, and re-render in
  seconds (clips now keep an unfinished copy, so only the captions are redone).

### Highlights
- Finds funny and exciting moments, not just loud ones. ClipFarm now listens for laughter, screams, cheering,
  explosions, gunfire, crashes and sirens (Google's YAMNet sound model, free for commercial use). It also
  reads chat reactions compared with that channel's usual chat, motion, flashes and screen shake, community
  clips, and an AI rating of what was said.
- Scores down menus, loading screens, maps, BRB screens, calm chatting and music on its own. A constantly
  loud streamer no longer counts as a highlight.
- Every clip gets a type: Funny, Action/Explosion, Rage/Reaction, Clutch/Skill, Fail or Wholesome.
- Clips start a moment before the build-up and end after the reaction settles.
- Settings › Highlights: Moment focus (Balanced / Funny first / Action first), adjustable weights.
- 👍 / 👎 plus quick "why?" tags in Review. After about 50 ratings for a streamer or game, ClipFarm reweights
  its signals to match your taste and real view counts ("Reset to default" any time).

### Music
- Only licensed music: 8 original ClipFarm tracks (yours to use, can't be claimed by anyone), your own music
  folder (with a reminder that you're responsible for its rights), and room for a licensed library later.
- Picked to fit the moment (funny → playful, action → hype, fails → ironic). It drops on the payoff, gets
  quieter while anyone talks (12–18 dB, your choice), lifts on reactions, and every clip is levelled to
  −14 LUFS. The same track isn't used twice in a row on an account.
- Campaigns that say "original audio only" or "no music" never get music. You can also switch music off per clip.
- Trending sounds: keep a list of names (Settings › Music). ClipFarm suggests one in Review and Post & Submit
  and makes a "for trending sound" export without its own music, so you add the sound in TikTok/Instagram.
  It never puts a trending song in the video.

### Under the hood
- Database update 5 (automatic, keeps everything): editable caption data, moment types, ratings, music
  library, trending list, "no music" campaigns.
- New tests for caption centring in every layout and source size, safe zones, word sync, scoring, ducking,
  loudness and licensing, plus a labelled evaluation set (top-10 precision, old vs new).

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
