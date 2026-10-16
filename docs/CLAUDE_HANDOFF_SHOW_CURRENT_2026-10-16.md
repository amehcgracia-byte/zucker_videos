# Traspaso actual a Claude — 16 octubre 2026

Este documento actualiza los traspasos anteriores. **Código mínimo: `33326721`**, rama `codex/crossplatform-ui-audit`, PR https://github.com/amehcgracia-byte/zucker_videos/pull/30. Su padre `8173ae51` contiene colocación por palabras y previsualización individual; los commits `2a710b40` y `f8ab0c2f` conectaron y aislaron la generación real de imágenes con Einstein/ChatGPT. No reconstruyas desde un commit anterior ni desde una copia antigua de la maqueta.


## Cambio de identidad confirmado: el personaje de Simón

El usuario ha confirmado expresamente **el muñeco de la planta con gorro verde y abrigo amarillo**, creado por su sobrino Simón. Es su personaje recurrente a partir de ahora. No es el monigote de alambre rojo/azul ni el antiguo presentador de plastilina con barba y gafas.

En el proyecto privado, `assets/show-library/plant-character.png` conserva la ruta estable pero ahora contiene el recorte del primer plano de C0272 a los 72 s (321 × 548), con RGB originales y máscara alfa. La versión diminuta anterior queda archivada en `assets/character-references/plant-character-early-wide-crop.png`. El catálogo y el inventario privado registran su procedencia y hash. No sustituirlo por una recreación generada.

`assets/character-references/simon-character.json` fija identidad, procedencia y reglas. `simon-clay-proposal-v1.png` es una **propuesta generada para revisión**, no una foto ni un diseño ya aprobado. Mantiene gorro verde irregular, cara redonda, ojos de punto, mejillas rosadas, abrigo amarillo, cremallera oscura y bolsillos marrones. Usar siempre el original como referencia visual; cambiar vestuario no debe cambiar identidad. Los futuros personajes compartirán el acabado infantil de plastilina imperfecta, con referencias propias.

La maqueta `pilot/clay-memory/character.json` apunta a la nueva identidad; el antiguo descriptor queda en `character-presenter-v2-archived.json`. **Actualización: ya se han regenerado las dos hojas de poses y las cuatro escenas con Simón**, en `assets/clay/simon/`. El MP4 privado de 21,28 s y la versión con contexto de 29,29 s ya usan el nuevo personaje. Las versiones antiguas están retiradas del recorrido activo. Animar por acciones ligadas a la narración y pausas, no con un ciclo mecánico continuo. Las imágenes y vídeos privados permanecen fuera de GitHub.

## Actualización: pegatinas y biblioteca limpia

Lee también `docs/SHOW_STICKERS_LIBRARY_REVIEW_2026-10-16.md`. Las pegatinas de `scene.overlays` tienen IDs persistentes y pistas, edición por palabras/bordes, menú de botón derecho dentro del popup y render individual sin exigir completar otros efectos. El selector ofrece **Add sticker** dentro del editor.

`assets/show-library/catalog.json` identifica recursos elegidos y retirados. Los antiguos `Gag library/Painted` están retirados por defecto; se pueden restaurar sin romper referencias de proyectos existentes. Hay cinco recursos visibles en el proyecto de prueba: sol de ceras original, toro original, recortes exactos de planta/nevera y sofá elegido. No vuelvas a llenar el selector con las 30 imágenes retiradas. Conserva la distinción entre retirar del selector y borrar archivos compartidos.

El inventario privado incluye ahora la raíz `project_curated_library`. Copia/integra esos archivos elegidos cuando prepares la app real; no basta con bajar el PR. La guitarra antigua sigue sin identificación positiva.

## Encargo

Conserva la edición automática, captions, cortes y mejoras que ya gustaron al usuario. Integra lo nuevo sin convertirlo en otra aplicación ni reemplazar dibujos aprobados por recursos genéricos de GitHub/Hugging Face. Toda la interfaz sigue en inglés.

