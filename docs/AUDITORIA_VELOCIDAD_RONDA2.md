# Auditoría de velocidad — ronda 2

8 de octubre de 2026. Referencia de instrumentación: `8653b4e`, con las prioridades 1 y 2 ya aplicadas. Meta pendiente: primera exportación completa por debajo de 420 s.

## A. Desglose por segmento y decisión principal

**24 segmentos reales y 24 controles pareados, tres fuentes: apertura/seek y llegada al primer frame decodificado suman el 5,93% de la pared de render de la muestra real. No se implementa render por bloques en esta ronda**, siguiendo el umbral de 15% solicitado. Esto descarta esa inversión por el criterio definido, no demuestra que reutilizar procesos tenga ahorro cero ni identifica toda la inicialización interna de un encoder hardware.

La muestra toma ocho posiciones repartidas por el montaje de cada cámara. Los controles bloquean la pose 360, quitan el movimiento del móvil o añaden al Sony la misma receta válida de zoom usada por el móvil. La cámara Sony del plan no tenía movimientos. Son controles diagnósticos, no cambios en el montaje del usuario. Incluye hold/close_hold, reveal, settle, push_in y zooms de entrada/salida; no pretende cubrir cada efecto de la biblioteca.

Misma máquina que la ronda anterior (MacBookPro16,2, 4 núcleos físicos/8 lógicos, 32 GiB), FFmpeg 9.0.1, aplicación cerrada. Cortes individuales secuenciales, sin otros renders ni tests durante la muestra principal, fuentes originales, 1920x1080, 30 fps y 18 Mbps. No se purgan cachés ni se ejecuta el ensamblado del master. Se usa perfil de color neutro en ambas ramas para aislar fuente/movimiento: esta muestra no sustituye la comparación del proyecto completo con su calibración y cuatro workers.

### Qué se mide y qué no se debe sumar

- **Arranque/apertura/seek:** cota superior hasta el primer frame decodificado. En 360 se mide dentro de la tubería real, incluyendo los dos Popen y la primera lectura BGR. En Sony/móvil se ejecuta una lectura aislada del primer frame original; incluye su decode. No es un cronómetro de apertura pura ni de inicialización interna de GPU.
- **Decode aislado:** FFmpeg recorre el corte al sink null, decoder por CPU. Es una referencia separada; no es el tiempo exclusivo del decoder hardware 360 concurrente.
- **360 real:** cronómetros directos de lectura del pipe, pose/mapa/remap, escritura al encoder y espera final. Lectura y escritura incluyen backpressure. Decode y encode avanzan en hijos simultáneos; sus tiempos de cómputo no pueden deducirse íntegramente de estas esperas.
- **Sony/móvil, decode + filtros:** replay del mismo filter_complex al sink null, sin encoder H.264. Restar el replay de decode da un estimador de coste incremental, no una fase exclusiva exacta.
- **Encode aislado:** se reconstruyen los píxeles filtrados originales en NUT/rawvideo y se codifican con los mismos argumentos de bitrate. Incluye apertura/lectura del NUT y empaquetado del sink null; excluye el mux MP4/faststart real. Los intermedios se borran inmediatamente. Es una prueba de componente, no encode puro ni tiempo que pueda sumarse al render completo. El replay 360 se hizo después de la muestra principal; sus valores son diagnósticos, no se usan para decidir el porcentaje de arranque.
- **Verificación:** duración/frame count y verificación de movimiento existentes sobre el resultado de cada muestra. No incluye todas las copias y hashes de atestación del job completo.

Los replays son ejecuciones distintas y sus paredes se solapan conceptualmente dentro de la ejecución real. Una partición exclusiva exacta decode/filtros/encode requeriría instrumentar FFmpeg/driver; no se inventa esa precisión.

### Agregado por fuente: cortes reales

Valores sumados en segundos de los ocho cortes de cada fuente; no son pared de un proyecto concurrente.

| Fuente | Cuerpo | Render | Primer frame (cota) | % render | Decode CPU aislado | Decode + filtros aislado | Encode + lectura NUT aislado | Verificación |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| VID_20260831_223407_00_011.mp4 | 29,100 | 72,422 | 4,569 | 6.31% | 21,979 | — | 27,825 | 5,517 |
| VID_20260831_223148.mp4 | 29,133 | 77,109 | 2,368 | 3.07% | 4,357 | 77,497 | 33,058 | 5,527 |
| C0220.MP4 | 22,200 | 36,904 | 4,124 | 11.18% | 7,439 | 23,513 | 14,660 | 6,274 |

En 360: lectura del decoder **21,398 s**, pose/mapa/remap **35,584 s**, escritura/backpressure del encoder **8,799 s**, espera final de encoder **4,604 s**, para **72,422 s** de render. El primer frame está incluido en las lecturas; no se vuelve a sumar. Mapa/remap supone un 49,1% de esa pared aislada. No se extrapola ese porcentaje al render con cuatro workers.

