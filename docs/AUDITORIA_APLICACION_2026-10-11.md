# Application audit — 11–12 October 2026

## Completed synthetic round

Revision a967731d, including PR #31 through f46d1714, passed all ten browser and
real-pipeline journeys: YouTube, Reel, Medley, 360 and Backstage on hosted macOS
and Windows. Run 38163170602 succeeded. Each OS also passed 26 targeted tests.
Windows package build and packaged startup checks passed in run 38163609156.
These are scoped results, not a claim that the historical full suite is green.

Corrections established in this round:
- Mode cards accept clicks on their child labels/icons.
- Continue accepts unchanged camera/filler settings while preparation runs.
- Decoder stderr is drained concurrently to prevent pipe deadlocks.
- Back navigation accepts preparation finishing before the cancellation request.
- Caption burning avoids Windows drive-letter parsing in subtitle filter paths.
- Arranging captions no longer starts an unsolicited composition render.

The macOS audit environment now installs ffmpeg-full for subtitle support. The
harness collects evidence per mode and requires every journey to pass; failed
steps cannot be hidden by continuing to the remaining modes.

## Follow-up on 12 October

The installed 2.6.3 application now reports revision 841d7a46. PR #31 includes
cherry-picked audit corrections plus new caption effects, direct caption editing,
Reel replacement safeguards, FFmpeg escaping, persistent probe caching, and safer
opening/listing of saved projects. The older local a967731d DMG was built but was
not installed over this newer application.

The audit branch merges the complete PR #31 head 841d7a46, retaining its new
caption renderer (including removal of the old animation expansion), its new
handoff regression test, and the newer mode-card handling. Remote validation of
this combined revision is being started; previous green results do not certify
these newly integrated features. Targeted checks now include filter escaping,
probe persistence and caption tests in addition to the existing regressions.

## Scope and remaining work

Journeys exercise new project, mode choice, import, mode-specific options,
back/forward navigation, shot or paper review where applicable, and export.
Outputs are decoded completely and checked for duration and audio/video streams.
Reel also exercises caption distribution, burning and final composition, and
asserts distribution does not start a render. Artifacts retain screenshots,
traces, reports and synthetic videos for seven days per mode and OS.

Only synthetic media has been uploaded so far. The authorized short real-recording
round remains pending and requires private transport, without public media
artifacts. Native microphone permission dialogs are not established by browser
journeys; packaged Windows startup is a separate, narrower check. No user render,
project or installed application was interrupted by this follow-up.

## Follow-up run 38215928111: test failures diagnosed

At fa24e231, macOS targeted checks and four mode journeys passed. Reel's burn
endpoint returned HTTP 200, but the harness waited for the obsolete word
“Created”; the UI now says “Showing the burned result”. The harness now verifies
the actual burn response and that the player loads the returned media URL.

Windows stopped in targeted checks (68 passed, 1 failed, 6 existing skips), before
any mode journey. The failing test tried to create a directory named `drive C:`,
which Windows forbids. It now exercises the actual drive colon in the absolute
temporary path, while retaining literal directory-colon coverage on POSIX.
Neither failure establishes a product regression. A fresh remote round is required.

## Follow-up — 14 October: evidence at 8d721075

