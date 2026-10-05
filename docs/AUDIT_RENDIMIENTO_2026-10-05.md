# Auditoría de carga y render — 5 de octubre de 2026

Datos de `elapsed_seconds` guardados por la aplicación, en segundos. Son ejecuciones distintas, con fuentes y duraciones distintas; no son un benchmark antes/después.

| Proyecto | Ingestión | Sincronización | Cut | Edit | Exportación |
|---|---:|---:|---:|---:|---:|
| So much to me | 47,347 | 79,571 | 0,071 | 101,798 | 4215,235 |
| Kongroove | 1338,360 | 89,719 | 0,151 | 443,613 | 4110,452 |
| Moonlight Cable | 2811,357 | 153,142 | 0,459 | 695,105 | 4138,038 |
| Wasib Funk | 549,243 | 40,641 | 0,077 | 197,183 | En curso durante la auditoría |

La exportación es el coste dominante en las ejecuciones terminadas. La ingestión varía considerablemente: hay que distinguir comprobar metadatos, normalizar fuentes y analizar imagen. No se puede atribuir toda esa variación a una regresión sin igualar fuentes, duración y caché.

## Recorridos por modo

| Modo | Ingestión / proxies | Sincronización | Análisis | Whisper |
|---|---|---|---|---|
| YouTube | Validación, normalización cuando es necesaria y análisis de imagen | Si hay máster externo | Ritmo, cámaras, calidad y movimientos | No en el render normal; transcripción manual ahora rechazada |
| Reel | Validación y encuadre; reutiliza cachés | No sincronización multicámara | Extracto promocional, ritmo y encuadre | Solo al transcribir subtítulos |
| 360 | Passthrough del MP4 esférico; INSV necesita cosido | Solo con máster externo | Plan de clip esférico, sin dirección multicámara | No |
| Backstage | Originales, sin proxies de edición multicámara | No | Análisis documental y edición | Sí, cuando corresponde al análisis de voz |
| Medley Populi | Metadatos y audio; no proxies completos | No: cada vídeo usa su audio o su audio externo asignado | Cambios musicales por canción, extractos y fundidos | No |

## Correcciones de esta revisión

- El modo elegido se escribe antes de la ingestión al rehacer un proyecto. Antes la ingestión podía aplicar las condiciones del modo anterior.
- Los metadatos FFprobe exitosos se reutilizan en memoria, hasta 256 archivos. Cambiar ruta resuelta, identidad, tamaño, fecha de modificación o fecha de cambio invalida la entrada. Cada consumidor recibe su copia; los fallos no se guardan.
- Los nueve visores 360 no solicitan fotogramas ni animan mientras el panel está cerrado. Se abren bajo demanda.
- La energía musical se calcula con operaciones vectorizadas. Se elimina la segunda decodificación y el HPSS usado para atribuir voz a energía armónica: esa atribución podía confundir instrumentos con cantantes.
- El análisis rápido de cambios musicales se obtiene de las muestras ya decodificadas para el ritmo. El análisis instrumental detallado es opcional y tiene caché independiente por fuente y intervalo. No utiliza Whisper.
- Medley normaliza únicamente los extractos elegidos. La unión final copia las pistas ya compatibles, evitando volver a codificar el vídeo entero.
- Las barras pequeñas usan anchura explícita según el porcentaje recibido, con etiquetas accesibles; una tarea sin medida aparece como pendiente, sin inventar porcentajes.

## Límites y siguientes mediciones

Las cachés de proxies, presencia del operador, calidad y segmentos ya existían; esta revisión las conserva. Los movimientos esféricos siguen necesitando reproyección y render: no equivale a copiar un vídeo. No se aumenta automáticamente el número de trabajadores, porque puede empeorar el rendimiento en este ordenador y competir con la memoria y el decodificador.

La detección detallada utiliza separación de seis fuentes de Demucs. Voz e instrumentos son estimaciones. La detección de solos combina prominencia y cambios frente al patrón local; no debe presentarse como una transcripción musical infalible. El piano del modelo experimental tiene separación menos fiable. Los modelos se almacenan en el disco de datos seleccionado, no en Descargas; no se conservan seis WAV completos por canción.

Falta comparar una misma canción y las mismas fuentes, con cachés frías y calientes, y desglosar el tiempo de reproyección 360, codificación de segmentos y ensamblado. No se promete un porcentaje global de ahorro sin esa comparación.
