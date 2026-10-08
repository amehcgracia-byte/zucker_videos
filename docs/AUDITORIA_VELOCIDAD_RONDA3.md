# Ronda 3 — movimiento y reproyección

Estado: experimentos y validación en curso. **Todavía no hay una medición nueva del master completo ni se ha demostrado la meta de 420 s.**

## Perfil principal: cuatro workers reales

Referencia: `1ffb168`, MacBookPro16,2 Intel, 4 núcleos físicos/8 lógicos, 32 GiB, Intel Iris Plus Graphics (Metal 3). FFmpeg 9.0.1, OpenCV 4.11.0 (GCD, 8 threads declarados). Aplicación cerrada durante las mediciones. Fuentes originales en `/Volumes/RAWVideos`; no se han borrado cachés. Plan congelado SHA-256 `5f8ae890664f1f8db7465e617a954a379c4469a5c141e41d48db0c5b48f37919`.

`tools/benchmarks/movement_costs.py` ejecuta 19 tomas 360 con cuatro workers: 16 distribuidas por la canción más los movimientos que faltaban. Incluye los nueve movimientos, también planeta → escenario. Instrumenta copias privadas del módulo; cada worker tiene sus propios contadores. Renderiza 1.999 fotogramas y conserva comandos y resultados. La corrección de color y los textos se neutralizan en este diagnóstico; no sustituye al benchmark completo del proyecto.

Datos: `/Volumes/RAWVideos/ZZSesions/benchmarks/render-round3-20261008-profile/results.json`.

| Medida | Segundos |
|---|---:|
| Pared del lote, cuatro workers | 173,243 |
| Suma de paredes de los 19 renders | 649,482 |
| Generación de mapas, incluyendo resize | 163,649 |
| Resize cúbico de mapas, incluido en la fila anterior | 13,670 |
| Resto de generación de mapas | 149,979 |
| Remap cúbico de píxeles | 174,255 |
| Espera/lectura del pipe del decoder | 226,214 |
| Espera/escritura del pipe del encoder | 34,083 |

Las sumas de workers **no son particiones de los 173 s de pared del lote**. Decode, conversión de formato, reproyección y encode se solapan. La lectura del pipe incluye espera, transferencia y competencia con otros procesos: no permite atribuir 226 s exclusivamente al decoder ni a la conversión BGR. El resize de mapas se mide por separado; no es el escalado de los píxeles del vídeo.

Ya se reutilizaban los mapas cuando la pose no cambiaba: hubo 880 generaciones para 1.999 frames. Un hold congelado produce un solo mapa. No se presenta esa reutilización existente como una mejora nueva. Cero eventos del watchdog en este lote.

### Conversión de formato: ablación con cuatro workers

`conversion_costs.py` repite ocho cortes de la misma fuente HEVC full-range validada. Conserva decoder VideoToolbox y `fps=30`, alternando terminar en YUV frente a convertir también a BGR24, con salida null. Orden YUV → BGR → BGR → YUV: **6,237 / 7,205 / 7,184 / 6,241 s** de pared por lote. La diferencia ronda 0,96 s en este replay; no es una partición del tiempo de los pipes del render real, que además transportan los bytes y sufren backpressure. Datos: `render-round3-20261009-conversion/results.json`.

## A — candidatos de mapas

### Módulo de longitud sin división

Prueba privada con sumas/restas en los valores dentro de una vuelta, conservando el módulo general como fallback. Los 19 MP4 son idénticos byte a byte. Generación de mapas: 135,561 s agregados frente a 163,649 s; pared del lote: **175,927 s frente a 173,243 s**. No se activa: una mejora de una función no demostró una mejora de la exportación.

Datos: `render-round3-20261008-fastwrap/results.json`, bajo la misma carpeta de benchmarks.

### Keyframes con la trayectoria original

Prototipo aislado `tools/benchmarks/keyframe_maps.py`, activado exclusivamente mediante `movement_costs.py --incremental`. Intervalo máximo de ocho frames; velocidad máxima 0,75°/s incluyendo FOV; vistas de polos y lentes anchas excluidas. La interpolación usa el progreso de la pose original, no una recta temporal que borraría el easing. Se alinean longitudes antes de interpolar para no cruzar incorrectamente la costura.

Se compara el mapa del punto medio con el mapa original en grados; tolerancia 0,001°, aceptando solo la mitad como margen. **Esta comprobación del punto medio no demuestra una cota matemática para cada píxel de todos los frames intermedios.** Las vistas no elegibles siguen por la ruta exacta. Los mapas vivos están acotados, no se acumula la película en RAM.

El primer ensayo con interpolación temporal rechazó todos los bloques y tardó 175,166 s. El corregido por pose interpoló únicamente **24 de 1.999 frames** y tardó 162,666 s. Esta diferencia de una ronda no puede atribuirse íntegramente a 24 frames ni extrapolarse al master. No se activa: la cobertura es solo el 1,2% de frames y la ruta exacta Metal ofrece una alternativa de mayor alcance sin aproximar los mapas. El prototipo queda exclusivamente en herramientas de benchmark.

Datos: `render-round3-20261008-incremental-valid` y `render-round3-20261008-incremental-pose`. Un intento previo falló al importar el prototipo antes de obtener mediciones; no se contabiliza como una ronda válida.

## Decisión sobre GPU (separada)

Prototipo Metal de laboratorio: `tools/benchmarks/metal_remap.mm` y `.py`, seleccionable únicamente mediante `movement_costs.py --metal-library`. Conserva los mapas y el kernel cúbico, la trayectoria, el FOV y las dimensiones. Usa cuantización de coordenadas a 1/32 como OpenCV; el cálculo de coeficientes y redondeo en GPU puede diferir de las tablas enteras de CPU. Por ello no se presupone igualdad de píxeles.

