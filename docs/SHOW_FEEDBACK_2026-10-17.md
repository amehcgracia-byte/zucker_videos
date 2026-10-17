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

8. ⏳ **Right-click menu like macOS/Windows:** cascading submenus that open beside, native items (cut/copy/paste…), effects grouped by category, each with generic + specific tools.
9. ⏳ **Drawn comments** must actually appear when placed; a text menu (typeface, size, bold, italic); one plain typeface and many wild ones; "Draw this" offers every engine.
10. ⏳ **Character with eyes, mouth and voice:** one preview with all of it (today: the preview moves eyes/mouth without sound, the render has sound and mouth but no eyes); trim the recording (silence at start/end, or auto-trim) in the same window.
11. ⏳ **Physical effects** (smoke, rain, water…) in their own category with settings.
12. ⏳ **"Connectors" in the top bar:** see and switch the accounts used (Claude, ChatGPT/Codex, GitHub, Hugging Face) when one runs out of allowance.

## Block 3 · the big ones

13. ⏳ **"Your script" first:** after adding the videos, paste a document, write it, or dictate it (microphone); choose "write it together" or "automatic". The director follows the script and adds its own ideas.
14. ⏳ **"Next, next… lottery" mode:** a half-made edit from the start, with pictures, voices and movements already generated, for the person to correct rather than generate scene by scene.