En móvil: decode + filtros pasa de **77,497 s** con sus movimientos a **26,458 s** sin ellos; el render real pareado pasa de **77,109 a 39,634 s**. En Sony, añadir movimientos eleva decode + filtros de **23,513 a 57,958 s** y render de **36,904 a 61,026 s**. En 360, bloquear la pose baja mapa/remap de **35,584 a 19,616 s**, pero el render total solo baja de **72,422 a 61,219 s**: cambian las esperas entre etapas, por lo que no se suman los ahorros de componentes.

El agregado de arranque es 6,31% en 360, 3,07% en móvil y 11,18% en Sony. Hay dos Sony cortos que superan individualmente 15% (17,16% y 16,53%); no cambian el agregado del grupo ni el criterio sobre la muestra completa. El corte de primer frame es una cota con trabajo de decode incluido, por lo que apertura pura pesa menos.

### Por tipo de movimiento: cortes reales

| Cámara / movimiento | Cortes | Render (s) | Verificación (s) |
|---|---:|---:|---:|
| 360 / close_hold | 1 | 9,058 | 0,596 |
| 360 / reveal | 2 | 23,446 | 1,374 |
| 360 / settle | 2 | 12,640 | 1,250 |
| 360 / hold | 2 | 13,465 | 1,474 |
| 360 / push_in | 1 | 13,813 | 0,823 |
| estatica / zoom_in_very_slow | 4 | 46,929 | 2,802 |
| estatica / zoom_out_very_slow | 4 | 30,180 | 2,725 |
| sony / none | 8 | 36,904 | 6,274 |

### Registro individual: 24 cortes reales

`D` es decode CPU aislado; `F` es decode + filtros aislado (360: mapa/remap directo); `E` es replay encode + lectura NUT. No sumar D/F/E. Las 24 variantes de control y los cronómetros completos están en el JSON de evidencia.

