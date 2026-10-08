# Segunda evaluación de rendimiento — 8 de octubre de 2026

Código evaluado: `49b3570` (2.5.9). Aplicación instalada comprobada: 2.5.6, commit `51726f0`. Los cambios de 2.5.7–2.5.9 preparados en este repositorio todavía no forman parte del proceso instalado. No atribuir los tiempos históricos ni la percepción de velocidad del render activo a esos cambios. Auditoría de lectura, sin cerrar la aplicación, borrar cachés ni iniciar benchmarks pesados. Al comprobar el proceso actual, estaba exportando con 86/177 segmentos completos.

## Evidencia y alcance

Se recorrieron importación, preparación de fuentes, análisis visual, sincronización, selección musical, edición, revisión, exportación, composición, proyectos y los recorridos de YouTube, Reel, 360 navegable, Backstage y Medley. Se contrastaron siete manifiestos completos, la auditoría previa y las pruebas de hardware ya realizadas. No es una medición nueva de canción completa ni una prueba de Windows.

Cantalo Disfrutalo Inventalo: exportación 3324,875 s; segmentos 2946,912 s de pared. La 360 acumuló 7497,9 s de FFmpeg para 331,7 s de montaje, la Sony 1031,1 s para 193,3 s y el móvil 2309,3 s para 152 s. Estas sumas incluyen concurrencia: no se suman al tiempo de pared ni representan minutos directamente ahorrables.

| Proyecto | Segmentos | Unión (s) | Audio/mux (s) | Cadencia (s) |
|---|---:|---:|---:|---:|
| All I got | 194 | 80.2 | 110.3 | 5.6 |
| Cantalo Disfrutalo Inventalo | 170 | 122.9 | 184.4 | 10.6 |
| Chemical Winds | 182 | 99.2 | 189.8 | 5.9 |
| Colour Storm | 226 | 63.7 | 112.3 | 5.9 |
| Is cold outside | 233 | 113.3 | 132.7 | 6.0 |
| Korven-s discussion | 164 | 101.6 | 113.7 | 6.2 |
| Listening to the Abroad | 146 | 125.1 | 233.3 | 18.1 |

Unión + mux: mínimo 175,97 s; mediana 245,99 s; máximo 358,38 s. Es tiempo del proceso actual completo de ensamblado, no una promesa de ahorro equivalente.

## Prioridades para la siguiente implementación

| Orden | Cambio | Evidencia | Validación necesaria |
|---|---|---|---|
| 1 | Decodificar por hardware la 360 del montaje | `core/spherical_motion.py:run_reprojected_command` todavía decodifica por CPU; el encoder final ya utiliza hardware cuando está disponible. | Conservar formato/rango de color original, comprobar segmentos enteros con movimiento y poner respaldo ante fallo de decoder sin ocultar cancelaciones. |
| 2 | Evitar el vídeo intermedio entre concat y mux | `core/stages/export.py` escribe joined-video y lo vuelve a leer/escribir al añadir audio. La unión ya usa copia, no una segunda codificación de vídeo. | Ensamblar desde lista concat con audio directamente, comprobar duración/cadencia, mantener publicación atómica y reparación cuando la cadencia no es válida. También estudiar el recorrido 360 navegable. |
| 3 | Acelerar Medley con hardware y concurrencia acotada | `core/medley.py` usa libx264 veryfast, dos hilos y extractos secuenciales; análisis musical y visual también son secuenciales por canción. | Encoder compatible en todos los extractos/negros, fundidos/audio, orden original, cancelación, disco y RAM; medir uno frente a dos workers. No cambiar automáticamente calidad por velocidad. |
| 4 | Compartir lectura del proxy entre encuadre y operador | `core/stages/ingest.py:prepare_videos` ejecuta framing y después operator; ambos abren VideoCapture y recorren el vídeo. En frío, framing no encuentra el análisis de operador que todavía no se ha creado. | Mantener los tiempos de muestreo y ambos resultados. Sus frecuencias y decisiones difieren: compartir lectura no equivale a eliminar un detector. Medir sobre el mismo proxy. |
| 5 | Reunir las dos muestras de comprobación de movimiento | `_verify_moving_frames` llama dos veces a `_frame_md5`, cada una inicia FFmpeg y abre el segmento. | Un proceso con las dos muestras exactas y hashes independientes; conservar detección de congelados y fallos de lectura. La suma de verify_sec de workers no es ahorro de pared. |
| 6 | Optimizar movimiento del móvil 4K | Decode y filtros/zoompan siguen siendo caros aunque la salida sea 1080p. | Comparar recorte previo o pipeline GPU manteniendo encuadre, detalle, luz y trayectoria. Escalar temprano puede reducir detalle de primeros planos; no habilitarlo sin prueba de calidad. |

