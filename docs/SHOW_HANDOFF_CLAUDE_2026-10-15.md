# Prompt de traspaso a Claude · Show Studio · 15 octubre 2026

Continúa Zucker Editor / Show Studio desde el PR https://github.com/amehcgracia-byte/zucker_videos/pull/30, rama `codex/crossplatform-ui-audit`, worktree `/Users/macbookair/zucker_videos/work/medley-repair`. Lee primero `docs/SHOW_HANDOFF_2026-10-12.md` y este documento. No sobrescribas trabajo del checkout principal ni cierres la aplicación del usuario. Toda la interfaz debe seguir en inglés.

## Qué se ha añadido y probado

La muestra viva está en http://127.0.0.1:8792/pilot/editor.html, servida desde `/Users/macbookair/zucker_videos/work/show-motion-sample`. Los originales versionados están en `tools/qa/show_*.js` y `tools/qa/show_pilot_editor.html`. El prototipo conserva decisiones del usuario en localStorage; los archivos personales nuevos de fondos y máscaras se guardan en IndexedDB. No restaures una receta antigua: el usuario ya ha recortado sus efectos y actualmente conserva dos fragmentos de Automatic Finger y tres cortes de cámara.

- Zoom/cámara: seleccionar un punto centra el plano; arrastrar la imagen con cursor de mano reencuadra. Corregido el valor de zoom mostrado cuando superaba el máximo inicial del control.
- Backgrounds: cuatro fondos generados (plaza española con dos toreros distantes, estación espacial, estudio y paisaje), más importación de fotos **y vídeos** personales. El vídeo de fondo se sincroniza con la escena, permite elegir segundo inicial y bucle, queda silenciado y conserva la voz principal. Durante una búsqueda conserva el último fotograma disponible. Se probó importar un vídeo sintético, colocarlo detrás del recorte, cambiar el tiempo y reproducirlo. Los límites de sincronía de una vista previa con dos decodificadores no certifican exportación frame-exact.
- Masks: foto original de Bill Gates y máscara original de vidriera; importación de fotos, contorno rectangular/poligonal y región de boca. La cabeza fotográfica usa píxeles originales. Seguimiento facial precomputado del piloto, tamaño, desplazamiento, giro y mandíbula rectangular de la misma foto; voz original, sin clonación. Puede desactivarse el seguimiento para colocar/animar la máscara libremente.
- Animation / path: claves de posición, tamaño, rotación y opacidad; cruzar, saltar, asomarse por la derecha y crecer. Se permiten posiciones fuera del encuadre y tamaños mayores que la imagen. Copiar/pegar y mover desplazan las claves; recortar conserva la trayectoria correspondiente.
- Create / animate y cuadros Describe what you want en los diálogos de materiales: aplican un recurso preparado (toro de ceras, vidriera, Bill Gates o fondo) o una acción existente. Una petición nueva queda guardada como material pendiente y permite adjuntar referencia. **No hay proveedor de generación conectado a la app**; no simular resultados ni prometer generación por la suscripción de Codex.
- Scene plan: reglas locales explicables sobre la transcripción cacheada o contexto escrito por el usuario. Muestra cita, propuesta, material que falta y petición editable; descarga del guion JSON. No es comprensión audiovisual general ni un guionista LLM.

Ficheros principales: `show_background_library.js`, `show_background_model.js`, `show_mask_library.js`, `show_mask_geometry.js`, `build_show_face_sample.py`, `show_motion_model.js`, `show_motion_editor.js`, `show_request_model.js`, `show_request_tools.js`, `show_scene_plans.js`, además de los cambios en Studio, Paint, Timeline y HTML. `tools/qa/SHOW_ASSET_PROVENANCE.md` contiene fuentes, modo de generación y prompts de todos los materiales públicos.

## Límites que no debes presentar como resueltos

- El recorte automático de persona sigue siendo una muestra revisada de **3 segundos, fuente 21.32–24.32 s**. Fuera de ella hay selección manual, no segmentación automática general de todo el vídeo.
- Face Mesh: 625 detecciones de 1.100 fotogramas, 8,488 s de análisis local del piloto. Los huecos no se rellenan con caras inventadas. Es seguimiento frontal/rotación en plano, no una máscara 3D ni sincronía fonética de labios.
- Movimiento de todo el recorte; no articulación de piernas, física de colisiones ni eliminación/inpainting del objeto original. Copiar el gorro no borra el gorro original.
- Estos efectos funcionan en la vista previa **pero su exportación MP4 no está conectada**. No afirmar que la aplicación instalada ya los exporta. No se ha certificado este nuevo código en el paquete Windows.
- Los fondos generados son imágenes estáticas. Hay fondo de vídeo importable; animación generativa de esas imágenes y búsqueda/descarga de stock todavía pendientes.
- El ejemplo contextual Spain no se ha detectado en la transcripción del fragmento actual: los ejemplos los ha solicitado el usuario. La selección editorial sigue requiriendo revisión.
- No publicar vídeos, fotos extraídas, máscaras personales, landmarks ni capturas del usuario en Git o artefactos públicos. Una mezcla no elimina los derechos del material previo; usar material propio o con permiso aplicable y registrar procedencia.

