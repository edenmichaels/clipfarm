# ClipFarm changelog

## 0.8.9
- Chat-first detection: moments come from chat tags (clip requests > laughing > shock), counted per unique chatter vs each channel's own baseline, plus viewer-made Twitch clips. Video/vision analysis removed.
- Clips start before the chat spike (default 20 s) using each streamer's delay learned from viewer clips, and end when chat calms; the local AI judges from transcript + chat only.
- Editable chat tags, spike size and delay per streamer (Settings, or "chat tags" on any Live Watcher row); "Why this moment?" shows the chat evidence.
- Auto-pilot now runs on the Live Watcher: watches the best live campaign streamers (pay-per-view first), up to 3 at once (fewer under CPU/disk pressure), with run hours and a log.
- Live Watcher list: sort by live, viewers, projected A$, campaign type, chat activity, kept today, hit rate or name; filters remembered.

## 0.8.8: presets, variations, and live dots

- **Preset bar in the Editor:** six presets, each card showing your clip in that style: Streamer Corner, Split Screen, Bold Box, Clean Minimal, Hype and Facecam Focus. Click one to apply it, or tweak it and "Save as my style". All keep the rules: fixed crops, static captions, Montserrat, no music.
- **Make variations:** make 1 to 10 versions of a clip in one preset or a mix. Each has its own hook from the clip's words, plus a different trim, layout, colour or punch-in, and a label saying what's different.
- **Cheap previews first:** variations show as quick low-res previews. Only "Keep" or "Export" renders a full clip into My Clips, linked to the original with its own viral score and A$. "Keep all", "Keep selected" and "Discard the rest" are there too.
- **Live dots in every streamer picker:** green for live (viewers, time live, game, with a warning when it's not the project's game), grey for offline, hollow for "status unavailable". Live streamers go to the top, and the list refreshes every minute while it's open.
- **Cheap live checks:** Twitch uses one call per 100 streamers, Kick its official API, and YouTube a ~2-unit check per channel (never search.list) with a daily cap. Database change: migration v15.

## 0.8.7: Live Watcher, a different hook on every clip, no auto music

- **Live Watcher (the new default):** watches the streamers you pick while they're live, using chat, new viewer clips, and laughter from a 3-minute buffer. When something happens, an AI judge scores it; anything rated 7/10 or higher (after checking viewer clips and chat 10 minutes later) becomes a clip in My Clips. Everything else is deleted.
- **Live Watcher panel:** pick streamers, see who's live, close looks this hour, clips kept, CPU and disk use, and a Pause button. Your thumbs up/down in the Editor teach the judge.
- **Different hooks:** every clip gets its own hook from its own transcript and never repeats one of your last 50. If the AI model isn't running you get a warning, and the hook is written from the clip's words instead of "Wait for it...".
- **Music off:** no style or auto-edit adds background music any more. Exports use the stream's own audio, and music is a manual Editor option. Existing clips and saved styles were switched over too (migration v14).
- **Post & Submit:** TikTok now has an "Add a trending sound in TikTok before posting" step. Sound campaigns you've joined (like "Tokyo Girl") show as a reminder on clips they fit; the song is never put in the video. "Scan a past stream" (the old VOD scan) is still there.

## 0.8.6: manage My Clips, and post more from inside the app

### My Clips
- **Sorted by viral score** (highest first) by default. You can also sort by projected A$, date made, streamer, campaign, status (not posted / posted / submitted) or length. ClipFarm remembers your choice.
- **Select clips:**
  - each clip has a checkbox;
  - Shift+click selects a range, Ctrl+click adds or removes one clip;
  - "Select all" (everything in the current filter), "Select none" and "Invert selection";
  - shortcuts: Ctrl+A selects all, Esc clears, Delete deletes.
- **Selection bar:** "N selected · Delete · Export · Send to Post & Submit · Apply style · Clear".
- **Delete:**
  - works on one clip, a selection or everything;
  - the confirmation says how much space it frees ("Delete 24 clips? This frees 1.8 GB.");
  - if some were posted, it says their stats are kept.
