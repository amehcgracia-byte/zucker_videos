# Implementación de rendimiento — prioridades 1 y 2

8 de octubre de 2026. Prioridad 1: `7ae1d8d`. Prioridad 2: `055e7e6`.

## Condiciones y objetivo

Objetivo: exportación de un proyecto completo por debajo de 420 segundos sin pérdida observable. No alcanzado ni demostrado todavía. El plan fijado de Is cold outside tiene 233 segmentos, 655 segundos de cuerpo y 675 con intro/outro: no son 233 cortes de un segundo. El informe de 2236,871 segundos corresponde a otro render anterior (230 segmentos); no se mezcla con los 233 segmentos para medir mejora.

La aplicación instalada era 2.5.6; el usuario autorizó cancelar el render activo que tenía 176/177 segmentos terminados. Un FFmpeg de Sony llevaba unos 28 minutos sin CPU; la muestra de pila registra esperas de scheduler/threads. No demuestra por sí sola un fallo específico de VideoToolbox. Se detuvo solo ese proceso, después de solicitar cancelación. Siguen presentes los 1560 archivos de segmentos cacheados inventariados antes de cancelar. La aplicación después se encontró cerrada; no hay otros renders activos para las mediciones.

## Prioridad 1 — decoder 360

Activación conservadora en macOS para el HEVC 8-bit full-range yuvj420p/bt709 de los originales comprobados. Conversión a su formato planar original antes de fps/BGR. Otras fuentes mantienen CPU; no se fuerza yuvj420p en rango limitado/HDR ni en codecs no validados. Los metadatos de fuente se reutilizan en memoria; no se introdujo una caché de hashes por timestamps. Interpolación, tamaño, bitrate y trayectoria no cambian.

Un error propio del decoder hardware dispara una repetición por CPU con el mismo corte y pose. Error de encoder o cancelación no se confunde con un fallo del decoder. Logs indican backend. 17 pruebas pasan.

Corte entero de 5,3 segundos, movimiento reveal, 1920x1080 y bitrate real de 18 Mbps; orden CPU → hardware → hardware → CPU. Los cuatro resultados tienen exactamente los mismos fotogramas decodificados y 12341000 bytes. Estos tiempos son de un corte, no del proyecto completo, y no acreditan un ahorro global:

| Backend | Render (s) | Verificación (s) |
|---|---:|---:|
| CPU, primera ronda | 14,670 | 0,480 |
| Hardware, primera ronda | 16,440 | 0,495 |
| Hardware, segunda ronda | 15,337 | 0,484 |
| CPU, segunda ronda | 16,656 | 0,496 |

No hay mejora clara de pared en este corte al bitrate real. Se redujo el trabajo de CPU de procesos hijos, pero falta medir el proyecto completo con concurrencia real. Son rondas repetidas de corte sin caché de salida; no se presentan como la prueba fría/caliente del proyecto entero. Las pruebas iniciales a 4 Mbps se conservaron como diagnóstico y no se usan como referencia de calidad del proyecto.

## Prioridad 2 — concat y audio en una pasada

Para montajes sin transiciones ni overlays finales pendientes, concat demuxer y master-audio entran en un único FFmpeg: vídeo por copia, audio con los mismos trim/delay/fades. Se mide espacio con el tamaño de los segmentos, no con el pequeño archivo de lista. El master queda privado hasta comprobar streams, duración, cadencia, número exacto de frames y las comprobaciones finales de movimiento/audio existentes; se publica por el mecanismo de rename con respaldo EXDEV existente.

Si falla la cadencia o el número de frames, se descarta la propuesta privada y se ejecuta la ruta anterior con reparación de cadencia. No se ocultan errores de mux/disco como problemas de cadencia. Transiciones y overlays conservan su recorrido previo. No se eliminó ninguna caché de usuario.

360 navegable se estudió: su cuerpo ya se copia sin recodificación y puede tener FPS/timebase del original (por ejemplo 25 fps), además de inyección de metadatos esféricos. Mantiene el recorrido previo hasta validar una vía directa específica; no se impone el control 30 fps del montaje plano.

