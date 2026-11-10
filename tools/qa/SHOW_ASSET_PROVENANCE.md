# Show sample assets · 15 October 2026

These are public sample assets, not extracted user footage. Generated assets used Codex's built-in image-generation tool (no API key or app-side generator). The application only loads these prepared files; its request UI does not itself call that tool.

| Saved asset | Origin / generation prompt |
| --- | --- |
| `show-backgrounds/bullring.png` | Photographic 16:9 Spanish bullring in afternoon light, red/gold barriers and historic stands, two distant adult matadors posing, clear central foreground for a presenter; no fight, injury, text or logo. Generated scene, not documentation of an actual event. |
| `show-backgrounds/space.png` | Photographic space-station observation room with Earth through the window; clear central foreground, cool light, no people or text. |
| `show-backgrounds/laboratory.png` | Photographic eccentric professor's study, chalkboard calculations, books and desk; clear central foreground for compositing. |
| `show-backgrounds/landscape.png` | Photographic green alpine meadow and mountains in morning light; clear central foreground, no people or text. |
| `show-masks/stained-glass.png` | Original front-facing theatrical stained-glass head, jewel colours and lead seams, eye openings and simple mouth; head only on transparent background, no outline or text. |
| `show-elements/flying-bull-clean.png` | One absurd flying bull with tiny wings, rough black pencil and wax-crayon marks, handmade uneven drawing, full body, transparent background, no emoji outline or paper. A subsequent built-in edit removed the outer ambient halo while preserving the drawing. |
| `show-masks/bill-gates-original.jpg` | Unchanged original photograph, HHS Photo / Christopher Smith, US federal government public domain. Source: https://commons.wikimedia.org/wiki/File:Bill_Gates_2018.jpg ; original: https://upload.wikimedia.org/wikipedia/commons/a/a0/Bill_Gates_2018.jpg . Runtime polygon clips the original head pixels; no synthetic portrait. |

The manifests record asset identity and provenance. Personal photo/video uploads remain in the local browser's IndexedDB, not the repository. Personal recordings, extracted photographs, landmark JSON, segmentation mattes and screenshots must remain private. Background video testing used a four-second FFmpeg `testsrc2` clip outside Git; it is a diagnostic pattern, not a creative stock background.