- **Recently deleted:**
  - deleted clips stay 7 days, with Restore and Empty now;
  - after that their videos, previews, thumbnails and edit projects are removed for good;
  - posted clips keep their link, campaign, views and earnings, so Earnings, submissions and learning stay right;
  - source videos that other clips use are never deleted; the storage clean-up removes them once nothing uses them.
- **Clips being processed:** deleting a clip that's rendering or uploading stops that first (an upload stops before its next chunk).

### Posting
- **YouTube Shorts (automatic):**
  - shows "N uploads left today" and moves extras to the next day by itself;
  - Google now allows 100 uploads a day; the old limit of 6 is gone;
  - uploads go in 8 MB chunks;
  - the title gets #Shorts; the description gets the campaign caption (hashtags, credit, disclosure); "made for kids" is set to no;
  - reminds you before Google's 7-day "Testing" sign-in runs out;
  - the post link goes into the submission checklist automatically.
- **TikTok (your inbox):**
  - ClipFarm uploads the finished clip and TikTok sends a notification to your TikTok inbox;
  - open it, add a trending sound if you want, paste the caption (copied for you) and post;
  - TikTok allows 5 drafts a day; ClipFarm shows "2 of 5 TikTok drafts used today" and queues the rest;
  - when you post it, the link fills in automatically if TikTok gives it, otherwise you're asked to paste it;
  - Direct Post is still in the code, switched off.
- **Instagram Reels (automatic if your account qualifies):**
  - ClipFarm checks your account (Business or Creator, linked to a Facebook Page, with publish permission) and shows the setup steps if it doesn't qualify;
  - it uploads straight from your PC, publishes the Reel with caption and hashtags, and checks the daily limit first;
  - otherwise it uses the Post Kit.
- **X:** posts directly if you add paid X API keys; otherwise it uses the Post Kit.
- **Post Kit** ("Post to TikTok / Instagram / YouTube / X"), in one click:
  - copies the caption;
  - opens the upload page;
  - opens Explorer with the video selected so you can drag it in;
  - shows a short checklist for that platform;
  - has a "Paste post link" box that fills in the submission checklist.
- **Send to phone:**
  - shows a QR code so your phone can download the clip on your home Wi-Fi;
  - the page has Download and Copy caption;
  - the link expires after an hour, has a random token, and only works on your home network;
  - optional "Phone folder" copies a clip and its caption into your Dropbox or Google Drive folder.
- **One posting screen:** Post & Submit lists every clip waiting to post, with each platform's status: Auto-posted ✓ / In TikTok inbox / Post Kit needed / Posted / Submitted.
- **"Post all ready clips":** posts what can go automatically and makes Post Kits for the rest. Posts are still spread across the day, follow each platform's limits, and never go to the same account twice.
- **"Check accounts":** checks each account's sign-in, permissions, account type and quota without posting anything. It also shows the audit table of what changed.

### Fixed
- A cancelled post no longer blocks scheduling that clip again (it used to crash).
- YouTube quota errors no longer retry every 5 minutes; they wait for the next quota day.

### Database
- **Migration v13:**
  - Recently deleted;
  - posting stage (automatic / TikTok inbox / Post Kit), TikTok publish id, upload time;
  - per-account posting mode and the last account check.
- Clips deleted before 0.8.6 go into Recently deleted with a fresh 7 days.

### Proven by tests
`tests/test_v086.py` (28 tests, every platform mocked) covers:
- select all → delete → restore;
- posted stats kept after delete;
- disk space freed with no files left behind;
- YouTube upload and quota;
- TikTok inbox, the 5-a-day limit and status tracking;
- Instagram eligibility, fallback and resumable upload;
- the Post Kit caption, page and file;
- Send to phone (token, expiry, home network only);
- nothing posted twice to the same account.

## 0.8.5: steady framing, a proper facecam corner, and static captions

