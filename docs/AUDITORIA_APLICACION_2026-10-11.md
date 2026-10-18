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

Ejecución privada **`38865498621`**, código congelado `883ab5cbebcb904cd1b8bc84a6c7c0fdf8cdb3b4`: cuatro recorridos por sistema (YouTube, Reel, Medley, Backstage). **Intento invalidado por un fallo del harness**, descrito abajo; no se cuenta como validación del producto. El SHA-256 del asset privado coincide con el archivo local (`b8161addcabca0c97305e71cbe0b76050c9c289d11f1fda9aa825882e29542fb`); tras comprobarlo se eliminaron las copias temporales locales. Debe recogerse la evidencia antes de eliminar el asset remoto temporal. Un test adicional comprueba que una fuente privada ausente aborta antes de abrir el navegador o generar fixtures; ese test y los 13 del bridge pasan localmente (14 en total). La fuente 360 real está pendiente: RAWVideos no está conectado y no se utiliza una grabación plana como sustituto. El recorrido 360 sintético no demuestra la calidad de una cámara 360 real.

El paquete Windows y su selftest de arranque en **`38865306308`** han terminado correctamente, con esa misma revisión. Se extrajo solo el log de su artefacto, sin descargar el ZIP de 618 MB: el JSON final declara `ok`, `windows_ui`, `transcription`, `scene_tagging`, `instrument_model_loaded` e `intro_rendered` verdaderos. El test abre el backend real Edge/WebView y ejecuta JavaScript; no prueba los diálogos ni permisos nativos. El anterior paquete aprobado en `38533337837` no certifica el código nuevo. Los diálogos nativos de archivos, permisos de micrófono, sustitución/actualización del ejecutable y comportamiento de menús nativos requieren validación del paquete; Chromium headless no los certifica. No se instala este paquete sobre la aplicación del usuario.

La repetición sintética tras el cambio de harness está en **`38865264815`**. La auditoría permanece **abierta** hasta recoger y revisar esas ejecuciones; este seguimiento no se pausa todavía.

## Primera ronda real: fallo del harness, no del editor

Los cuatro informes macOS de `38865498621` fallan en `page.evaluate` al construir el drop, antes de que el editor reciba los vídeos, con `TargetClosedError`. El harness convertía toda la grabación en base64 y la duplicaba entre Python, CDP y JavaScript. Con cientos de MB, Chromium se cierra. No hay errores HTTP de la aplicación en esos informes. Se canceló el trabajo Windows restante para no repetir la misma entrega defectuosa; no se han convertido esos fallos en aprobados.

**`20799323`** usa archivos respaldados por disco mediante `set_input_files` en un input temporal y entrega esos mismos `File` al handler de drop de producción. No modifica la importación del producto ni sustituye los medios reales. El test de fixtures ausentes sigue pasando. La repetición privada es **`38866605144`**, con SHA completo `20799323ed35913046ac9a2082f79da3f58d32a4`; sus resultados siguen pendientes. Los informes/capturas/logs privados se recogen selectivamente mediante HTTP Range, sin bajar gigabytes de trazas ni publicar imágenes del usuario. Helper local: `/tmp/zucker-private-qa-runner/read_evidence.py`; destino privado local `/tmp/zucker-private-evidence/<run>`.

## Diagnósticos del paquete Windows

Aunque `38865306308` acaba con `ok: true`, su log contiene múltiples `Logging error: AttributeError: 'NoneType' object has no attribute 'write'`. `_configure_logging` creaba un `StreamHandler(sys.stderr)` cuando PyInstaller GUI deja `sys.stderr=None`; el handler no recupera el stream al redirigir después el selftest. Los mensajes sí podían llegar al archivo, pero la consola de diagnósticos se llenaba de errores del propio logging.

**`760b5b07`** mantiene siempre el archivo rotatorio y añade consola solo si existe stderr. No descarta errores del render ni cambia los niveles de logging. Dos regresiones verifican que los errores quedan escritos tanto con consola como sin ella, sin modificar los handlers globales de pytest. Junto con bridge y fixtures: **16 tests aprobados localmente**. Reconstrucción/selftest remoto solicitado en **`38866751152`** para comprobar que desaparecen esos errores; resultado pendiente.

Los runs sintéticos adicionales de los commits que solo cambiaban documentación se cancelaron por duplicados. Se conservan la validación sintética de `883ab5cb`, los tests de la revisión con código nuevo y las ejecuciones privadas; no se ocultan sus fallos.

## Seguimiento del 18 de octubre — evidencia recogida y diagnósticos pendientes

PR #30 sigue sin comentarios ni reviews nuevos. Se revisaron las novedades de
los PR #37–#41: descargas, cambio de cara, previsualización de Show, Backstage y
capas/texturas. Son ramas de trabajo posteriores y separadas: los resultados de
este documento **no certifican automáticamente esos PR ni la app 2.6.7**.
El checkout principal y la aplicación del usuario no se han modificado ni cerrado
durante este seguimiento remoto.