Las pruebas ya disponibles de la primera prioridad: seis puntos del original y cuatro rondas de reproyección de 60 fotogramas coinciden con CPU cuando el decoder hardware pasa primero por el formato yuvj420p del original probado. Sin esa conversión, hubo diferencias. Este resultado NO autoriza forzar yuvj420p en otras fuentes. Las cuatro rondas incluyeron compilación concurrente y no codificación H264; tiempos CPU 13,289/16,024 s, hardware 9,806/13,071 s. No se extrapola un porcentaje al render entero. Una proyección completa por GPU es un cambio mayor posterior, no una mejora aplicada.

## Resto de la aplicación

- **Arranque y proyectos:** mantener imports pesados diferidos y tarjetas sin tamaños recursivos. Mejorar sondeo/listado solo si una traza demuestra coste; prioridad inferior a los segmentos. El sondeo ya evita solapamientos de solicitudes idénticas.
- **Importación:** clasificación tiene caché en memoria por stat. Ingest vuelve a llamar ffprobe; una caché común de metadatos por firma podría evitar algunos probes. Ahorro previsiblemente menor que la lectura de vídeo. Mantener revalidación si cambia el archivo. No copiar fuentes externas innecesariamente; los uploads desde el navegador siguen transfiriendo bytes.
- **Proxies:** ya intentan VideoToolbox. Dos workers y cola larga primero en el código preparado. Probar más concurrencia con RAM/decoder/disco medidos; no asumir que subir workers acelera proporcionalmente este Mac. Framing y operador ya se solapan con proxies que aún están en marcha.
- **Sync:** envolventes persistentes y respaldo espectral/HPSS para correlaciones débiles. Varias funciones cargan audio del mismo path por separado. Compartir PCM acotado o audio remuestreado por firma puede ahorrar decode, conservando tasas y señal de cada análisis. HPSS tiene coste propio; no desactivar la recuperación de sincronización para mejorar un cronómetro.
- **Selección musical:** Reel puede leer el audio para elegir la ventana y Edit volver a leer la ventana para beats. Comparten materia prima, no el resultado: estudiar caché PCM/rasgos por ventana. YouTube no llama Whisper. Demucs de seis instrumentos es opcional y caro; medirlo por separado y reutilizar sus resultados cuando no cambian audio/ventana/modelo.
- **Review:** 2.5.8 preparada evita convertir toda la 360 para fotos y reutiliza cachés; 2.5.9 prepara fotos mientras se deciden cortes. Antes de decidir instante y pose no existen fotos definitivas que adelantar. Crear cientos de alternativas especulativas puede añadir trabajo. No contabilizar ese coste eliminado como mejora del encoder.
- **360 navegable:** ya hace stream-copy del cuerpo y no necesita reproyección/selección multicámara. Su oportunidad es I/O, ensamblado y evitar reparaciones innecesarias, no cambiar x264 de un cuerpo que normalmente no se recodifica. INSV sin coser requiere procesamiento real.
- **Backstage:** transcripciones cacheadas por fuente/modelo/opciones. Un modelo se reutiliza entre fuentes dentro del trabajo. Evaluar reutilización entre trabajos con límite de memoria; dejar un modelo pesado residente en un Mac de 8 GB puede empeorar el render. No eliminar transcripción de un modo que la necesita.
- **Medley:** conservar highlights musicales/visuales y varios extractos por canción. Sus cachés están ligadas a índices locales: estudiar almacenamiento común por firma de fuente/ventana y reutilizar al reordenar canciones o abrir otro proyecto.
- **Overlays y subtítulos:** composición copia una base limpia y después puede recodificar el vídeo. Mantener una base inmutable y evitar copia física cuando se puede demostrar que nadie la modifica. Unificar pasadas compatibles de flyer/logo/texto/captions evita recodificar varias veces; conservar la base para que guardar de nuevo no acumule overlays. No sustituir por enlaces duros sin controlar quién puede sobrescribir el archivo.
- **Cachés:** no vaciarlas como optimización de velocidad. Segmentos existentes siguen comprobados por receta y hashes; los hashes repetidos de un segmento recién escrito ya se redujeron. No reintroducir una caché insegura por tamaño/mtime de precisión insuficiente.

## Qué medir antes de afirmar una mejora

Mismo plan congelado, fuentes, audio, calidad y parámetros; aplicación sin otra operación pesada. Una ronda fría y otra caliente, orden invertido de variantes. Registrar pared por fase, render y verificación por segmento, fallbacks, bytes escritos, CPU y presión de memoria. Comparar frames, duración, cortes, audio y color; verificar MP4 completo publicable. No prometer 50 %, rendimiento proporcional al número de workers ni un máximo físico.

Orden recomendado: decoder 360 con fidelidad demostrada; concat+audio directo; Medley hardware/concurrencia; lectura compartida de análisis; verificación de dos frames en una sesión. Los primeros dos atacan el proyecto lento observado; Medley beneficia específicamente a ese modo. La mejora de las fotos ya preparada es independiente.