1. Comprueba `git status`, los commits recientes y el PR. Trabaja en un checkout aislado; incorpora estos cambios con su historial o una integración revisada, sin sobrescribir cambios locales. Comprueba `git merge-base --is-ancestor 33326721 HEAD` en el checkout que vayas a empaquetar. Si no es ancestro, detente antes de construir y revisa qué falta.
2. Lee `docs/SHOW_SELF_REVIEW_2026-10-15.md`, `docs/SHOW_TEXT_AND_TIMELINE_PLACEMENT_2026-10-15.md`, `docs/SHOW_EINSTEIN_IMAGE_REQUESTS_2026-10-15.md` y este traspaso. Los traspasos viejos describen prototipos de otra fase; sus frases «no hay proveedor conectado» no describen el actual Einstein del storyboard de producción.
3. Abre y compara el storyboard de producción, no solo el Studio experimental. La referencia local es `http://127.0.0.1:8798/?show=storyboard&scene=scene-02`, servida desde `/Users/macbookair/zucker_videos/work/medley-repair`. No cierres la app instalada ni interrumpas un trabajo activo para comparar.

## Lo nuevo que debes conservar

- Einstein crea/refina imágenes reales a través del Codex instalado y autenticado con ChatGPT; muestra versiones y permite elegir el resultado. El pintor local es una alternativa explícita, no un sustituto silencioso ni un resultado equivalente. No exige usar una API key de OpenAI.
- **Use image & choose words**, **Choose words**, selección de primera/última palabra o frase y **Use these words** colocan la imagen sobre los tiempos de la transcripción. Los tiempos exactos numéricos son secundarios.
- **Open scene editor · video & timeline** abre una ventana emergente con el mismo reproductor, no una segunda reproducción independiente. X, Escape o clic fuera la cierran y devuelven el reproductor al storyboard.
- Las imágenes elegidas aparecen únicamente durante su intervalo. La barra usa el mismo reloj editado que las palabras: mover, recortar bordes, reproducir y buscar actualizan la visibilidad. **Go to effect** localiza explícitamente su comienzo.
- Mover/redimensionar el dibujo con ratón guarda `image_center`/`size`; el compositor respeta esa colocación, incluso parcialmente fuera del frame. No restaurar el layer fijo de la versión anterior.
- `web/show_clock.js` comparte el reloj; `web/show_edit_queue.js` conserva los cambios cuando falla un guardado. **Retry saving** permite reintentarlo; aceptación y render esperan el guardado.
- **Approve** conserva un efecto configurado. **Accept** de la escena exige su previsualización actual. **Preview in scene / Render effect preview** renderiza ese efecto con contexto; **Generate scene preview** compone el conjunto.
- Previsualización individual: corregido `progress` no definido; publicación del MP4 solo después del último pase de audio y error explícito si falta la imagen.

Archivos de producción: `web/show_review.js`, `web/show_clock.js`, `web/show_edit_queue.js`, `web/show_einstein.js`, `web/styles.css`, `web/index.html`, `server/api.py`, `core/show.py`, `core/gags.py`. No sustituyas estos archivos por las páginas antiguas de `tools/qa`.

## Bibliotecas privadas: el código de GitHub no contiene todos los dibujos

Inventario local con rutas, tamaños y SHA-256:

`/Users/macbookair/zucker_videos/work/show-motion-sample/CLAUDE_ASSET_HANDOFF_2026-10-16.json`

Rutas importantes:

- Proyecto de prueba: `/Users/macbookair/zucker_videos/work/show-motion-sample/Application Review.zuckervid`.
- Imágenes Einstein de ese proyecto: `assets/show-ai/<id>/image.png`, con `request.json` y referencia desde `artifacts/show_proposals.json`.
- Imagen simplificada del sofá elegida: `assets/show-ai/788ae1b2ed3a4448b999d7be2e5bd0e1/image.png`.
- Biblioteca de la app de prueba: `/Users/macbookair/zucker_videos/work/show-motion-sample/application-data/Gag library`.
- Recursos del piloto: `pilot/backgrounds`, `pilot/masks`, `pilot/elements`, `pilot/clay-memory`, debajo de `/Users/macbookair/zucker_videos/work/show-motion-sample`.
- Fotos extraídas: `photo-library/manifest.json` en esa misma carpeta.
- Sonidos del prototipo: `audio-library/manifest.json`. Las melodías sintetizadas iniciales fueron criticadas por el usuario: no presentarlas como una biblioteca musical terminada.

