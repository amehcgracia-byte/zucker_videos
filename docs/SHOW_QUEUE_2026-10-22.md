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
10. 🔜 **Kinds of Show** (23 October): *Gamer* (fast, jokes), *Influencer* (informative, calm), *Podcast*, *Storytelling*. Captions are always in English (a talk in another language is translated on this Mac).
    - ✅ **Podcast, first version**: one microphone per person (up to 12) and any number of cameras (normal ones showing who they were pointed at, a 360 in the middle whose seats are found and named). Everything on one clock by its sound; who speaks from each microphone (the voices it picks up from others do not count); the program shows who speaks (a real camera first, the 360 close-up as another angle), the table when several talk or nobody does, and halfway through a long answer the person who asked; microphones levelled and lowered while their person is silent (no echo), saved as MP3. Then the Show cuts only long silences (no "eh" or retakes taken out from under another voice), no zooms. Not yet: a close-up cut out of a two-person camera; who sits where in the 360 found from who speaks; captions with each speaker's name; a "Cameras" row to change who is on screen.
    - ✅ **Podcast is automatic** (24 October): only the microphones (named), the cameras and subtitles yes/no. Who sits where in the 360 and whom each normal camera shows are found from whose mouth moves while each microphone speaks; no idea, effects or review; English subtitles burnt at the end.
    - 🔜 **Test references** (private, `work/test-material`, never in Git): a round table filmed with a 360 in the middle (5 people) made into 5 microphones and 3 more cameras by `tools/podcast_reference.py`; a 30-minute narrated story in Spanish for Storytelling; a person talking to camera without effects for Gamer (to choose).
    - ⏳ **Storytelling**: the narration recorded in the app over the video without sound (phrase by phrase, a phrase can be redone) or an audio brought in; the video built from it: moments of the narration, a picture or clip for each (from the library or found on the internet), photos with slow movement, clips with their sound under the voice or with the voice paused, an optional small video of the narrator.
      Long videos (up to an hour, clips of several minutes): a timeline of the whole program, like the narrator track editor — zoom out to see the hour, in to place a frame, scroll sideways, and it grows to the right as clips get longer; scenes are only marks on it; the scene editor stays for the detail.
11. 🔜 **Remove Backstage** (confirmed 23 October): gone from the mode chooser; its code goes next, keeping the transcription and audio parts the Show uses.
12. ⏳ **Clearer names in the interface** (an ongoing pass; tell me the ones that confuse you).

## The person's notes of 23 October

1. ✅ **Zooms on the face, not the chest.** Every zoomed shot is looked at again (three moments, the Mac's face detector, faces turned aside too) and aimed at the face in it; when the person leans the zoom keeps the face in (easing off if it must); a shot with no face, or an old cut inside it to another framing, goes wide instead of zooming at random. Making the Show does it; for a Show already made, "Aim every zoom at the face" in the narrator track (on the copy of «La prueba»: 110 aimed, 6 went wide). Zooms aimed by hand stay. Changing the framing keeps the scenes' decisions.
2. ✅ **Camera moves instead of quick zooms.** A change of framing can be a camera move: from the previous framing to the new one in half a second, eased, in the render and in the editor alike. Making the Show turns every second change of framing on running footage into a move (the calm style, all of them); each zoom can be a cut or a move, or all at once.
3. ✅ **Narrator track editor** (right-click the Video or Audio row of the scene timeline, or "Narrator track…"): the edit played from your video with its framing live; time marks, scenes, pictures, zooms, cuts and your voice's waveform; change a zoom (wide, medium, close, closer, any value), a slow push in or pull out, cut or camera move into it, aim it by clicking the whole picture; drag a zoom to move it, its edges to extend or shorten it, Delete removes it, double-click a wide stretch to add one; Undo / Redo; a copy is kept in Earlier versions before the first change; the list of every zoom and cut. Timing never changes: effects stay put; only the scenes whose picture changed are previewed again.
4. ⏳ **Undo or extend a cut** (put back what a cut took out): it moves every effect after it, so it comes next, with a copy kept first. Today the cuts are listed and you can jump to them.
5. ✅ **Effects timeline**: time marks with seconds, faint second lines on every row, and pictures of the edit under "Video".
6. ✅ **Captions: right-click → Edit this caption…** (on the video or on the timeline; double-click too): correct words that were heard wrong, typeface, size, colour, italics; "Back to the words heard" undoes a correction. The final video draws them the same way.
7. ✅ **Library**: a video plays (silently) while the pointer is over it; an effect shows what it does on a picture of your video (close-up zooms in, smoke rises, a word pops…); a picture moves a little.

## 23 October, evening: the render showed an old edit

- ✅ **The final video was built on 19 October's edit.** The last step (captions and overlays over the rendered Show) took its private copy of an earlier render instead of the new one, then removed the new 52-minute render with the other exports. It now always starts from the newest render. While a render runs, the app no longer shows the previous final video as if it were ready.
- The edits of «La prueba» are intact (14 scenes accepted, 65 effects approved, 18 stickers); a copy of everything editable is in `Documents/Zucker recovery/La prueba 20261023-183819 (antes de instalar 2.6.17)`. The final video file itself is no longer in `exports`: render once more.
- 2.6.18 joins Codex's installed 2.6.17 (saved video versions, captions paused during inserted clips, Clay timeline preview, 360 thumbnails) with this branch.

## Still open from earlier lists

- Narration captions spanning inserted footage: corrected on the integrated Codex branch (dde66199), with cue splitting and live suppression. Targeted tests pass; the complete final montage still needs visual review.
- "Write the script together" as a back-and-forth with the director (only "follow my idea" exists).
- The "next, next… lottery" mode (a half-made edit with pictures and voices already generated).
- The edits made to «La prueba» after 19 October (kanji clips in scene 10) need redoing; the clips are in its library.
- Freesound originals (only previews download today).
- Windows: the package passes its self-test; the interface was not walked through on Windows.
- The general fast CI remains red: latest complete run 39717899980 at 1cefcf6d has 24 failures / 1201 passing. Slow tests: 16 passing. Individual 360 contract review is in progress; do not treat all remaining failures as obsolete. PR #45 is merged; PR #44 remains open.