### Resultados reales, separados de la ronda sintética

Se recogieron privadamente los ocho informes y sus logs de `38866605144`, código
`20799323`. En macOS pasan YouTube (44,2 s de salida), Reel (20 s), Medley
(10,054687 s) y Backstage (9 s). En Windows pasa Medley (10,054687 s); YouTube y
Reel fallan en `_frame_md5_pair`, cuyo proceso FFmpeg alcanza el timeout de 20 s.
Backstage no termina dentro del límite de 15 minutos del harness. **Cinco de ocho
recorridos aprobados; la ronda real no está verde.** Las duraciones anteriores
son del vídeo producido, no tiempos de render. Los pasos del workflow usan
`continue-on-error` para recoger evidencia, pero la aserción final falla
correctamente cuando falta un informe aprobado.

El timeout de Windows aparece después del render del segmento, en la lectura
conjunta de las dos muestras para verificar movimiento. No demuestra por sí solo
si el problema es bloqueo de FFmpeg o lentitud de decodificación; falta medir CPU,
hilos y pared. Se ha lanzado una comparación aislada en el repositorio privado,
ejecución **`39112425954`**, con las mismas grabaciones autorizadas: cuatro
verificaciones simultáneas, ruta actual frente a hilos acotados, orden invertido,
hashes de ambas muestras y límite de 30 s. **No se ha cambiado todavía el código
de exportación ni ampliado su timeout para ocultar el fallo.** Los únicos
artefactos nuevos de ese diagnóstico son métricas JSON, retenidas tres días en
privado; los renders se ejecutan en los runners, no en el Mac.

RAWVideos ya aparece conectado. También se encontraron ejecuciones privadas
360 posteriores a lo documentado: `38868862415` y `38869815619`. En esta última,
macOS completa el export en 32,128 s según `export.log`, y el harness decodifica
el MP4 completo antes de comprobar el reproductor. Falla la reproducción del
navegador: ambos elementos tienen `readyState=4`, pero `videoWidth=0` y
`videoHeight=0`; `canPlayType` para HEVC está vacío. No hay errores HTTP de API.
La captura muestra el reproductor vacío. Esto es compatible con falta de soporte
HEVC en ese Chromium remoto, pero **no certifica la reproducción nativa ni
justifica marcar el recorrido aprobado**. El job Windows de esa ejecución fue
cancelado: no hay resultado Windows 360 real que pueda darse por válido.

### Ronda sintética más reciente

`38865264815` terminó correctamente en `883ab5cb`. En cambio,
`38912684071`, revisión `4320cdf7`, pasa macOS pero Windows se detiene antes de
los recorridos: 87 tests pasan, 6 skips existentes y un
`threading.BrokenBarrierError` en
`test_simultaneous_preview_requests_render_same_asset_only_once` (barrera de 3 s).
Se solicitó un reintento del job fallido sin cambiar el test ni el producto para
distinguir un fallo reproducible de temporización. No se cuentan como aprobados
los cinco modos Windows que no llegaron a ejecutarse.

La auditoría permanece abierta. Siguen pendientes el diagnóstico y nueva
validación de Windows real, la reproducción nativa 360, la revisión completa de
vídeos/trazas y los diálogos nativos. No se publica material privado en este PR.

### Diagnóstico aislado completado; no justifica un arreglo especulativo

`39112425954` pasa en ambos sistemas, con 16 verificaciones por sistema y hashes
idénticos en ambas rutas. No reproduce el timeout. Con cuatro trabajos, medianas
por proceso: macOS 0,7705 s actual / 0,8715 s acotado; Windows 2,688 s actual /
2,672 s acotado. Pico de hilos observado: 21/6 en macOS y 17/8 en Windows. **Se
mantiene la ruta actual**: no hay evidencia de que limitar hilos repare el fallo
original ni una mejora global de render demostrada. La siguiente ronda real
recoge cada cinco segundos CPU acumulada, hilos y memoria de los procesos hijos
con `ZUCKER_AUDIT_PROCESS_METRICS=1`, solo en el harness y los runners privados;
no añade dependencias a la app ni cambia sus límites de exportación.

La prueba de peticiones simultáneas ahora coordina la contención en el lock real
del recurso: la primera escritura no puede terminar hasta que la segunda petición
intente ese mismo lock. Conserva la exigencia de una sola renderización y dos
URLs iguales; evita que la búsqueda del ejecutable en el SO consuma la barrera
artificial de 3 s. No se ha eliminado ninguna aserción ni cambiado el lock de
producción. Los siete tests de `test_review_hardware_decode.py` pasan localmente.

Se verificó además el log empaquetado de `38866751152`, revisión `760b5b07`:
selftest `ok: true`, `windows_ui: true`, y **cero `Logging error`**. Confirma la
corrección del handler sin stderr; no valida versiones posteriores del paquete.