| Índice del plan | Cámara | Movimiento | Cuerpo (s) | Render (s) | Primer frame (s) | D (s) | F (s) | E (s) | Verificación (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 360 | close_hold | 5,300 | 9,058 | 0,525 | 3,296 | 2,852 | 10,698 | 0,596 |
| 35 | 360 | reveal | 3,733 | 12,354 | 0,449 | 2,717 | 8,561 | 1,364 | 0,688 |
| 67 | 360 | reveal | 3,100 | 11,091 | 0,716 | 2,887 | 7,238 | 1,111 | 0,686 |
| 101 | 360 | settle | 2,500 | 8,354 | 0,521 | 2,136 | 5,245 | 2,420 | 0,657 |
| 133 | 360 | settle | 1,300 | 4,287 | 0,428 | 1,292 | 2,570 | 0,815 | 0,594 |
| 167 | 360 | hold | 3,100 | 6,839 | 0,738 | 2,729 | 1,913 | 4,618 | 0,700 |
| 199 | 360 | hold | 3,100 | 6,627 | 0,618 | 2,268 | 2,143 | 4,995 | 0,775 |
| 233 | 360 | push_in | 6,967 | 13,813 | 0,575 | 4,655 | 5,061 | 1,804 | 0,823 |
| 2 | estatica | zoom_in_very_slow | 6,167 | 17,925 | 0,370 | 0,862 | 17,941 | 12,227 | 0,805 |
| 18 | estatica | zoom_in_very_slow | 3,100 | 8,166 | 0,263 | 0,504 | 7,352 | 4,508 | 0,642 |
| 32 | estatica | zoom_out_very_slow | 2,800 | 7,394 | 0,353 | 0,492 | 8,504 | 1,305 | 0,691 |
| 48 | estatica | zoom_out_very_slow | 2,667 | 6,918 | 0,259 | 0,433 | 6,092 | 1,542 | 0,678 |
| 64 | estatica | zoom_out_very_slow | 3,467 | 8,465 | 0,245 | 0,462 | 7,856 | 1,679 | 0,668 |
| 168 | estatica | zoom_out_very_slow | 2,633 | 7,404 | 0,314 | 0,478 | 6,203 | 1,397 | 0,689 |
| 202 | estatica | zoom_in_very_slow | 2,967 | 7,718 | 0,286 | 0,478 | 6,907 | 1,496 | 0,691 |
| 232 | estatica | zoom_in_very_slow | 5,333 | 13,119 | 0,278 | 0,648 | 16,641 | 8,905 | 0,665 |
| 76 | sony | none | 3,533 | 4,509 | 0,422 | 0,918 | 3,117 | 1,220 | 0,647 |
| 94 | sony | none | 3,067 | 5,401 | 0,661 | 1,265 | 3,972 | 1,158 | 0,867 |
| 110 | sony | none | 3,100 | 4,555 | 0,451 | 0,852 | 2,847 | 1,125 | 0,684 |
| 128 | sony | none | 3,667 | 5,844 | 0,508 | 1,019 | 3,249 | 1,265 | 0,700 |
| 144 | sony | none | 3,100 | 3,980 | 0,401 | 0,823 | 3,100 | 4,856 | 0,690 |
| 166 | sony | none | 2,633 | 5,224 | 0,436 | 0,800 | 2,780 | 2,948 | 0,748 |
| 192 | sony | none | 1,333 | 3,724 | 0,639 | 0,877 | 2,091 | 0,825 | 1,001 |
| 226 | sony | none | 1,767 | 3,668 | 0,606 | 0,886 | 2,357 | 1,263 | 0,936 |

## B. Watchdog de segmentos

120 segundos sin nuevos frames/timestamps/escrituras ni aumento de CPU acumulada del hijo. Se revisa cada dos segundos: un proceso activo sin frames nuevos no se declara bloqueado. Mensajes repetidos con el mismo frame no reinician el contador. Si la medición de CPU no está disponible se actúa conservadoramente, sin matar por una suposición. En la reproyección, mientras Python está calculando el mapa no se considera que espera a un hijo.

Se aplica dentro del render de segmentos; no impone ese timeout a un mux, concat o análisis fuera de ese ámbito. Los callbacks periódicos permiten cancelar aunque stdout esté bloqueado. En la tubería 360 se observa CPU de ambos hijos y se termina el hijo donde espera la lectura/escritura; la limpieza existente recoge al compañero para evitar huérfanos. No se mata la app, el worker Python ni procesos de otro segmento.

Antes de terminar el hijo se toma una muestra de pila limitada (macOS `sample`, con timeout de seis segundos). Evento con PID, comando, segmento, fuente, intento, timeout, CPU, salida, fecha y muestra se guarda atómicamente en `artifacts/export_watchdog_manifest.json`. Si se recupera, consta `cpu_retry_succeeded`; al terminar la exportación se incluye `watchdog_events` en `export_manifest.json`. El diagnóstico separado permanece disponible si la exportación falla antes de publicar el master. La terminación ocurre también si falla escribir el diagnóstico, para no dejar el proceso bloqueado.

Un único reintento por CPU para esa ruta de segmento, compartiendo presupuesto si después se pide reparar el mismo corte. Mantiene receta, filtros, corte, resolución e interpolación; usa libx264 con el mismo bitrate y decoder CPU en 360. No cae en bucles ni en un proxy nuevo por este timeout. Segundo fallo aborta mediante la ruta existente de fallo de segmentos. Solo se elimina el archivo parcial de ese intento; no se vacían cachés ni se cambian recetas/hashes.

Los renders sanos siguen enviando los mismos comandos. Los tres cortes comprobados después del watchdog (360, móvil y Sony) tienen framemd5 y archivo completo idénticos a los de la instrumentación previa. No se afirma identidad binaria entre encoder hardware y un reintento libx264; este último es el mecanismo de recuperación con parámetros de calidad conservados.

CPU acumulada: ps en macOS, /proc en Linux y GetProcessTimes en Windows. Esta entrega se prueba en macOS; no se ha ejecutado el watchdog en Windows. Linux puede denegar stacks del kernel y Windows no tiene aquí un muestreador nativo de pila: esos casos quedan marcados como no disponibles, sin inventar una muestra.

## C. Bloques

**No implementados ni activados.** El criterio de A está por debajo del 15%. No hay flag ni mejora de bloques que validar o revertir. Tampoco se lanza otra comparación completa de media hora para una implementación que no se ha hecho.

El siguiente candidato es reducir el trabajo de movimiento/reproyección y las transferencias de frames: perfilar mapa frente a remap y su escalado con cuatro workers, y medir una vía GPU que conserve las curvas e interpolación. Un remap GPU aislado no basta como promesa de 5,7x: decode, transferencias, filtros, encode y ensamblado siguen existiendo. El objetivo de 7 minutos sigue sin demostrarse.

## Validación y evidencia

- Suite de exportación y primeros diez tests de watchdog: 90 pasan / los mismos 10 fallan, 186,04 s. Comparación automática con la referencia: cero nombres de fallo nuevos o resueltos.
- Pasada dirigida final: **36 pasan** (13 watchdog, 9 movimiento nativo, 8 decoder HW, 3 concat directo, 3 publicación atómica). En total, sin contar tests repetidos: **116 pasan / 10 fallos preexistentes**.
- Bloqueo real simulado con hijo que duerme: muestra registrada, solo ese hijo terminado, segundo hijo ajeno sobrevive y recuperación CPU única. Tests adicionales comprueban CPU real activa sin frames, progreso real con CPU constante, mensajes de frame estancado, cancelación de lectura bloqueada y agotamiento del presupuesto de reintentos.
- Tres renders sanos: identidad completa de frames y binaria, duración/cadencia verificadas, ningún evento de watchdog. La reparación de cadencia y publicación atómica conservan los tests de prioridad 2.

Herramienta reproducible: `tools/benchmarks/segment_costs.py`, referencia fijada en `8653b4e` para que futuros cambios no contaminen la medición. Datos preservados en `/Volumes/RAWVideos/ZZSesions/benchmarks/render-round2-20261008/`: `results.json` (muestra principal), `native-encode-replay.json` (encoder 360 aislado), `consolidated.json` (48 registros completos), `healthy-quality.json` y hashes de frames. El primer intento de selección falló antes de renderizar, se corrigió y no forma parte de las mediciones. Logs de tests en `work/render-round2/`.
