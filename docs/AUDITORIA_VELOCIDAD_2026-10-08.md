# Auditoría de velocidad — 8 de octubre de 2026

Base: `8b6ab54`, aplicación original 2.5.4; correcciones empaquetadas e instaladas en 2.5.5. El render de Korven-s discussion se mantiene funcionando; ninguna tarea del usuario se cancela ni se reinicia para auditar.

## Evidencia real

El informe enviado de Is cold outside muestra 531,560 s de ingestión, 46,211 s de sincronización, 315,904 s de edición y 2236,871 s de exportación. Sus 230 segmentos consumieron 1868,743 s de pared. No es un problema exclusivamente de la interfaz.

El manifiesto actual corresponde a otro render de esa canción, `Is cold outside-youtube-20261007-230413-280636000.mp4`: 233 segmentos, 675,20 s de vídeo, cuatro workers. Render de segmentos 1976,820 s; unión por copia 113,321 s; comprobación de cadencia 5,952 s; mux 132,666 s. Suma de tiempos de verificación de los workers: 909,178 s; esta suma incluye concurrencia y no equivale a minutos ahorrables de pared. La edición actual figura como 36,821 s: no se atribuye a los cambios de esta auditoría.

Una muestra de dos segundos del proceso instalado durante Korven-s discussion registra lecturas y SHA-256 de segmentos. Las funciones de sellado calculaban el mismo digest al escribir el sello y al comprobarlo inmediatamente después.

## Recorrido revisado

| Sección | Hallazgo y tratamiento |
|---|---|
| Arranque/proyectos | Las tarjetas ya omiten tamaños recursivos. Cargas de modelos pesados son diferidas. Mantener ese recorrido. |
| Importación | Escritura de subida en bloques de 4 MB y deduplicación por contenido; no carga el vídeo entero en RAM. La transferencia desde navegador sigue teniendo un coste por byte. |
| Ingestión | Dos proxies concurrentes, intento de VideoToolbox y análisis por fuente según acaba. Encuadre y operador conservan cachés; son análisis distintos, no se puede borrar uno sin perder decisiones. |
| Sincronización | Audio de cámaras y envolventes cacheados. Spectral/HPSS solo se usan ante correlación débil; contienen costes adicionales de lectura/STFT. |
| Edición | Beats por ventana y parámetros, análisis musical sobre la señal ya cargada. Demucs de seis instrumentos es opcional, caro y ajeno a Whisper. Calidad visual de Sony se repetía entre proyectos: ahora se comparte por firma de fuente. |
| Review/frames | Proxy esférico compartido, miniaturas por pose y firmas; exportación sigue usando originales. No disminuir la calidad del render para acelerar miniaturas. |
| YouTube | No llama a Whisper. Color fijo por cámara, sin analizar cada corte. |
| Reel | Omite medición de color y análisis de operador/encuadre en toma única. Overlays finales pueden requerir recodificación y son trabajo real. |
| 360 navegable | Omite proxies/análisis planos; INSV requiere cosido. |
| Backstage | Transcripción por firma/modelo, caché compartida. Análisis de relato y composición pertenecen a este modo. |
| Medley | Highlights musicales y visuales cacheados; audio de vídeos válido. Excerpts y fundidos requieren render. |
| Segmentos | Hardware cuando disponible, software veryfast como respaldo. Copia a caché innecesaria y hashes repetidos corregidos. |
| Reproyección 360 | Original → BGR → mapas → remap cúbico → encoder. Reutilizar rejillas y búfer de salida; conservar resolución, interpolación y píxeles. Sigue siendo trabajo por fotograma. |
| Unión/audio | Unión y mux copian vídeo; no hay encode final normal duplicado. Dos pasadas de disco, faststart y publicación completa aún tienen coste. |
| Progreso | Leer el registro completo cada refresco era trabajo creciente: lectura desde el final. stderr de FFmpeg podía bloquear su pipe: ahora se vuelca al disco de trabajo. |

## Cambios aplicados