The moment picker was already good; this update makes the edits look like a real clipper made them.
(The Companion work moves to 0.9.x.)

### Framing: nothing pans any more
- Every shot uses **one fixed crop**. A layout change is an instant hard cut.
- Removed: auto panning, drifting, tracking moves and sliding crops, in auto-edit and in every style.
- **Zooms are off by default.** Switch on "Zoom cuts" (in a style, or Settings › Editing) and they're instant cuts to a centred zoom and back. No animated zoom, Ken Burns or shake.
- Screen shake and whip transitions are gone from every style.
- **Old projects** are converted to one steady crop per shot (their average framing), with a one-line notice in the Editor.

### Facecam
- **Exact webcam crop:** the detector snaps to the inside of the webcam border, so there are no overlays, alerts or borders in the crop.
  - If the detector isn't sure, ClipFarm uses the box you saved for that streamer.
  - If there's no saved box, the Editor asks once ("Draw the facecam") and remembers it for that streamer.
- **New default layout, "Streamer Corner":** gameplay full frame plus the facecam in a corner (about 34 % wide) with a thin border, rounded corners and a soft shadow.
  - Top-left or top-right is picked per clip (whichever covers less important detail) and then stays put.
  - It stays inside the safe zone and never covers captions, the title or the credit.
  - Drag it to the other corner or resize it in the Editor, then "Save as my default for this streamer".
- **Focus on the streamer:** when they're the moment (talking, a big reaction, quiet gameplay), the clip hard-cuts to the full facecam.
  - Cuts happen at sentence starts, never mid-word, at least ~3 s apart.
  - They show as markers on the timeline that you can move or delete.
- **No facecam:** gameplay only, one fixed crop per shot.
- **Split layout** is still there as an option, with no panning.

### Captions: rebuilt
- **One fixed place:** centred near the bottom (baseline at 77 % down), 840 px wide, max 2 lines. They never move with the layout and never overlap the facecam.
- **No motion:** no pop, bounce, scale, slide, typewriter or fade. The spoken word turns yellow (or switch highlighting off). 1–3 words per caption by default, up to 6 words / 2 lines if you choose.
- **Look:** Montserrat Black, uppercase, ~86 px cap height, white with a 9 px black outline and a soft shadow.
  - Montserrat is bundled under the SIL Open Font License (licence file included).
  - You can add your own licensed fonts (Komika Axis, The Bold Font, Anton, Bebas Neue) from a fonts folder in Settings; ClipFarm doesn't bundle them.
  - The title uses the same font, fixed at the top, no animation.
- **Timing:**
  - captions follow Whisper's word timings and break at phrases;
  - nothing shows during silence;
  - no gaps or overlaps;
  - nothing is shorter than 0.3 s.
- **Vocabulary:** an editable list in Settings, pre-filled with your campaign streamers' names and GTA 6 terms. Whisper is primed with it and captions are corrected with it.
- The profanity setting works as before.
- **Preview and export use the same caption renderer.**

### Styles
- Every built-in style follows these rules. "Streamer Corner" is the default when a facecam is found.
- Your saved custom styles are converted (animated captions and pans become static). The Styles page lists which ones changed.

### Removed code paths
- The old caption system:
  - pop, bounce, karaoke and typewriter animations;
  - caption positions (Placement, POSITIONS, position_y);
  - shrinking long captions (layout_chunk);
  - the old ASS builder (build_ass);
  - placement by layout segments.
- The `/edit/{id}/canvas` and `/clips/{id}/caption-preview` routes; the preview uses the export renderer.
- Beat and punch zoom pulses (punch_zoom), automatic shakes and whips, and pan smoothing (smooth_path).

### Database
- **Migration v12:**
  - converts saved edit projects to fixed framing;
  - converts saved custom styles and records which ones changed;
  - marks auto-detected facecam boxes so they're re-checked, while boxes you drew stay as they are.

