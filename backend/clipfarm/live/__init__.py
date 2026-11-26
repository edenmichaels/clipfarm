"""Live Watcher (v0.8.7): finds funny moments while a permitted streamer is live, cheaply.

Tier 0 (always on while live, ~0 cost): Twitch chat over anonymous IRC (rate vs the channel's normal rate,
          laugh emotes/words, "clip it") + Twitch Get Clips every ~60 s (a new viewer clip is the strongest trigger)
Tier 1 (cheap): a rolling ~3-minute buffer of the live HLS segments saved as-is (no re-encoding), audio decoded
          at 16 kHz and run through YAMNet for laughter/screams
Tier 2 (on a trigger, max ~6/hour/streamer): cut 60-90 s, Whisper, LLM judge (transcript + chat + 3 frames)
Tier 3 (10 minutes later): did viewers clip it, did chat stay hyped -> final score; >= threshold -> My Clips
"""
