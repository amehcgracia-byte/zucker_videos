# Show reference: I Invented a New Type of Vandalism... But It's Legal

Reference: https://www.youtube.com/watch?v=pYOJ6NzoM9o
Reviewed on 15 October 2026. Visual inventory from a local analysis copy (450p),
303 samples at approximately one-second intervals across the video. Shorter-than-
one-second effects can be missed; timing below is approximate. No audio design
inventory or claim about the author's software/generation methods is made here.
The reference video, frames and contact sheets are not committed or redistributed.

| Approximate interval | Visible device | Editor capability it suggests |
| --- | --- | --- |
| 00:03–00:06 | Cut-out presenter layered over animated/archival imagery; strongly different foreground/background scales | Video masks, independent layers, transform, background video |
| 00:12–00:16 | Moving face imagery inside the red lunchbox surface | Surface insert, four-corner perspective mapping and tracking; exact production method unknown |
| 00:23–00:25; 01:08–01:12; 02:02–02:04 | Exaggerated head/body proportions and local deformation | Anchored region enlargement or a local warp; visual appearance alone does not establish the original algorithm |
| 00:34–00:40 | Supporting images/video placed in a frame on the left of the shot | Contextual inserts, positioning, perspective and event timing |
| 00:44–00:45 | Archival clip with strong wide-angle/fisheye appearance | Insert footage; the lens appearance may already be present in the source |
| 00:46–00:49; 03:04–03:09; 04:46 onward | Portrait footage within paper-textured side margins | Aspect-preserving layouts with textured backgrounds |
| 01:51–01:58; 03:10–03:15 | Other footage illustrates the objects/story | B-roll placement while preserving chosen speech or source audio |
| 03:28–03:43 | Puppet-like animated sequence; insert gives way to full-frame coverage | Reusable animated-character/video assets and layout transitions; the way this sequence was produced is not established |
| 04:01–04:05 | Glass/fragment transition reveals a green-studio-looking background behind the presenter | Transition layer plus subject/background separation; not proof the main scene was filmed on green screen |
| 04:16–04:18 | Small figures/heads layered around the hat | Partial cutouts, small duplicates and object anchoring |
| 04:22–04:25 | Two cut-out characters, white contours and changing scene/collage backgrounds | Duplicate, mirror, outline, background/layout change |
| Throughout | Hard changes between wider framing, close face views and object details | Separate hard-cut and zoom tools, with editable timing |
| 05:00–end | Drawn CHOE SHOW logo with visible material texture and changing appearance | Own drawn logo/short outro asset, opacity/fade |

## Keep the editor compact

These should be parameters of a few shared tools, rather than a button for every
reference joke:

1. **Select a region**: whole person, head, object, rectangle or closed outline.
2. **Transform the selection**: size, position, rotation, duplicate, mirror;
   later local warp and white outline.
3. **Place content**: image, background, video, paper layout; later four-corner
   perspective tracking.
4. **Time it**: hard cut, quick reveal, motion, entrance/exit and source audio.

## Implemented pilot addition

`Big head` is a bounded original-pixel region enlargement, anchored at the neck,
on the reviewed source 21.32–24.32 s. It uses the existing propagated alpha matte,
restricts it to a head region and feathers the lower join. The body and room keep
their scale. Size, rotation and position are editable under the picture.

`+ Add > Move / enlarge a part` creates a manually selected rectangular/polygonal
source region. This is a **copy over the original**, not object removal or
inpainting. A displaced copy can leave the original visible. A manual region is
fixed in source coordinates; arbitrary-subject tracking is not implemented here.
Selecting a new region should be done on the original view, not the enlarged
copy. These additions are local preview tools, not production export support.

Part transforms share an affine matrix with tests requiring the source anchor to
stay fixed when scaling/rotating, and to follow a separate target position when
moving. Perspective inserts, glass transitions, white outlines, general local
warps and a puppet-animation pipeline have **not** been implemented by this
reference review. The previous prototype's MP4 export limitation remains.