Prueba real de dos cortes: todos los hashes de los frames de vídeo y de audio del master directo coinciden con el master de la ruta antigua. Cadencia inválida protege el resultado previo y pide reparación; fallo de disco se propaga. Suite amplia de exportación: 92 pasan y los mismos 10 fallan que en la auditoría previa; comparación automática de nombres confirma cero fallos nuevos. Comprobación dirigida final: 14 pasan.

## Medición del proyecto entero

Herramienta reproducible: `tools/benchmarks/render_ab.py`. Referencia: export.py de 7ae1d8d con decoder CPU; variante: implementación nueva. Plan y configuración congelados; mismo bitrate, fuentes y máquina. Cachés privadas de segmentos: referencia fría → variante fría → variante caliente → referencia caliente. No se vacían ni se modifican las cachés originales. Frío significa caché privada de segmentos vacía, no vaciado de caché del sistema operativo ni de fuentes/análisis.

Se registran pared por fase y por segmento, CPU, RSS agregada, vm_stat antes/después y bytes lógicos de escrituras de medios terminadas. El contador de bytes incluye resultados FFmpeg y copias de medios, pero no journaling, JSON, escrituras parciales fallidas ni la reescritura interna de faststart. Abrir la aplicación o dejar de recibir progreso durante 180 segundos invalida el benchmark y detiene únicamente sus propios procesos.

### Resultados completos

Máquina comprobada: MacBookPro16,2, 4 núcleos físicos / 8 lógicos, 32 GiB de RAM. FFmpeg 9.0.1. Cuatro workers, 1920x1080 a 30 fps, bitrate configurado de 18 Mbps. Aplicación cerrada y sin otros renders durante las cuatro rondas. Intro/outro existentes cacheados en ambas variantes. No se vació la caché del sistema operativo: el orden inverso reduce, pero no elimina, los efectos de caché de lectura y variabilidad del disco.

| Pared (s) | Antes fría | Después fría | Después caliente | Antes caliente |
|---|---:|---:|---:|---:|
| Exportación completa, incluidas comprobaciones | 1822,281 | 1738,875 | 276,624 | 460,908 |
| Segmentos: render/verificación/copias | 1625,032 | 1548,998 | 141,305 | 194,198 |
| Concat intermedio | 58,289 | 0 | 0 | 59,330 |
| Audio y vídeo/master directo | 114,853 | 149,563 | 114,873 | 182,071 |
| Comprobación de cadencia del master | 4,981 | 5,365 | 4,692 | 4,686 |
| Intro | 0,150 | 0,166 | 0,316 | 0,085 |
| Outro | 0,211 | 0,208 | 0,157 | 0,249 |
| Preparación de fuentes | 0,001 | 0,001 | 0 | 0,001 |
| Segmentos reutilizados / 233 | 0 | 0 | 233 | 233 |

Los totales incluyen perfiles de color, preparación, comprobaciones finales y otras esperas no representadas por las filas de fases; no se suman estas filas para sustituir la medición completa. Los tiempos individuales de verificación se ejecutan concurrentemente: sus sumas (643,728 / 663,133 / 391,650 / 419,247 segundos, mismo orden) no son pared del proyecto.

Primera exportación: **30:22 → 28:59**, diferencia observada de **83,406 s (4,58%)**. Repetición íntegramente cacheada: **7:41 → 4:37**, diferencia de **184,284 s (39,98%)**. Solo hay una ronda por condición: estos resultados no son una garantía estadística ni una previsión para otros proyectos. El resultado caliente no equivale a exportar un montaje nuevo.

En frío, ensamblado más cadencia: **178,123 → 154,928 s**, ahorro observado de **23,195 s**. En caliente: **246,087 → 119,565 s**, ahorro observado de **126,522 s**. Por tanto, no se presenta el coste previo de unión+mux de 110–360 segundos como ahorro garantizado. La fase de segmentos baja 76,034 s en la comparación fría; no se atribuye todo ese cambio exclusivamente al decoder, porque intervienen lectura, encoder, concurrencia y variabilidad de la máquina.

