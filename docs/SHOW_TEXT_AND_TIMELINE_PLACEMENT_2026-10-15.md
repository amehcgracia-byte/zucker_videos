# Place Show effects by words and by timeline

The Scene 2 sofa drawing had a valid ChatGPT PNG but its saved start was 53 seconds, outside its owning scene (23.833–48.833). It therefore could not appear in that scene. Independently, proposal approval was disabled until a full scene preview loaded, even when the user had already reviewed and chosen the generated image.

## Workflow

- Einstein's result now offers **Use image & choose words**. It keeps the artwork, opens that scene and asks for its phrase instead of immediately rendering every other proposed effect.
- Every proposal has **Choose words**. Select a phrase with the mouse, or click its first word and then its last; **Use these words** stores the timed word boundaries and quote. The transcript highlights the interval. The selected effect can also be placed through the transcript's right-click menu.
- The production storyboard now has a compact **Scene timeline** immediately below the video, using the same edited clock as the words. Original video and linked audio provide reference bars. Active proposals have individual draggable clips; both edges trim duration. An out-of-scene legacy effect explicitly asks for words instead of silently disappearing. A playhead slider seeks through the actual edited source segments.
- **Show on video & timeline** selects the bar and immediately opens a clearly labelled image-layout view on the source cut. Drag the actual selected drawing or its corner to position/resize it. `image_center` is persisted and used by the production gag compositor, including positions partly outside the frame. Optional numeric timing remains in collapsed **Exact timing**.
- **Approve** keeps an individually configured effect; it no longer requires a full scene render. **Accept** still requires loading the current full scene preview. These are separate decisions; approving an image does not claim the whole scene has been checked.
- **Preview in scene** renders that effect with 1.5 seconds of surrounding context. **Generate scene preview** remains the explicit operation to compose all scene effects together.

## Corrections and validation

The individual-preview route reproduced `name 'progress' is not defined`. Its renderer now declares its optional progress callback, rejects missing requirements and incomplete artwork, and publishes the completed cached MP4 only after its final audio pass. Cache filenames include the preview recipe and output height.

The timing PATCH rejects nonfinite, empty and out-of-scene placements without modifying the saved proposal. Incomplete characters retain their zero-duration placeholder until configured. Selecting words can repair an existing misplaced proposal without replacing its image or altering another scene.

115 targeted Show/bridge/API/media/render tests passed in 40.56 s at the first integration checkpoint. The final combined rerun passed **116 tests in 37.95 s**. JavaScript syntax, Python compilation and diff checks pass. Coverage includes persistence, invalid placements leaving the project unchanged, precise short durations, picture position surviving into the production render recipe, and the individual-preview progress regression.

Browser verified the actual sofa image moving from the invalid 53-second placement onto “he's touching his balls the whole day,” (32.650–34.430), trimming its bar edge, moving the picture, and approving it without accepting the scene. A real 640×360 contextual MP4 was rendered from the original C0271 footage and visually inspected: the approved sofa artwork appears at the saved picture position. Real footage, generated images, MP4s and screenshots remain local and uncommitted.

## Limits

The immediate layout view uses the source cut, not all camera effects; it is labelled accordingly. Render previews show the actual composition. Video/audio bars are references to this storyboard cut, not new independent audio-editing controls. Character inserts still use their voice/hold settings for their performed duration. No installation, full-film export or Windows run was performed for this UI change. The isolated preview server/project at port 8798 is used; the installed app and original project are untouched.

The browser also completed the repaired individual-preview route on the real sofa proposal: a 4.766667-second 960×540 MP4, 30 fps with audio, loaded with video readyState 4. This is a contextual effect preview, not a full-film render.