### Also fixed
- The daily post limit counted in UTC, so it reset at 10 am in Brisbane. It now uses local time.

### Proven by tests
`tests/test_v085.py` covers:
- every style keeps each shot still, and zoom cuts are instant;
- caption baseline and centre within ±0.5 % in every layout;
- glyphs don't change size or position between frames;
- no overlap with the facecam, title or safe zone;
- preview == export;
- Montserrat and its licence;
- migration v12.

## 0.8.4 (step 5): full audit

The full bug table is in `docs/reports/v0.8-audit.md`. It lists 15 issues, each with its area, problem, cause, fix and test.

### Fixes
- **After a crash, power cut or Task Manager kill:**
  - jobs that were running are re-queued;
  - ClipFarm's leftover render folders in Temp are removed on start-up;
  - unsaved Editor changes are offered back.
- **Editor at 125 % / 150 % Windows display scaling:**
  - the menu gets narrower;
  - toolbar tools become icon-only (hover for names);
  - the inspector stacks beside the preview, so the preview stays large.
- **B-roll and sticker thumbnails** from your own folders now show in the Editor.
- **Screenshot uploads** are capped at 25 MB / 40 MP.
- **Zoom amounts in Styles:** old projects whose zoom amounts were stored as factors now scale correctly.

### Proven by tests
- **Every API route needs the per-launch token.** Only `/health`, the fonts and the OAuth callback are public.
- No shell is used anywhere.
- Bundled fonts, emoji and SFX each carry their licence.
- Nothing is fetched from TikTok or Instagram.
- On Windows, killing the engine also kills its ffmpeg.
- **Soak:** `scripts/soak.py` loops style, edits, preview, proxy and export. It checks memory, handles, temp folders, child processes and render speed. CI runs it for 30 s. For the 6–12 h run on your PC:

  ```
  python scripts\soak.py --minutes 600 --out soak.json
  ```

### Reports
- `docs/reports/v0.8-step1-root-causes.md`
- `docs/reports/v0.8-detection-report.md`
- `docs/reports/v0.8-audit.md`
- `docs/reports/v0.8-styles-before-after.png`
- `docs/reports/v0.8-captions-before-after.png`

## 0.8.3 (step 4): the Editor is the app

### A real video editor, and the screen ClipFarm opens on
- **Show more / Show less is gone.** The Editor now has a professional layout:
  - **Top:** a toolbar with undo/redo, Split, Freeze, Text, Sticker, Transition, Effects, Auto-edit, Fix my clip, Variants, brand template, 👍/👎, export preset, Export and Send to Post, plus a save indicator.
  - **Left:** the **Bin** (your clips with filters and batch actions, and **+ Append** to add a clip after the open one), plus **Styles**, **Music**, **SFX**, **B-roll** and **Stickers** tabs.
  - **Centre:** the 9:16 preview, drawn by the export renderer. **Drag text, stickers and the caption block right on the preview.**
  - **Right:** an **inspector** for whatever you selected, then the clip's Captions, Title, Layout, Colour, Audio, Effects, Transcript, Rules and Sequence.
  - **Bottom:** a **multi-track timeline** with Video, Text, Stickers, Captions, FX, Music, Voice, SFX and B-roll tracks. It zooms with Ctrl+wheel, + / − / 0 and a slider. Bars and markers can be dragged, and text and sticker bars resize from their edges.
- The Editor is the **start screen** and first in the menu. It **reopens where you were**: the same clip, playhead, panels and timeline zoom.