## Siguiente trabajo recomendado

1. Integrar assets y decisiones en el almacenamiento real del proyecto, conservando claves, jerarquía, tiempos de fuente y audio/vídeo vinculados. Separar selección de personaje, biblioteca y generación del proveedor.
2. Conectar trabajos reales de análisis/segmentación/generación con progreso, cancelación y caché por fuente/receta. Si falta material, pedirlo de forma concreta. No confundir matching de biblioteca con creación de una imagen.
3. Llevar la composición al renderer real con paridad de tiempos, capas, máscaras y audio frente a la vista previa; validar salida completa antes de publicar atómicamente. Probar macOS y Windows.
4. Construir el gag de gorro solicitado: seleccionar/seguir el gorro → separar y reconstruir el fondo donde estaba → trayectoria de subida/giro/falsa caída → impacto que cambia al fondo de vídeo de cristales → sonido de rotura. La infraestructura de partes y claves existe, **el gag no está entregado**. Pedir un recurso si no se puede obtener/generar con procedencia clara.
5. Mantener el editor compacto: controles sobre imagen/contextuales, fragmentos del mismo efecto en una pista, copy/paste en huecos, Undo y audio unido al vídeo por defecto. Verificar que cada control afecta de verdad a la ruta activa.

Antes de modificar, inspecciona cambios nuevos del PR y coordina con trabajo local existente. Pruebas Node: `test_show_background_model.cjs`, `test_show_motion_model.cjs`, `test_show_scene_plans.cjs`, `test_show_request_model.cjs`, `test_show_paint_tools.cjs`, `test_show_timeline_model.cjs`; sintaxis de los módulos y del script inline. Los resultados locales de estos tests no significan que la suite histórica completa ni la auditoría remota estén verdes.

## Petición posterior: biblioteca por proyecto y control de espacio

Se ha añadido una base **real del servidor**, separada del prototipo: `core/project_library.py`, `server/project_library_api.py` y el botón **Project library** de `web/index.html` / `web/project_library.js`.

- Guarda imágenes, vídeo y audio en `<proyecto>.zuckervid/library/assets/` con manifest relativo y SHA-256 del contenido completo. No modifica ni mueve la fuente. Una importación idéntica reutiliza el archivo; una copia corrupta se repara al reimportar el original.
- El catálogo global `<data_root>/Library/projects.json` solo registra ubicaciones de proyectos. Se puede copiar un recurso de otro proyecto al actual, deduplicado y con identidad validada, para conservar portabilidad. No hay una segunda biblioteca global de medios. Los proyectos desconectados no causan traslado al disco interno.
- El panel muestra ubicación, tamaño, recursos y reutilización entre proyectos. La limpieza muestra primero el inventario exacto de `library/previews/`, exige el token del inventario vigente y mueve solo esas previsualizaciones a Papelera. Inputs, exports y originales están excluidos. Se rechaza durante wizard/render/composición activos. **Mover a Papelera no libera físicamente espacio hasta vaciarla**; no se vacía automáticamente.
- Importaciones transmitidas por bloques al disco del proyecto, publicación/manifest atómicos y bloqueo entre procesos macOS/Windows. Siete pruebas locales de almacenamiento/API pasan. El panel se abrió y la importación se comprobó en una app dev aislada con un proyecto temporal; no se usó ni se interrumpió la app del usuario.
- **Integración pendiente:** las importaciones existentes de Show en IndexedDB y las de otros editores aún no llaman este servicio. No borrar ese IndexedDB ni presentarlo como migrado. Conectar al resolver una carpeta real de proyecto, añadir referencias de asset ID a las decisiones y migrar por copia verificada antes de ofrecer limpieza de datos antiguos. Los generadores futuros deben escribir el resultado por este servicio. La limpieza de originales “sin usar” exige un grafo completo de referencias y no está habilitada: todavía no se puede probar que sea seguro borrarlos.
- El caché de previews se podrá regenerar cuando el renderer lo conecte; ahora el nuevo servicio no fabrica previews. No promete un ahorro cuantificado ni un límite de tamaño total automático. A futuro añadir presupuesto/aviso de espacio y borrado manual revisable de materiales no usados, respetando referencias de otros proyectos.
