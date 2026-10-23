# Show — work queue (22 October 2026)

Branch `claude/show-1022`, on top of 2.6.16 (`claude/show-after-44`, PR #45).
✅ done · 🔜 next · ⏳ queued · ❓ needs a decision.

## The person's list of 22 October

1. ✅ **ChatGPT sign-in not recognised.** The browser said "connected" but Codex never got it (it waits for the browser on a local port). Sign-in now uses Codex's device code (a link and a code typed on the website); the Connectors window notices when it is done.
2. ✅ **Names.** New downloads/imports start with the video's title or the file's name; the timeline and libraries show titles, file names or words instead of "Video clip" and codes.
3. ✅ **Cuts are not clean** (the talking video is an earlier export with 238 cuts burned in: old cuts next to new ones now hold a clean frame; close-ups and inserts snap to cuts; close-ups start on whole frames): at a zoom change, a switch to another video or an internet clip, a frame of another video flashes.
4. ✅ **No duplicates of the same internet video**: download once; the library shows one entry per video (two at most when the time ranges differ).
5. ✅ **Speed** of videos 10 %–200 % (100 % normal), in Clip settings and the timeline's right-click menu.
6. ✅ **Space** = play/pause on the video; on the timeline bars = make the preview. "Render effect preview" renamed "Preview".
7. ✅ (clips and Clay windows/bubbles; ⏳ a few moving frames instead of one) **See videos, Clay and rendered layers on the stage** (a frame, ideally a few moving), with the same box as pictures: move, resize, crop. Clip size/position for clips that are not full frame.
8. ✅ **"Balance audio levels"** (and the mixer is now a desk of faders) instead of the smart mixer: keep every choice (narrator, clip sound, muted) and only even out loudness, taming sudden peaks (explosions).
9. ✅ **Remove any plain background automatically** (green, blue, black, any single colour; pick a key colour the subject is not wearing). "Remove green screen" → "Remove background".
10. ⏳ **Four Show styles** after adding the videos: *Influencer* (fast cuts), *Explainer* (slower cuts), *Storytelling* (a narration with references, photos and clips found for what it tells — to build), *Gamer* (the talking video small, bottom right; other footage full frame).
11. ❓ **Remove Backstage** from the app to make it smaller, proving nothing in Show depends on it (Show uses some of its transcription/audio code: those parts stay).
12. ⏳ **Clearer names in the interface** (an ongoing pass; tell me the ones that confuse you).

## The person's notes of 23 October

1. ✅ **Zooms on the face, not the chest.** Every zoomed shot is looked at again (three moments, the Mac's face detector, faces turned aside too) and aimed at the face in it; when the person leans the zoom keeps the face in (easing off if it must); a shot with no face, or an old cut inside it to another framing, goes wide instead of zooming at random. Making the Show does it; for a Show already made, "Aim every zoom at the face" in the narrator track (on the copy of «La prueba»: 110 aimed, 6 went wide). Zooms aimed by hand stay. Changing the framing keeps the scenes' decisions.
2. ✅ **Camera moves instead of quick zooms.** A change of framing can be a camera move: from the previous framing to the new one in half a second, eased, in the render and in the editor alike. Making the Show turns every second change of framing on running footage into a move (the calm style, all of them); each zoom can be a cut or a move, or all at once.
3. ✅ **Narrator track editor** (right-click the Video or Audio row of the scene timeline, or "Narrator track…"): the edit played from your video with its framing live; time marks, scenes, pictures, zooms, cuts and your voice's waveform; change a zoom (wide, medium, close, closer, any value), a slow push in or pull out, cut or camera move into it, aim it by clicking the whole picture; drag a zoom to move it, its edges to extend or shorten it, Delete removes it, double-click a wide stretch to add one; Undo / Redo; a copy is kept in Earlier versions before the first change; the list of every zoom and cut. Timing never changes: effects stay put; only the scenes whose picture changed are previewed again.
4. ⏳ **Undo or extend a cut** (put back what a cut took out): it moves every effect after it, so it comes next, with a copy kept first. Today the cuts are listed and you can jump to them.
5. ✅ **Effects timeline**: time marks with seconds, faint second lines on every row, and pictures of the edit under "Video".
6. ✅ **Captions: right-click → Edit this caption…** (on the video or on the timeline; double-click too): correct words that were heard wrong, typeface, size, colour, italics; "Back to the words heard" undoes a correction. The final video draws them the same way.
7. ✅ **Library**: a video plays (silently) while the pointer is over it; an effect shows what it does on a picture of your video (close-up zooms in, smoke rises, a word pops…); a picture moves a little.

## Still open from earlier lists

- Captions kept running during an inserted clip (to check in the final video).
- "Write the script together" as a back-and-forth with the director (only "follow my idea" exists).
- The "next, next… lottery" mode (a half-made edit with pictures and voices already generated).
- The edits made to «La prueba» after 19 October (kanji clips in scene 10) need redoing; the clips are in its library.
- Freesound originals (only previews download today).
- Windows: the package passes its self-test; the interface was not walked through on Windows.
- The 360 tests that fail on GitHub (28, inherited) and the open PR stack (#33–#45) to merge into `main`.