### New editing tools
- **Multi-clip sequences:** append other clips after this one. Each keeps its own cuts and layout, and its captions come along.
- **Speed ramps:** select a part and set it from 0.25× to 4×. The audio is time-stretched, not pitched.
- **Freeze frames:** F holds the frame at the playhead, 0.2–10 s, with silence under it.
- **Text layers:** font, size, colour, outline or white/black box, rotation and timing, on screen wherever you drop them.
- **Stickers:** the bundled reaction emojis or images from your own sticker folder. Each has size, rotation and timing.
- **Transitions:** dip to black, flash, zoom punch and whip (zoom plus shake).
- **Colour:** brightness, contrast, saturation and warmth.
- **Crop, zoom, pan and rotate.** Rotating scales up automatically, so no black corners show.
- **Audio keyframes:** volume points on the voice and music tracks, gliding between them, plus a "duck music here" button.
- **Facecam editor:** drag or resize the cam box on the real source frame. Every split, picture-in-picture and full-reaction part uses it.
- **Keyboard shortcuts** for everything, with a **shortcut sheet** you open with ? or the ⌨ button.
- **Autosave and crash recovery:**
  - Every change is saved as a new version.
  - A change still being saved is kept as a draft.
  - After a crash or a forced close, the Editor offers **Restore it**.

### Fixes
- **Auto zooms were 2× too big.** Zoom amounts were stored as scale factors, for example 1.16, but rendered as "+116 %", so automatic punch-ins zoomed to about 216 %. They're now +16 % as intended. Old projects are read correctly.
- The **+ Punch-in zoom** button had the same problem.
- The "crash" event tag no longer fires on scene cuts or facecam movement without an impact sound, or on the word "hit". This is what caused "Crash at 0:00" in moment reasons.

### Under the hood
- **Timeline engine:** segments now cover the main clip, speed, holds and sequence items, and every source↔output mapping goes through them.
- **Base render:** one input per item, plus `setpts`, `atempo` chains, `tpad` freezes, and silence for holds and silent items.
- **Finish pass:**
  - crop/zoom/rotate;
  - one per-frame `eq` for colour and dip/flash transitions;
  - text layers in the libass track;
  - image stickers;
  - rotation for emoji stickers;
  - volume envelopes for voice and music.
- **New API routes:**
  - `GET /edit/{cid}/source-frame`;
  - `POST /edit/{cid}/sequence` and `DELETE /edit/{cid}/sequence/{idx}`;
  - `GET /stickers`;
  - `GET` and `PUT /editor/session`.

  The Editor view now includes `segments` and `caption_y`.
- **Tests:** `test_v08_editor.py` covers:
  - the timeline maths;
  - a speed ramp + freeze + appended-clip render with an exact duration;
  - text, sticker, transition, colour and rotate pixels;
  - preview vs export;
  - audio keyframes muting and restoring;
  - the caption handle;
  - the old zoom factors.

  A new contract test covers the editor routes.
- **Screenshots:** CI now captures the Editor at 100 %, 125 % and 150 % display scaling.

## 0.8.2 (step 3): Trending Styles

### Five ready-made looks, picked for you
A **style** sets the whole look of a clip in one go: the layout, the captions, the title, the pacing, the effects and the music level.
- **Streamer Split:** the facecam sits on top, taking 35–45% of the height, with the gameplay below and a clean seam between them. Captions sit on the seam, 1–3 words at a time, with the spoken word in yellow. A white-box title shows from the first frame.
- **Full Reaction:** the streamer fills the screen. Use it for IRL, Just Chatting and big reactions.
- **Gameplay Only:** no facecam. Captions go in the lower third, with an outline title.
- **Meme / High Energy:** one word at a time and hard punch-in zooms. A flash, a shake and sound effects hit on the payoff.
- **Clean:** minimal captions with no highlight, no title, a gentle zoom and nothing flashy.

Which style a clip gets, checked in this order:
1. the clip's own choice;
2. the campaign's default;
3. the account's default;
4. the streamer's default;
5. your global default;
6. automatic: Streamer Split when a facecam is found, Full Reaction for Just Chatting/IRL, otherwise Gameplay Only.

An explicit caption-template default you already set still wins over the style's captions.

