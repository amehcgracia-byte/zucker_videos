# Text, mode boundaries and Reel workflow

- Native WKWebView/WebView2 context menus and text selection enabled without debug mode.
- Hidden mode panels now stay hidden despite flex/grid styles. Native menu actions follow the selected mode; Medley does not offer 360 setup.
- Reel imports bypass the YouTube session filter; legacy Filler marks no longer exclude Reel sources. Reel already plans clips independently of synchronization.
- Start Again sends POST to reset and reopens the saved project before starting with a fresh variation. Retry retains the variation. Caches remain intact.
- Reel captions can record/import voice, transcribe locally, and distribute editable phrases chronologically with gaps and varied color/glow/entrance. Audio is bounded to three minutes/10 MB and removed after transcription. No ambient recording occurs until the user presses Record and grants microphone permission.

Validation: 45 tests passed across desktop menus, storage, Medley and the initial three Reel dictation tests. API suite: 42 passed and the same five failures as detached d488d6b0 (four existing spherical/project expectations plus sphericalSetup removal expectation). No failures added. Actual hidden macOS WKWebView verified context-menu preservation and panel/menu visibility for all five modes. Microphone device permission and live voice transcription require user testing; automated tests substitute transcription, not microphone hardware. Windows native menu path has not been exercised on Windows in this pass.