No hay importación desde producción ni activación automática. Se compila la biblioteca fuera del bundle. La herramienta `metal` no está instalada; el prototipo utiliza compilación de shaders en ejecución a través de Metal, con el puente construido mediante `clang++` y los frameworks del sistema.

La primera variante flotante tardó 154,023 s, pero cambió los píxeles. Se conserva como experimento rechazado, con SSIM/VMAF por frame en `render-round3-20261008-metal/quality`.

La segunda variante reproduce las tablas enteras de OpenCV (incluyendo su corrección de suma y redondeo): **143,701 s y los 19 MP4 idénticos byte a byte**, 1.999 frames. Datos y hashes: `render-round3-20261008-metal-integer`. Se está integrando en `core/metal_remap.py` y `core/native/metal_remap.mm`, con prueba de equivalencia antes de usar la GPU y fallback a CPU. El aviso de licencia original del archivo de OpenCV se conserva en el código y en `NOTICE.txt`.

Todavía no se ha cambiado el backend predeterminado. Falta el A/B completo de la integración; la igualdad de esta muestra no se presenta como validación del master entero.

## B — recorte previo y zooms planos

El plan contiene 55 movimientos de móvil. **Los 55 empiezan o terminan con zoom 1,0**: la unión de las ventanas de su trayectoria cubre el encuadre entero. Un recorte fijo previo que conserve el movimiento no puede quitar el 75% de esos píxeles. Pasar de 4K a 1080p es un downsample de toda la imagen, no descartar tres cuartas partes del encuadre.

`tools/benchmarks/zoom_quality.py` prueba por separado reducir el escalado intermedio a 1080p, con dos zooms reales de mayor amplitud y dos controles de Sony con movimiento añadido. La Sony no tiene movimiento en el plan congelado original. Conserva bitrate y dimensiones finales. **Se rechaza la reducción temprana.**

| Toma | Fuente | SSIM medio / mínimo | VMAF medio / mínimo |
|---|---|---|---|
| 2 | Móvil | 0,980436 / 0,940249 | 71,61 / 39,81 |
| 232 | Móvil | 0,981376 / 0,947920 | 74,12 / 45,99 |
| 116 | Sony, movimiento añadido | 0,990533 / 0,978554 | 88,41 / 66,95 |
| 156 | Sony, movimiento añadido | 0,990656 / 0,980736 | 88,16 / 75,32 |

Se revisaron el frame 45 de la toma 2 y su recorte ampliado del teclado. El encuadre general se conserva, pero cambian bordes y detalles; no se acredita una diferencia imperceptible. Además del doble remuestreo, trabajar en una rejilla más pequeña cambia la cuantización espacial del zoom. Se mantiene íntegra la ruta 4K actual.

Datos válidos: `render-round3-20261009-zoom-valid`, con métricas por frame y capturas. El primer ensayo `render-round3-20261009-zoom` tenía un error en el harness: reducía el scale pero conservaba el pad de 4K. Sus métricas no representan la propuesta y se excluyen; se corrigió el harness y se repitieron las cuatro tomas.

## C — verificación en una sesión y solapamiento

`_frame_md5_pair` abre el archivo una vez y obtiene dos muestras mediante ramas `split/trim`, con hashes independientes identificados por stream. Mantiene el redondeo de los seeks a milisegundos. No impone un límite de frames global que pueda cortar la segunda rama prematuramente. Falla si falta una muestra. Compara vídeo explícitamente: el audio distinto no debe hacer pasar una imagen congelada como movimiento.

Solapamiento experimental mediante `settings.export.overlap_segment_verification`: un verificador activo y como máximo otro pendiente, mientras los workers siguen renderizando. Memoria desconocida, inferior a 16 GiB o con menos de 2 GiB disponibles conserva la secuencia anterior. Cualquier fallo del verificador activa inmediatamente el mismo abort de los renders, incluso antes de que el hilo principal recoja su futuro. Todas las verificaciones deben terminar antes de ensamblar/publicar. Reparación, stamps de contenido, copias de concat y watchdog permanecen activos.

La opción todavía está desactivada por defecto, a la espera del A/B. No se interpretan los antiguos 640–663 s agregados de `verify_sec` como tiempo exclusivo de los dos probes: ese campo incluye también copias y hashes.

## D — master completo

Pendiente. `render_ab.py` permite fijar la referencia a `1ffb168` y carga también su módulo nativo, evitando comparar accidentalmente con el baseline anterior a las prioridades 1–2. Conserva el modo reproducible `--reference 7ae1d8d` de la auditoría anterior. Registrará cuatro rondas frío/caliente con orden invertido, recursos, fases, escrituras, cachés y eventos del watchdog.

## Tests

Referencia heredada: 116 pasan y 10 fallos conocidos de expectativas de hold/proyección/filtros. Pruebas nuevas de igualdad de muestras, falta de frames, imagen estática con audio variable y cancelación; pruebas de solapamiento con fallo de verificación, parada del siguiente render y conservación del master previo. Suite completa: **133 pasan / los mismos 10 fallos conocidos**, sin fallos nuevos. Comparación automática de nombres guardada en `work/render-round3/test-comparison.json`. El primer pase expuso la sensibilidad de una prueba sintética del watchdog al arranque de Python con timeout de 0,15 s: se estabilizó esa prueba con 1 s y un flujo que dura 1,6 s, sin cambiar los 120 s de producción; se repitió la suite completa.