### Tweak everything, save your own
- Every part of a style is adjustable:
  - **Layout:** the layout, the facecam height (35–45%) and the seam line.
  - **Captions:** position (seam, upper, centre or lower), words on screen (1–3), highlight on/off and its colour, and UPPERCASE.
  - **Title:** the look (white box, black box, outline or none), the whole clip or the first 1–8 s, and top or seam.
  - **Pacing:** zoom strength and pause cutting. Pause cutting never touches the payoff or a tagged event.
  - **Effects:** flash, shake, SFX and emojis.
  - **Audio:** music on/off and its level.
- Save any style as your own, and set it as the default for a streamer, an account, a campaign or everything.
- Editor › **Style**: apply a style to the open clip. You can undo it like any other edit.
- Campaign rules still win: if a campaign requires the overlay on screen, the full frame is kept whatever the style says.

### Style references (your own screenshots, never scraped)
- Settings › Styles › **Style references**: paste, drop or pick a screenshot, then add notes and tags. Each reference keeps the date you added it, and **"Style last updated"** shows how fresh your references are.
- **Make a style from this** reads a screenshot and fills in a style you can then tweak:
  - Image analysis finds the facecam/gameplay seam and its height, where the captions sit, how many words are on screen, the highlight colour, and a white or black title box.
  - If you've set up a local vision model, it fills in what pixels can't tell, such as meme energy or uppercase.
- Tags on your references, such as fail or rage, also feed this week's trending events in the moment finder.

### Under the hood
- **Migration v11:** new `styles` and `style_refs` tables.
- **New API routes:**
  - `/styles` (GET and POST), `DELETE /styles/{sid}` and `PUT /styles/defaults`;
  - `POST /edit/{cid}/style`;
  - `/style-refs` (GET and POST), `PUT` and `DELETE /style-refs/{rid}`, `GET /style-refs/{rid}/image` and `POST /style-refs/{rid}/make-style`;
  - `POST /styles/from-image`.
- **Render changes:**
  - the split facecam height is configurable;
  - a new `cam_full` layout;
  - an optional seam line;
  - libass box styles for titles;
  - a whole-clip title option;
  - caption positions.
- **Windows CI fixes:**
  - The zoom crop no longer fails on newer FFmpeg builds.
  - The caption-centre test no longer depends on font metrics.
- **Tests:** `test_v08_styles.py` has 27 tests. They cover:
  - every style with a facecam, without one, and with a campaign overlay rule;
  - the split ratio landing exactly on the seam;
  - validation;
  - the default resolution order;
  - pause cuts that keep the payoff;
  - effects;
  - making a style from a split screenshot and from a gameplay screenshot;
  - merging the vision model's answer;
  - the reference library;
  - auto-edit picking the style.

  Contract tests cover every new route.
- `docs/reports/v0.8-styles-before-after.png` shows the v0.8.1 default next to each style.

## 0.8.1 (step 2): viral moment detection that learns from what actually goes viral

### How moments are picked now
- Every candidate moment gets a **viral score (0–100)** from a new model. The streamer's reaction counts most,
  then chat, then what happened in the game and how it lands together:
  - **Reaction:** laughing, screaming, disbelief ("no way", "bro", "what"), a sudden jump in the streamer's voice,
    and big facecam movement.
  - **Chat:** busier than that channel's normal, laughing or hype emotes, "clip it".
  - **What happened:** event tags per second, combining sounds, visuals, what's said and (optionally) a local
    vision model in Ollama. The tags are crash, explosion, gunfire, police/wanted/arrest, death/wasted,
    stunt/flip, fall/ragdoll, NPC fight, vehicle steal, plane/heli, boat, chase, fail/glitch, easter egg/animal,
    and money/heist.
  - **Surprise:** something unexpected followed within ~3 s by a reaction.
  - **Talk:** the AI rates it funny/chaotic/shocking; serious or sad talk counts against it.
  - **Context:** community clips nearby, and this week's trending events for the game.
- Combinations count extra. **An explosion with the streamer laughing scores far higher than an explosion on its
  own.**
- **Never picked:** menus, loading/BRB screens, cutscenes or calm talk with no reaction, sad/serious talk, long
  calm driving, and music on its own. Loud game audio or music alone never counts.
