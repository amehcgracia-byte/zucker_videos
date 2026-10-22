# Show — work queue (22 October 2026)

Branch `claude/show-1022`, on top of 2.6.16 (`claude/show-after-44`, PR #45).
✅ done · 🔜 next · ⏳ queued · ❓ needs a decision.

## The person's list of 22 October

1. ✅ **ChatGPT sign-in not recognised.** The browser said "connected" but Codex never got it (it waits for the browser on a local port). Sign-in now uses Codex's device code (a link and a code typed on the website); the Connectors window notices when it is done.
2. ✅ **Names.** New downloads/imports start with the video's title or the file's name; the timeline and libraries show titles, file names or words instead of "Video clip" and codes.
3. 🔜 **Cuts are not clean**: at a zoom change, a switch to another video or an internet clip, a frame of another video flashes.
4. ⏳ **No duplicates of the same internet video**: download once; the library shows one entry per video (two at most when the time ranges differ).
5. ⏳ **Speed** of videos 10 %–200 % (100 % normal), in Clip settings and the timeline's right-click menu.
6. ⏳ **Space** = play/pause on the video; on the timeline bars = make the preview. "Render effect preview" renamed "Preview".
7. ⏳ **See videos, Clay and rendered layers on the stage** (a frame, ideally a few moving), with the same box as pictures: move, resize, crop. Clip size/position for clips that are not full frame.
8. ⏳ **"Balance audio levels"** instead of the smart mixer: keep every choice (narrator, clip sound, muted) and only even out loudness, taming sudden peaks (explosions).
9. ⏳ **Remove any plain background automatically** (green, blue, black, any single colour; pick a key colour the subject is not wearing). "Remove green screen" → "Remove background".
10. ⏳ **Four Show styles** after adding the videos: *Influencer* (fast cuts), *Explainer* (slower cuts), *Storytelling* (a narration with references, photos and clips found for what it tells — to build), *Gamer* (the talking video small, bottom right; other footage full frame).
11. ❓ **Remove Backstage** from the app to make it smaller, proving nothing in Show depends on it (Show uses some of its transcription/audio code: those parts stay).
12. ⏳ **Clearer names in the interface** (an ongoing pass; tell me the ones that confuse you).

## Still open from earlier lists

- Captions kept running during an inserted clip (to check in the final video).
- "Write the script together" as a back-and-forth with the director (only "follow my idea" exists).
- The "next, next… lottery" mode (a half-made edit with pictures and voices already generated).
- The edits made to «La prueba» after 19 October (kanji clips in scene 10) need redoing; the clips are in its library.
- Freesound originals (only previews download today).
- Windows: the package passes its self-test; the interface was not walked through on Windows.
- The 360 tests that fail on GitHub (28, inherited) and the open PR stack (#33–#45) to merge into `main`.