- Publicar segmentos mediante rename en el mismo volumen; copiar únicamente si el sistema devuelve EXDEV. También en reparaciones.
- Omitir el hash inmediato redundante después de escribir o comprobar el sello: la copia independiente de concat sigue verificándose íntegramente. No memorizar hashes entre exportaciones: se observaron archivos con mtime/ctime en segundos enteros, insuficientes para detectar todas las modificaciones por tamaño/fecha. La inspección actual identifica RAWVideos como USB/UFSD_NTFS; no se presupone su tipo a partir de las fechas.
- Compartir calidad visual por fuente; migrar la caché previa sin analizar otra vez. Recalcular tiempos master desde los tiempos de clip para cada proyecto, evitando reutilizar offsets de otra canción.
- Temporales de calidad en el almacenamiento seleccionado.
- Reutilizar rejilla de proyección y array de salida. Comparación contra código anterior en nueve poses: mapas y píxeles idénticos.
- Diagnósticos de FFmpeg fuera de pipe; mantener los últimos 64 KiB al fallar. Prueba con 1 MiB de errores que antes podía bloquear.
- Obtener solo el tail necesario del log, con pruebas de UTF-8 y líneas largas.

Se probó omitir filtros de color neutros. FFmpeg cambia formato/chroma al ejecutarlos y no produjo los mismos píxeles: se descartó el cambio.

## Concurrencia medida

Se capturó el comando real de un segmento con movimiento de cámara fija de Is cold outside. Cada grupo renderiza cuatro segmentos de un segundo, con las mismas fuentes, filtros, encoder VideoToolbox y bitrate. Orden invertido, con la aplicación cerrada y sin otros tests activos:

| Hilos de filtros por FFmpeg | Primera ronda | Segunda ronda |
|---|---:|---:|
| Automático | 9,797 s | 11,248 s |
| 2 | 10,373 s | 12,530 s |
| 1 | 16,422 s | 18,337 s |

No se fuerza un hilo ni dos: ambos empeoraron este caso. El ensayo no demuestra el ajuste óptimo para todas las fuentes ni para Windows. Cada archivo resultante tuvo 604783 bytes; no se usa igualdad de tamaño como prueba de igualdad de píxeles.

## Reproyección original antes/después

Cuatro rondas con el mismo segundo 60 del archivo 360 original 3840×1920 de Is cold outside, 60 frames por ronda, movimiento push_in y salida 1920×1080. Orden antes/después/después/antes, sin aplicación ni tests ejecutándose:

| Código | Ronda 1 | Ronda 2 |
|---|---:|---:|
| Anterior | 5,122 s | 4,995 s |
| Búfer/rejilla reutilizados | 4,857 s | 4,899 s |

Los 60 hashes de fotograma coinciden exactamente en las cuatro rondas. Incluye decode, reproyección, pipe y conversión de píxeles; no incluye el encoder H264 ni las otras fases de la aplicación. La diferencia es modesta y no acredita una reducción grande del render completo.

## Límites de las conclusiones

Pruebas dirigidas: 66 pasan, con tres avisos de dependencias obsoletas. Comprobación final de I/O y sellos: ocho pasan. Suite amplia de exportación: 80 pasan y diez fallan; se han reproducido exactamente esos diez fallos usando export.py de `8b6ab54`, sin los cambios de esta auditoría. Son expectativas previas sobre hold/proyección/filtros, no una suite completamente verde. Scripts, muestra y resultados reproducibles en `work/audit-speed-20261008`. No se promete un porcentaje global a partir de microbenchmarks ni se declara alcanzado un máximo físico.

Para medir el render entero hace falta el mismo plan, fuentes, calidad, estado de caché y máquina sin otra edición activa. Instalar durante el render alteraría la prueba y arriesgaría el trabajo. Windows hardware necesita medición en Windows; este Mac no demuestra rendimiento NVENC/QSV.

Los siguientes cambios de arquitectura a evaluar son mux directo desde concat (evitar el vídeo intermedio manteniendo reparación de cadencia), compartir decodificación entre análisis con muestras compatibles, procesamiento de proyección por GPU y selección de concurrencia según medidas. No son ahorros ya demostrados ni pueden presentarse como implementados.

## Distribución verificada

2.5.5 instalada en /Applications/Zucker Editor.app, commit de código `38a7b4cfb25d1adda8d727f38819a5753bb5c520`. Self-test empaquetado Mac: HTTP 200, intro, transcripción y carga del modelo instrumental correctos; firma y DMG válidos. Compilación Windows `37753289091` y promoción verificada `37754399651` completadas con éxito. Paquetes publicados juntos en la release v2.5.5 tras comprobar los hashes de ambos assets.

Las cachés válidas de segmentos no dependen del commit Git, sino de la receta explícita: esta revisión sin cambio de píxeles puede reutilizarlas. Se eliminaron las copias temporales de esta compilación y se conservó el spec local del usuario. No se midió un antes/después de canción completa y no se presenta como realizado.