Run [38429121991](https://github.com/amehcgracia-byte/zucker_videos/actions/runs/38429121991)
passed all ten synthetic journeys, with no reported browser exceptions or API HTTP
failures. Downloaded and inspected the ten reports, rendered-page screenshots and
one middle frame of each master. This is the audit branch, not the separate Show
PR #32 (fc106448); the Show prototype additions do not certify production Show.
PR #31 remains at 841d7a46; no new review comments were present on PR #30.

| Mode | macOS duration (s) | Windows duration (s) |
| --- | ---: | ---: |
| YouTube | 32.200 | 32.200 |
| Reel | 11.900 | 11.900 |
| Medley | 10.055 | 10.055 |
| 360 | 32.200 | 32.200 |
| Backstage | 10.233 | 20.900 |

These are **output durations**, not render wall times. Different speech synthesis
lengths affect Backstage, so the two outputs are not a content-equivalence test.
The black Medley middle frame is its intended inter-song gap: macOS blackdetect
reported 4.721–5.355 s (0.633 s), not an entirely black output. Other middle frames
show the synthetic image. Some rendered-page screenshots show black players;
a fixed two-second delay cannot distinguish a black intro from a player that has
not decoded. The next harness revision requires a visible player with decoded
video dimensions and readyState >= 2. No claim of verified player playback is
made for those older screenshots.

Full fast CI is **not green**: both runs 38429122493 and 38429131157 report
28 failed / 754 passed / 9 skipped / 1 xfailed, plus 16 deselected slow tests.
Existing skips/xfail were not introduced or altered here. The slow push run had
one bitrate-tolerance failure; the slow PR run passed. The Flask self-test failure
reports unavailable/non-discriminating CLIP scene tagging, not Flask failing to
initialize. The Reel scale-overlay regression exits with SIGSEGV on Ubuntu's
FFmpeg even with single-thread filter scheduling; macOS does not reproduce it.

Candidate correction: reevaluate pad placement per frame after dynamic overlay
scale, in both Reel graph paths. The source image dimensions change during the
animation but pad previously evaluated its placement only at initialization.
Local regression/escaping/path checks: 15 passed. **Ubuntu confirmation is still
required before calling the crash fixed**; the same regression is now included
in remote macOS/Windows targeted checks. No bitrate/resolution/animation change
is intended. Historical 360 assertions have not been changed in this follow-up.

The old harness fully decoded the first Reel master but only copied the caption
composition. The revised harness also fully decodes that final composition and
checks both streams and duration within 0.25 s of the first master. Earlier green
runs must not be presented as proving that final composed media decoded fully.

The private real-recording round, interactive native dialogs and a current
Windows package remain pending. Public synthetic artifacts must not be reused
for private clips: the existing workflow uploads complete screenshots, traces,
browser videos and masters. A dedicated private transport/evidence path is
required before introducing real footage. No app was closed or installed; no
user project or primary checkout was modified.

### Cancellation finding from the downloaded traces

The successful macOS Reel/YouTube and Windows Reel/YouTube server logs contain
`Reel framing analysis failed` tracebacks ending in `WizardCancelled` when the
journey goes back from settings. This is a product cancellation propagation bug,
not a failed detector or a harness timeout: the optional framing fallback catches
an exception emitted by the caller's progress callback, logs it as a detector
failure, and can proceed to the next source. The wrapper now propagates callback
exceptions unchanged before proceeding, while preserving detector fallback.
A regression raises the actual WizardCancelled from progress and requires that
the second source is never visited; all nine framing tests pass locally. This
suite is now included in remote targeted checks. Full journey revalidation is
pending for this follow-up commit.

A Windows package build was dispatched on the audit branch in run
[38533337837](https://github.com/amehcgracia-byte/zucker_videos/actions/runs/38533337837).
This builds/test-starts a package; it does not publish a release or install it on
the user's computer. Its result must be recorded before claiming current package
validation, and its revision precedes the cancellation correction above.

### 15 October follow-up: latest remote results collected

At `09afc2eb`, [journey run 38654782318](https://github.com/amehcgracia-byte/zucker_videos/actions/runs/38654782318) passed all five macOS journeys and four Windows journeys. Windows YouTube failed on the second transition to settings after back navigation. The screenshot displays “Failed to fetch”; the downloaded network trace records `net::ERR_NO_BUFFER_SPACE` on the status GET during cancellation. The server continued answering subsequent status requests with HTTP 200. This is evidence of a browser/network resource failure, **not yet proof of a product regression or an acceptable reason to mark the journey passed**. A failed-job rerun was requested to distinguish reproducibility from a transient executor issue. No timeout was increased and no assertion was removed. Earlier [run 38649274271](https://github.com/amehcgracia-byte/zucker_videos/actions/runs/38649274271) passed both OS jobs at `19dd5b1c`.

The targeted remote checks reported 79 passed / 6 skipped on each OS. The separate full fast suite [38654788370](https://github.com/amehcgracia-byte/zucker_videos/actions/runs/38654788370) remains red: 27 failed, 756 passed, 9 skipped, 16 deselected and one existing xfail. This follow-up does not claim that the historical full-suite failures have been repaired.

[Windows package run 38533337837](https://github.com/amehcgracia-byte/zucker_videos/actions/runs/38533337837) completed successfully at `7c9c561c`; that confirms that revision's package build/startup checks, not the latest head or full native-dialog interaction. Real-recording private transport/testing, remaining trace/video review and current-head packaged/native validation are still pending. No private clips were uploaded in this follow-up. The scheduled audit is not complete.
# Seguimiento del 16 de octubre — revisión actual `883ab5cb`

## Resultados recogidos, sin reinstalar ni cerrar la aplicación

- PR #30 sigue abierto, sin comentarios ni reviews nuevos. PR #31 sigue en `841d7a46` y PR #32 en `fc106448`; no hay una nueva revisión de Claude en esos PR que integrar.
- El reintento de `38654782318` terminó correctamente (attempt 2). El anterior `ERR_NO_BUFFER_SPACE` no reapareció en ese reintento; no se ha modificado el producto para ocultarlo.
- En **`38822618202`, revisión `b2dfb50a`**, pasan los diez recorridos sintéticos: YouTube, Reel, Medley, 360 y Backstage en macOS y Windows. En ambos sistemas pasan los 79 checks dirigidos (6 skips ya existentes). Los logs confirman reproductor cargado en los diez y verificación del MP4 final con captions en ambos Reel.
- Se descargaron y revisaron además las capturas, un fotograma del master y las trazas completas de 360/macOS y Backstage/Windows de esa ejecución. Ninguna traza contiene errores de acciones ni respuestas HTTP >=400. La imagen del reproductor ya muestra contenido, no una pantalla negra. Esto no equivale a revisar visualmente cada segundo de los diez masters.
- La suite completa sigue roja: **`38822618204`: 29 fallos, 944 aprobados, 10 skips, 1 xfail, 16 deseleccionados**. Los tres fallos adicionales respecto a los 26 reproducidos localmente en el padre son el selftest con CLIP, la aserción de píxeles de subtítulos en Ubuntu y la selección de fuentes de Show. No se contabilizan los diez recorridos aprobados como una suite completa verde.

## Fallo reproducido y corrección

La ruta de textos/fórmulas de Show (`core/show_bridge_text.py`) trataba todos los sistemas distintos de Windows como macOS. En Linux buscaba exclusivamente fuentes de Apple y fallaba con `No font for its words on this computer`. **`9845f3d2`** separa Linux y busca DejaVu/Liberation, manteniendo las rutas de Windows/macOS. Los 13 tests locales de `test_show_bridge.py` pasan. Confirmado en el job rápido de Ubuntu `116856507140` (ejecución `38865264847`): **28 fallos / 945 aprobados**, frente a 29 / 944 antes. El test de textos/fórmulas deja de fallar; la suite completa continúa roja. Los skips/xfail existentes no se han aumentado.

## Ronda privada con grabaciones reales

**`883ab5cb`** añade `--media` al harness. Usa exclusivamente los archivos indicados y falla si falta alguno, sin sustituirlos por patrones sintéticos. El informe etiqueta el origen real/sintético. YouTube y Reel usan la única cámara hablada disponible, sin fingir que una toma de detalle no sincronizada es una segunda cámara de concierto.

Se han extraído localmente, por copia de streams, solo 24 s de C0271 y 12 s de C0274, más su audio. No se renderiza localmente. Los fragmentos no están en Git ni en artefactos del repositorio público: se transportan como un asset de release de un repositorio **privado**, con verificación previa de visibilidad. El repositorio privado contiene únicamente el workflow; los medios no se añaden a su historial Git. Los resultados de los runners se retienen allí durante tres días para la revisión. No se publicarán capturas ni trazas privadas en PR #30.

Ejecución privada **`38865498621`**, código congelado `883ab5cbebcb904cd1b8bc84a6c7c0fdf8cdb3b4`: cuatro recorridos por sistema (YouTube, Reel, Medley, Backstage), **en curso, sin resultado aún**. El SHA-256 del asset privado coincide con el archivo local (`b8161addcabca0c97305e71cbe0b76050c9c289d11f1fda9aa825882e29542fb`); tras comprobarlo se eliminaron las copias temporales locales. Debe recogerse la evidencia antes de eliminar el asset remoto temporal. Un test adicional comprueba que una fuente privada ausente aborta antes de abrir el navegador o generar fixtures; ese test y los 13 del bridge pasan localmente (14 en total). La fuente 360 real está pendiente: RAWVideos no está conectado y no se utiliza una grabación plana como sustituto. El recorrido 360 sintético no demuestra la calidad de una cámara 360 real.

Se ha solicitado también un nuevo paquete Windows y su selftest de arranque en **`38865306308`**, con esa misma revisión. El anterior paquete aprobado en `38533337837` no certifica el código nuevo. Los diálogos nativos de archivos, permisos de micrófono, sustitución/actualización del ejecutable y comportamiento de menús nativos requieren validación del paquete; Chromium headless no los certifica. No se instala este paquete sobre la aplicación del usuario.

La repetición sintética tras el cambio de harness está en **`38865264815`**. La auditoría permanece **abierta** hasta recoger y revisar esas ejecuciones; este seguimiento no se pausa todavía.
