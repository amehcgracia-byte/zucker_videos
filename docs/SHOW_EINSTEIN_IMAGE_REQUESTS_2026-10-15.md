# Einstein image requests and consistent drawing direction

## The discrepancy

The earlier approved hand-drawn samples were generated with Codex's image tool. New storyboard drawing requests still went through the local DreamShaper painter. These were different providers. The local crayon prompt included “by a child”; the observed sofa result depicted a child. That wording was ambiguous, but the local model's stylistic bias also remains a limitation: changing words alone does not establish equivalent quality.

## Workflow now implemented

Select spoken words → right-click → **AI · create with Einstein**. The existing drawing idea box and Add drawing form also open this dialog. Existing drawing cards offer **AI · change image**. The dialog keeps the selected phrase and timing, accepts art direction, drawing style and position, and optionally uses one original video frame as an identity reference. The reference checkbox describes that transfer. It requests an image first, not a generated video or an invented chat response.

**Generate with ChatGPT** invokes the installed Codex image tool using a ChatGPT sign-in. A sign-in probe rejects API-key authentication for this workflow. No API-key billing or local-model fallback is silently substituted. Missing Codex, account/usage failures, timeouts, invalid output paths, opaque images and empty images become explicit errors. One image job per project runs at a time, with a seven-minute process timeout.

The generated PNG is reviewed in Einstein before **Use image & preview scene** adds or replaces a proposed drawing. The existing scene is untouched until then. Applying again is idempotent; changing an image preserves existing animation, size and timing. The normal scene-preview/review step remains separate from image approval.

Previous image versions remain selectable. A follow-up can keep the current image as a reference, with the earlier direction retained. Closing/reopening the dialog restores the matching request. The local painter remains an explicit alternative, and cannot be used for the face-reference option. Drawing cards identify the actual image source.

## Style and cache changes

Removed the positive “by a child” phrasing from the local crayon recipe; strengthened amateur, uneven, unfinished strokes and negative anime/manga/chibi/polished-illustration instructions. Local art cache keys now include the recipe version and style prompt. Scene preview signatures include the local art recipe and Claude's Studio fingerprint. Old pictures/previews are retained; they are not erased as an optimization. The earlier cache-blob test was updated to require preserving the older recipe's file while generating a fresh asset under the new recipe.

The ChatGPT brief separates crude drawing technique from the character's age and identity. Its crayon variant asks for a few uneven outlines, broad patchy fills, limited flat colours and no realistic shading. This is a direction to review, not a guarantee of artistic suitability.

## Integration and validation

Merged Claude's newer work through 3b4b598c (merge feef57f8), retaining the scene-preview invalidation/progress safeguards and adding Studio fingerprints to the combined preview recipe. This also brings the production bridge for Studio overlays and chosen library pictures into this branch.

106 targeted tests passed in 36.18 seconds across Show AI images, saved render, scene/review, Studio API/store/bridge, gags/gag render, Show render and media suites. AI tests mock provider execution; they do not consume account usage. They cover explicit image application, project-local storage, version history, idempotence, preservation of timing/animation/size, concurrency, invalid paths, missing provider, refinement references and API-key rejection.

A separate real browser test generated an adult sofa image from Einstein through the signed-in Codex image tool, using one host frame. The result had genuine PNG alpha and recognizable cap/glasses/facial hair. It was more detailed than the desired aesthetic. A second, simpler variation was generated with Codex's built-in image tool in this conversation and copied into the same project-local request library, with its distinct origin recorded. Both versions are available for review; neither was applied or scene-approved on the user's behalf. Private images, reference frames, provider logs and project requests are not committed or uploaded to GitHub.

Official image-generation background and reference-image guidance: https://developers.openai.com/api/docs/guides/tools-image-generation . The implementation uses Codex's installed image tool, not a new Images API integration.

## Limits

This is image generation plus existing compositing/animation tools, not generative video. The provider requires Codex installed and signed in with ChatGPT; this was verified on this Mac, not on a packaged Windows build. Availability and usage limits belong to that account. The simpler design remains for the user to judge. The local model's new style instructions have test coverage for routing/recipes but no claim of visual parity with ChatGPT.
