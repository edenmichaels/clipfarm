# ClipFarm (working title)

Desktop app that finds streamers who allow clipping, detects their best moments, and turns them into captioned vertical shorts. Architecture, schema, milestones, testing plan and limitations: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Get the installer

Every push to `main` builds a Windows installer automatically on GitHub:

1. Open the repo's **Actions** tab → click the latest green **build-windows** run.
2. Scroll to **Artifacts** → download **ClipFarm-installer** (a .zip) → unzip it.
3. Run `ClipFarm_0.1.0_x64-setup.exe`.

The installer isn't code-signed yet, so Windows may show **"Windows protected your PC"**. Click **More info → Run anyway**. (Code signing needs a paid certificate; see "Before a public release" below.)

To publish a proper release download link: create a tag like `v0.1.0` (Releases → Draft a new release → new tag). The installer is attached to that release automatically.

## Using it

1. The setup wizard walks you through: rules → storage folder → platform keys → graphics-card check.
2. **New Project** → pick a game → tick streamers marked *Allowed* / *Program member* → choose how long → output settings → **Start project**.
3. Clips appear in **Review Clips**. Approve them, then **Export MP4 + caption**.
4. Closing the window keeps ClipFarm running in the tray (bottom-right of the taskbar). Right-click the tray icon → **Quit** to stop everything.

Recommended: install [Ollama](https://ollama.com/download) and run `ollama pull llama3.1:8b` for free, private AI hooks and titles. Without it, ClipFarm uses simple template hooks.
The first clip with captions downloads the speech model (~250 MB for "small"), so it takes a few extra minutes once.

### Keys you'll need (all free)
| Service | Where | Used for |
|---|---|---|
| Twitch Client ID + Secret | dev.twitch.tv/console/apps → Register (OAuth redirect: `http://localhost`, category: Application Integration) | game list, live streamers, clips, VODs |
| YouTube Data API key | console.cloud.google.com → enable *YouTube Data API v3* → Credentials → API key | YouTube live discovery |
| Kick Client ID + Secret | kick.com/settings/developer | Kick live discovery |
| Google OAuth client | same Google project → Credentials → OAuth client ID → type **Desktop app** | uploading Shorts (next version) |

## For developers

### Engine (Python 3.11+)
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pytest -q                                            # needs ffmpeg on PATH
python -m clipfarm.main                              # API on http://127.0.0.1:47821 (no token in dev)
```

### UI (Node 20+, Rust stable)
```bash
cd app
npm install
npm run dev            # UI in a browser against the dev engine at :47821
npx tauri icon app-icon.png
npx tauri dev          # full desktop app (needs the engine binary, see below)
```
`tauri dev` expects `app/src-tauri/binaries/clipfarm-engine-x86_64-pc-windows-msvc.exe`; build it with step 3 of `scripts/build_windows.ps1`.

### Build the installer on your own PC
Install Python 3.12, Node 20, Rust (rustup) and Visual Studio Build Tools ("Desktop development with C++"), then in PowerShell from the repo root:
```powershell
./scripts/build_windows.ps1
```
Output: `app/src-tauri/target/release/bundle/nsis/ClipFarm_0.1.0_x64-setup.exe`.

### Before a public release
- **Code signing** (removes the SmartScreen warning): buy an OV/EV code-signing certificate and add it to the Tauri config (`bundle.windows.certificateThumbprint`) or use Azure Trusted Signing.
- **Auto-updates**: add `tauri-plugin-updater`, generate a key with `npx tauri signer generate`, and point the updater at this repo's releases.

### Defaults chosen (change in Settings)
Clip 15–60 s, 3 s lead-in / 2 s tail, up to 4 highlights per stream-hour, pickiness 55 · layout *auto* (facecam split if found, else action crop) · *bold pop* captions · Whisper *small* · Ollama `llama3.1:8b` · 50 GB storage cap, sources deleted after processing · 3 posts/day/account, 9am–10pm, ≥90 min apart · unknown-permission streamers blocked.
