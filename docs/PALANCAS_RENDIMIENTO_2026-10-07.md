# Siete palancas de rendimiento — 7 de octubre de 2026

Revisión contra el código instalado 2.5.1 (`92c3549`) y cambios posteriores en el repositorio. No hay una reducción del 50% demostrada para el render completo.

| Palanca | Estado comprobado |
|---|---|
| Workers de proxies | Este Mac tiene 4 núcleos físicos y 8 lógicos (`sysctl`). Los 2 workers actuales ya equivalen a físicos menos 2. El número sigue configurable; no se aumenta sin medir memoria, decodificación y contención. |
| Encode por hardware | Proxies y segmentos normales intentan `h264_videotoolbox` en macOS, con respaldo de software. En Windows este código no selecciona automáticamente NVENC/QSV: esa mejora requeriría implementación y pruebas allí. |
| Presets | El respaldo de segmentos usa `veryfast`; el proxy esférico de exportación usa `ultrafast`. No había un `medium`/`slow` general que sustituir. |
| Unión sin doble encode | La unión normal de segmentos usa `-c copy` y el mux copia vídeo. La normalización de cadencia y la compatibilidad del passthrough 360 pueden necesitar recodificación. Añadir un master x264 después de los segmentos introduciría otra codificación en el recorrido normal. |
| Decode y escala | Los proxies ya intentan `-hwaccel videotoolbox` y reducen resolución. La reproyección nativa 360 conserva el original para mantener el detalle de primeros planos; no se ha aplicado una reducción general de calidad. |
| Solapar análisis | Se ha implementado análisis por fuente al terminar su proxy, mientras los workers preparan otras fuentes. Un solo consumidor de análisis limita memoria y evita compartir concurrentemente el detector. |
| Benchmark | Primera comparación sobre los mismos tres fragmentos de Kongroove, con cachés de la aplicación aisladas y frías/calientes. La aplicación estaba trabajando en Chemical Winds, por lo que el resultado no constituye un benchmark aislado de máquina ni de render completo. |

## Cambios concretos

- `prepare_videos` conserva los dos workers de normalización y entrega las fuentes terminadas a un análisis secuencial, sin esperar a todos los proxies.
- Cada fuente pasa una vez por encuadre y por presencia del operador cuando corresponde. No se repiten las pasadas al final de ingestión. Los análisis de encuadre y operador se omiten en Reel de una sola toma.
- El progreso reserva una fracción para esos análisis; el 100% de un proxy no hace que el análisis pendiente parezca completado. Cada tarea conserva su porcentaje local y un identificador por fuente.
- Encuadre y operador usan `grab()` para avanzar y `retrieve()` únicamente en los fotogramas seleccionados. No se cambian la densidad de muestreo, las imágenes analizadas ni los umbrales del detector.

## Evidencia y límites

Se extrajeron 20 segundos de tres archivos de la misma canción: Sony 1080p, iPhone 4K y 360 4K. La Sony ya era compatible y no necesitó proxy; las otras dos sí. Los originales, proyectos y cachés del usuario no se borraron. Los cachés de prueba estaban en `Cache/ingest-overlap-benchmark` en RAWVideos.

| Recorrido | Caché fría | Caché caliente |
|---|---:|---:|
| Preparar todo y después analizar | 191,708 s | 0,032 s |
| Analizar según terminan los proxies | 142,203 s | 0,061 s |

La diferencia observada en frío es 49,505 segundos. **No se atribuye como mejora estable del 25,8%**, porque había una tarea real de la aplicación en segundo plano y la segunda ronda con orden invertido se detuvo para no competir con ella. Las filas calientes confirman reutilización, pero los milisegundos de diferencia no sustentan una conclusión sobre velocidad. Esta comparación tampoco incluye edición o exportación completas.

Comparación adicional de las funciones de muestreo, sobre el mismo fragmento Sony y usando el mismo detector:

| Análisis | Antes | Después |
|---|---:|---:|
| Encuadre | 15,982 s | 14,651 s |
| Operador | 12,859 s | 12,890 s |

Los resultados de detección fueron exactamente iguales. Una sola ejecución no acredita una mejora estable del análisis; la reducción de conversiones es verificable y en operador el tiempo fue prácticamente igual. La igualdad de resultados permite conservar las cachés existentes.

Pruebas de solapamiento: un proxy lento espera a que empiece el análisis de otro ya terminado; la prueba falla si se vuelve a esperar a todos los proxies. También se verifica una sola llamada por fuente y tipo de análisis y que el progreso no dé la ingestión por completada prematuramente.

Pendiente para medir un ahorro global: repetición sin otro trabajo de la aplicación, orden invertido y exportación de la misma canción completa, con el mismo plan/semilla y calidad, en frío y caliente. El render/selección de frames en curso no se ha reiniciado para instalar estos cambios.
