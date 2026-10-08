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

Resultados completos: pendientes de ejecutar y validar. No se declaran mejoras de 110–360 segundos por eliminar una pasada: esos números eran el coste completo observado de unión+mux, no ahorro ya demostrado. Las prioridades 3–5 no se implementan hasta revisar los resultados de 1 y 2.
