# Reel review: composition, selection and captions

The supplied 58.93-second captions export was rejected at 0.5 s because validation reduced the frame to a single grayscale pixel. Both the clean base and captioned copy returned 0 at 1×1, but reached 50 at 32×18. The revised validator samples 32×18, distinguishes decode failure from black, and compares genuinely black output to the source before rejecting it. Real introduced black is still rejected. The supplied file passes duration/stream/resolution and frame validation.

Reel previously chose source starts using index × 1.37, independent of image quality. Multi-source Reel now loads the existing cached handheld quality analysis and chooses complete measured windows ranked by stability, sharpness, exposure and face-like presence, penalizing reuse. All source participation is preserved. This is visual quality selection, not semantic solo recognition. Cold analysis adds work once per source; no source or render cache is removed. Reel plan recipe advances to 6.

Default composition branding uses the existing white logo; personal logos are unchanged. Automatic caption placement varies sizes and vertical positions as well as color/glow/entrance. Missing burned output says “Burn Captions first.”

macOS microphone consent is an explicit named NSAlert via WKUIDelegate, restricted to the application's current loopback origin and main frame, and microphone-only. Remote origins and camera requests are denied. OS microphone permission remains in force. Implementation follows Apple's requestMediaCapturePermissionForOrigin delegate API: https://developer.apple.com/documentation/webkit/wkuidelegate/webview(_:requestmediacapturepermissionfor:initiatedbyframe:type:decisionhandler:)

Validation: 33 targeted tests passed, including actual FFmpeg dark-detail/source-black/introduced-black fixtures and measured-window selection. Native text/mode smoke test still passes with the microphone delegate installed; no live microphone audio was recorded during testing.
