# Show — the person's review of 2.6.5 (17 Oct 2026)

Branch `claude/show-feedback-1017`, on top of `codex/show-preview-review` (PR #39).
Status: ✅ done and verified · 🔜 next · ⏳ later.

## Block 1 · bugs and annoyances

1. ✅ **Subtitles were always on.** The intent is occasional drawn comments, not subtitles of everything said. Speech captions are now off until turned on ("Speech captions · Off · turn on" in the scene editor, or the selection menu); the final cue track is empty while off. `core/show_caption_layout.enabled`.
2. ✅ **Times demanded three decimals.** Steps were counted from a 3-decimal scene start; time fields take any decimals (`step="any"`).
3. ✅ **Adding took the person out of the scene editor.** "Add ▸" in the editor and "Add an effect…" in the video's right-click menu open the choices inside the editor at the playhead; a close-up or speaking character asks for its video right there.
4. ✅ **Only ChatGPT for drawn words.** Claude letters words too (stroke by stroke, `LETTERING_SYSTEM`). A realistic photo edit still needs ChatGPT; the local painter cannot write exact words.
5. 🔜 **Captions kept running during an inserted clip** (seen in a preview). The final cue track already shifts by inserted spans; check the live layer while an effects preview plays an insert.
6. ✅ **With or without audio.** Talking character: its own voice (host pauses) or the host's voice (it mouths along). Clip: its own sound (host pauses) or picture only over the host's voice.
7. ✅ **Rainbow steam.** A + on the video to aim it, "Follow a mug" box, density/thickness/height/size/colours; steam without a mug ends with its shot.

## Block 2 · tools to rebuild properly

8. ✅ **Right-click menu like macOS/Windows** (`web/show_menu.js`; Add ▸ by kind, Effects in this scene ▸, picture tools ▸, Settings… over the editor): cascading submenus that open beside, native items (cut/copy/paste…), effects grouped by category, each with generic + specific tools.
9. ✅ **Words' text menu:** one plain + 19 wild typefaces, bold, italic, size, colour (settings and right-click, shown in themselves); "Hand-letter it instead" with Claude or ChatGPT. 🔜 Check a placed drawn comment shows in the live view and the effects preview.
10. ✅ **Character with eyes, mouth and voice** (eyes and mouth now move together in the render; "▶ Preview with voice" uses the render's own code; "Trim the silence" and start/end in the studio): one preview with all of it (today: the preview moves eyes/mouth without sound, the render has sound and mouth but no eyes); trim the recording (silence at start/end, or auto-trim) in the same window.
11. ⏳ **Physical effects** (smoke, rain, water…) in their own category with settings.
12. ✅ **"Connectors" in the top bar** (`core/connectors.py`): see and switch the accounts used (Claude, ChatGPT/Codex, GitHub, Hugging Face) when one runs out of allowance.

## Block 3 · the big ones

13. ✅ **"Your script" first** (`core/show_brief.py`, `web/show_idea.js`; the director follows it first, `from_idea` gags placed before its own; "write it together" as a back-and-forth is not built yet): after adding the videos, paste a document, write it, or dictate it (microphone); choose "write it together" or "automatic". The director follows the script and adds its own ideas.
14. ⏳ **"Next, next… lottery" mode:** a half-made edit from the start, with pictures, voices and movements already generated, for the person to correct rather than generate scene by scene.

## Block 4 · second round (17 Oct, afternoon)

15. ✅ **Change the video's background** (`core/show_backgrounds.py`): a "New background" effect keeps the person talking in front of a picture of the place (kitchen, outside, atelier…). macOS Vision cuts the person out of every frame on this Mac (nothing uploaded); masks are made once per span from the edited cut and cached, so previews and the final render share them. Add ▸ "Change the background behind me…" picks the place from the library; the director proposes one for every place named (its picture is chosen by the person, never invented).
16. ✅ **Two overlapping effects, one missing from the video.** The preview showed every effect not discarded, the render only those approved one by one, so a changed scene lost the rest; and a library picture the director named was never found outside the old pipeline. The video now keeps what a kept scene's preview shows (`show_scenes.as_seen`), and named library pictures are found exactly (`gag_library.named_picture`).
17. ✅ **Timeline editing:** split at the playhead (⌘B), cut/copy/paste at the playhead on the same row (⌘X/⌘C/⌘V), duplicate (⌘D), delete (⌫), undo/redo (⌘Z/⇧⌘Z), from the keyboard, a toolbar or the bar's right-click menu. Pieces are effects with the same settings sharing a row (`params.group`/`overlay.group`); a split close-up or jingle carries on in its source.
18. ✅ **Voices as small files:** recordings are kept as AAC .m4a (~1 MB a minute instead of ~6 MB as WAV). Voices already recorded stay as they are.
19. ✅ **Eyes and mouth together:** the glance moved eyes 5 % of their width (1–2 px). The iris now slides 22 % inside each marked eye (outline fixed, original pixels only) and a talking character glances every three seconds, in "▶ Preview with voice" and the render.

## Installed

17 Oct: `fe4803f7` (2.6.5 label) with items 1–4, 6–10 and 12; backup of the previous install in `build/installed-before-fe4803f7.app`.