- Each moment gets a type (Funny, Chaos/Fail, Action + Reaction, Rage, Clutch/Skill or Wholesome) and a
  plain-English reason, e.g. *"Explosion at 0:06, streamer screams and laughs, chat 6× normal, matches this
  week's trending 'police'"*.
- Clips start 1–3 s before the setup (the thing that happens), keep the full reaction, and end once it peaks.
  If there was a long setup (an RP conversation, a heist plan), **Make variants** offers a "with setup" version.
- The viral score now carries the most weight in the clip's virality score, so projected earnings and the
  posting order use it too.

### Learning from real viral clips
- Every day ClipFarm collects each game's **most-viewed Twitch clips of the week**, using the official Twitch API
  and **only for streamers you're allowed to clip**:
  - It reads the matching part of the VOD (low-res) and stores its features and views as a "viral" example.
  - Random and mundane windows from the same VODs are stored as "not viral" examples.
- It also learns from your posts' real views vs the prediction, your 👍/👎, and what you keep, export and delete.
  YouTube Shorts titles of channels you're allowed to clip count towards trending events (official API; nothing
  is downloaded).
- **Never downloads or scrapes TikTok or Instagram.**
- The model retrains automatically. Each game gets its own model once it has 40+ examples; before that, the
  research-based starting model is used.
- **Settings › Highlights › Viral moment model** shows:
  - examples per game;
  - accuracy on a held-out day, compared with the starting model;
  - when it last trained;
  - this week's trending events;
  - buttons to Refresh, Retrain and **Reset to default**.
- **Make detection report on a real VOD:** runs the detector on the newest VOD of a streamer you're allowed to
  clip. It shows how many of that VOD's top community clips the old and new rankings find, plus the 20 picked
  moments with their reasons and event tags.

### In the Editor
- Filter clips by what happened and how people reacted, e.g. *Explosion …with the streamer laughing*.
- Event tags show on clip cards and as ▼ markers on the timeline.
- **Why this moment?** shows each feature's contribution to the viral score.

### Fixed
- Saving one setting in a group (e.g. one highlight option) no longer wipes the group's other options.
- Learning examples without a VOD time could be stored twice.

### Database
- Migration v10 (additive):
  - new tables `viral_examples` and `viral_models`;
  - moments gain `viral_score`, `tags`, `tag_timeline`, `features` and `contributions`.

## 0.8.0 (step 1 of the editor rebuild): red box, moving captions, wrong moments

### Fixed
- **Red box around every video.** It was the Editor's safe-zone guide: a red striped overlay that was switched on
  by default. It was never in your exported files (a new test checks every layout × caption style for red or
  guide-line pixels). Guides are now off by default and drawn as a thin neutral dashed outline labelled "guide
  only".
- **Captions jumping around.**
  - Captions used to move whenever the AI reframe switched layout mid-clip (split ↔ crop ↔ speaker), and when a
    caption changed between one and two lines. Each clip now has **one fixed caption position**.
  - The pop animation now scales only the spoken word, around its own centre; the rest of the line never moves.
- **The preview now matches the export exactly.** The Editor preview is drawn by the same renderer as the export
  (FFmpeg + libass): a low-res render of your current edit, plus exact stills of the current frame while it
  updates.
- **Sad or random moments being picked.**
  - The AI rating used to reward "emotional" and "scroll-stopping" talk. It now rewards funny, chaotic and
    surprising moments, and counts sad or serious talk against a moment.
  - Chat sending "F / o7 / Sadge" no longer counts as hype.
  - GTA street crowd noise no longer counts as cheering, and calm driving no longer counts as action.
  - Crying and sighing are now detected and penalised.
  - Details and measurements: `docs/reports/v0.8-step1-root-causes.md`.
- Previews no longer pop up a "clip ready" desktop notification.
- A preview rendered while you kept editing was wrongly marked as up to date.

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
