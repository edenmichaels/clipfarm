# What ClipFarm bundles, and the licences

ClipFarm only puts into your videos things it is allowed to use commercially on TikTok, Instagram Reels, YouTube
Shorts and X. Anything it can't confirm is never added. Trending songs are never put in a video file: ClipFarm
only suggests a sound's name, and you add it inside TikTok/Instagram when you post.

## Fonts (caption styles)
| Font | Files | Licence | Source |
|---|---|---|---|
| Montserrat Black, Montserrat ExtraBold | `backend/assets/fonts/Montserrat-*.ttf` | SIL Open Font License 1.1 | github.com/JulietaUla/Montserrat |
| Anton | `Anton-Regular.ttf` | SIL Open Font License 1.1 | github.com/google/fonts (ofl/anton) |
| Archivo Black | `ArchivoBlack-Regular.ttf` | SIL Open Font License 1.1 | github.com/google/fonts (ofl/archivoblack) |

The OFL lets you bundle the fonts with software and use them in videos, including commercially. The licence
texts are next to the fonts (`OFL-*.txt`).

## Emoji images (reaction emojis, keyword emojis)
| What | Licence | Source |
|---|---|---|
| 17 PNGs from Noto Emoji (`backend/assets/emoji`) | Images: Apache License 2.0 (font tooling: OFL 1.1). Both files are included. | github.com/googlefonts/noto-emoji |

## Models
| Model | Used for | Licence | Source |
|---|---|---|---|
| YAMNet (AudioSet sound classifier), converted to ONNX at build time | Laughter, cheering, screaming, explosions, gunshots… per second | Apache License 2.0 | github.com/tensorflow/models (research/audioset/yamnet) |
| Whisper weights via faster-whisper (CTranslate2) | Transcripts and word timings | MIT (weights and faster-whisper) | github.com/openai/whisper, github.com/SYSTRAN/faster-whisper |
| Your local LLM through Ollama (optional) | Rating moments, hooks, titles, translation | Depends on the model you choose. Llama, Qwen, Mistral and Gemma have their own licences; check yours | ollama.com |
| OpenCV Haar face cascade (if present in your OpenCV install) | Facecam/speaker detection | BSD / Apache 2.0 (OpenCV) | opencv.org |

## Music (bundled pack, 8 tracks)
| Track | Moods | Licence |
|---|---|---|
| Goofy Footsteps, Banana Peel, Full Send, Redline, Heist Mode, Last Stand, Afterglow, Sad Trombone Hours (ClipFarm Originals) | funny, hype, tense, epic, chill, sad/ironic | **CC0 1.0.** Composed in code by ClipFarm (`backend/clipfarm/music/compose.py`). There are no samples or recordings from anyone else, so there is nothing for Content ID to match. |

Your own music folder: you're responsible for those licences. ClipFarm shows a warning, records "your file" as the
licence for each track, and you can switch any track off. No online library is connected: none with a usable
API was available for this build.

## Sound effects (bundled pack, 10 sounds)
| Sound | Licence |
|---|---|
| Whoosh, Boom, Impact hit, Pop, Ding, Riser, Bonk, Rewind, Camera shutter, Sad horn | **CC0 1.0.** Synthesised by ClipFarm from oscillators and noise (`backend/clipfarm/music/sfx.py`), with no samples. |

Your own SFX folder: only files in the folder you chose are ever mixed in. You confirm you have the rights when you
add the folder.

## B-roll
No B-roll is bundled. B-roll only comes from **your own folder** (Settings › Music). You confirm you have the rights
when you add it, and anything outside that folder is refused at render time.

## Test data only (not shipped in the app)
| What | Licence |
|---|---|
| Highlight evaluation sounds (laughter, explosions, gunshots, cheering, screaming), fetched by `scripts/build_eval_set.py` in CI | CC0 1.0 (Freesound). Each sound's page is checked for CC0 at build time and listed in the set's `SOURCES.md`. |
| Speech in the evaluation set | Generated with espeak-ng (GPL tool; the generated audio is not covered by the GPL) |