`ZUCKER_DATA_ROOT` cambia la biblioteca activa. Si no está definido, `core.storage.data_root()` usa la preferencia de almacenamiento o `~/ZuckerVideos`. La app instalada puede estar mirando otra raíz. **No cambies esa preferencia global para apuntar toda la app a la carpeta de pruebas.** Integra los recursos elegidos en el proyecto/biblioteca reales con referencias persistentes, conservando originales y decisiones.

### Regla para la guitarra y cualquier dibujo ya elegido

No basta copiar una carpeta. `core.gags.make_items` usa el PNG exacto cuando un Drawing tiene `params.picture`; sin esa referencia vuelve a `painted_picture`. Un nombre de tema, un prompt parecido o la búsqueda semántica no fijan el dibujo aprobado.

- Localiza y abre visualmente el archivo que el usuario eligió; un archivo llamado “guitar” no prueba que sea ese dibujo.
- El inventario contiene una guitarra del pintor local. **No está verificado que sea la antigua guitarra aprobada de ChatGPT.** No la proclames canónica por el nombre.
- Fija el recurso exacto mediante `params.picture` y una identidad de asset persistente; registra su hash y procedencia en la biblioteca. No regeneres ni sustituyas un asset aprobado al reabrir, al analizar o al renderizar otra escena.
- Reutiliza primero recursos aprobados. Si falta el original o hay varias candidatas sin identificación fiable, muestra las candidatas o pide el archivo; no descargues otro sticker como sustituto silencioso.
- Si hace falta material nuevo, genera una propuesta y pide revisión. Estilo: trazos torpes de lápiz/cera/rotulador, imperfectos, relleno desigual; no anime, chibi, icono brillante ni sticker pulido. El contenido puede ser un adulto, no convertirlo en niño por decir «dibujo infantil».
- No publiques grabaciones, rostros, biblioteca privada ni inventarios personales en GitHub/artefactos públicos. Lo privado se transporta aparte y se conserva en el almacenamiento del proyecto.

## Antes de construir o instalar

1. Prueba con el dibujo exacto del sofá: selección de palabras, reproducción antes/durante/después, cambio de duración, movimiento, cierre/reapertura y comparación con un MP4 real. Comprueba también un dibujo antiguo elegido por el usuario; debe ser el mismo archivo antes y después.
2. Ejecuta `node tools/qa/show_clock_test.cjs` y `node tools/qa/show_edit_queue_test.cjs`.
3. Ejecuta la suite dirigida de Show indicada en el informe y la general. Última dirigida: **120 pasan**. General: **951 pasan / 26 fallan**, con 2 skips y 1 xfail ya existentes; los mismos 26 se reprodujeron en el padre `8173ae51`. No ocultarlos, añadir skips/xfails ni afirmar que toda la app está verde.
4. La vista inmediata no compone todavía todos los efectos: pintura local sin imagen elegida, steam/tracking, matemáticas, cámara y personajes/voz necesitan render. La duración de personajes hablados usa voz/hold y requiere un contrato específico. No afirmar que eso está resuelto por las barras de imágenes.
5. Si el usuario solicita instalación, verifica respaldo y paquete terminado antes de sustituir la app; confirma que el build contiene el commit y los JS nuevos. Conserva proyectos, raíces de almacenamiento, recursos elegidos y hashes. No reemplaces el proyecto real por la copia de prueba.

Entrega: commit/build exacto, qué recursos seleccionados has enlazado, hashes antes/después, recorridos probados y limitaciones. El objetivo es conservar la misma aplicación y sus materiales aprobados, no reconstruir una variante parecida con recursos antiguos o genéricos.

## Aclaración posterior: la gorra

El usuario confirma que lleva una **gorra verde ladeada con una pequeña visera delantera que sobresale**, parecida a la que lleva él en el vídeo. Debe separarse visualmente de la cabeza, con volumen y sombra bajo la visera; no es un casco, gorro ceñido ni pelo. La interpretación frontal de los PNG actuales es incompleta: esta corrección explícita tiene prioridad en futuras generaciones. El usuario acepta las animaciones actuales, que se conservan. No afirmar que ya se ha redibujado la visera.