### Recursos y escrituras

| Medida | Antes fría | Después fría | Después caliente | Antes caliente |
|---|---:|---:|---:|---:|
| CPU Python (s de CPU) | 1993,467 | 2008,062 | 11,622 | 14,318 |
| CPU de hijos (s de CPU) | 8215,403 | 7005,324 | 53,575 | 62,482 |
| CPU agregada media muestreada (%) | 512,6 | 472,1 | 22,0 | 11,7 |
| RSS agregada máxima muestreada (MiB) | 4684,4 | 5928,6 | 323,4 | 338,6 |
| Escrituras lógicas de medios terminados (bytes) | 6129807772 | 4599939415 | 3076588370 | 4606456727 |
| Tamaño de cada master (bytes) | 1546590978 | 1546590978 | 1546590978 | 1546590978 |

CPU agregada puede superar 100% por procesos/hilos concurrentes; la media usa los valores de `ps`, no una medida instantánea de utilización de GPU. RSS suma procesos y puede contar páginas compartidas más de una vez. Hardware consume más RSS en esta prueba, aunque reduce el tiempo de CPU de hijos. Los contadores globales `vm_stat` no muestran nuevos swapins/swapouts en ninguna ronda. Deltas de compresión: 53934 / 4168 / 0 / 0; descompresión: 32854 / 39581 / 2096 / 1411. Son indicadores globales de memoria, no una medición aislada de presión ni del uso de GPU.

Se evita una escritura lógica de aproximadamente **1,53 GB** por master. El contador tiene el alcance descrito arriba: no permite afirmar esos mismos bytes de tráfico físico ni incluye la segunda pasada interna de `faststart`.

### Evidencia y siguientes decisiones

Datos de las cuatro rondas, tiempos por cada segmento, muestras de recursos, plan congelado y resultados se conservan en `/Volumes/RAWVideos/ZZSesions/benchmarks/render-performance-20261008-180618/`. Plan SHA-256: `5f8ae890664f1f8db7465e617a954a379c4469a5c141e41d48db0c5b48f37919`. Se conserva también el fallo inicial de preparación de la referencia por ausencia de `__file__` en el módulo dinámico: ocurrió antes del render, se corrigió el runner y no se cuenta como ronda. Las cuatro rondas válidas terminaron sin advertencias de exportación.

Los cuatro masters tienen exactamente el mismo SHA-256 de archivo completo: `09df8abf2b1ab69f2d8a1bc33255ebb6c70feec16385bccde5a2d31ece5a2bbc`. Esto comprueba igualdad binaria de todos los fotogramas codificados, audio, cortes, timestamps y metadatos, más fuerte que comparar muestras. FFprobe registra en los cuatro: vídeo H.264, 1920x1080, 30/1 fps, 20256 frames, yuv420p, rango tv/bt709; audio AAC estéreo a 44100 Hz. Ambos streams duran **675,200 s**, la duración real del plan normalizado y sus cabeceras, idéntica antes/después. No se afirma una duración exacta de 675,000 s.

Decodificación completa terminada correctamente con `-xerror` y `-map 0:v:0 -map 0:a:0 -f framemd5`: **20256 fotogramas de vídeo y 29079 bloques de audio decodificados**, sin error. La igualdad de los cuatro archivos permite realizar esta lectura completa una vez. Evidencia en `quality/metadata-hashes.json`, `quality/before-cold.framemd5` y `quality/result.json` dentro del directorio de medición.

**La meta de menos de 420 s no se alcanza en la primera exportación.** La fase de segmentos sigue costando 1548,998 s, antes del ensamblado. El siguiente trabajo debe centrarse en ese coste. El render por bloques es candidato, pero debe medirse: la media de los cortes es 2,81 s y estos datos no demuestran que el arranque de FFmpeg domine sobre la reproyección. Las prioridades 3–5 no se implementan hasta revisar los resultados de 1 y 2, tal como pidió el usuario. No se presenta como arreglado el bloqueo del render cancelado: hace falta investigar por separado su scheduler/encoder.
