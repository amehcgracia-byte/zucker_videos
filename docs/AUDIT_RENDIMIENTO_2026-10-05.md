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

## Segunda investigación: trabajo duplicado confirmado (19:38–19:52)

El exportador preparaba un proxy esférico de **todo el archivo** antes de decidir qué fuente usaría cada segmento. El renderizador nativo de tomas con movimiento, por calidad de primeros planos, utiliza el original. Ambas decisiones estaban separadas: se convertía un archivo completo que después no se utilizaba.

Comprobación con los planes y los metadatos reales de los proyectos:

| Proyecto | Tomas esféricas | Tomas que leen el original | Proxy previo necesario |
|---|---:|---:|---|
| Kongroove | 101 | 101 | No |
| If u want to stay | 65 | 65 | No |

En el render activo de «If u want to stay», el registro muestra **19:38:00 → 19:52:26: 14 minutos y 26 segundos** preparando ese proxy innecesario. Este tiempo pertenece a una fase concreta; no es una estimación del ahorro total ni un benchmark de dos exportaciones completas. El render activo conserva el código instalado y no se ha interrumpido.

Correcciones:

- Preparación y render comparten exactamente la misma decisión sobre leer el original. Solo se preparan fuentes que algún segmento realmente utiliza como proxy. Se mantienen la resolución del original, los primeros planos y los movimientos.
- Las nuevas cachés esféricas de revisión y exportación se guardan en el disco de datos, compartidas entre proyectos y con clave por fuente, tamaño, fecha y parámetros. Cambiar una fuente invalida la caché. Las cachés antiguas terminadas del proyecto se reutilizan en su ubicación, sin mover archivos de un render activo. Revisión y exportación conservan distintas resoluciones: son productos diferentes, no se sustituye la calidad de exportación por miniaturas.
- El render nativo reutiliza un único búfer de lectura por segmento y escribe mediante vistas de memoria. Se eliminan copias completas de los fotogramas originales y de salida. Las poses constantes reutilizan sus mapas de proyección; una pose distinta vuelve a calcularlos.
- El manifiesto registra ahora `source_prepare_sec` y `source_proxy_count`, antes ocultos entre el tiempo total y la suma de fases.

Otros costes observados: en Kongroove los segmentos consumieron 2915,475 segundos de pared y la exportación completa 4023,820 segundos. La unión fue 99,793 segundos y el multiplexado 170,429 segundos. La unión YouTube intenta copiar el vídeo; la normalización de cadencia solo vuelve a codificar cuando la comprobación falla. No se ha eliminado esa protección contra vídeos corruptos. El passthrough 360 tiene un caso previo con doble codificación (recorte y unión); queda pendiente una corrección independiente con pruebas de compatibilidad de cabeceras y metadatos esféricos.

Validación de reproyección: se ejecutaron la implementación anterior y la nueva sobre una esfera sintética de 30 fotogramas, para `hold` y `push_in`. Los hashes FFmpeg `framemd5` de todos los fotogramas decodificados fueron idénticos. La comparación se hizo mientras el usuario renderizaba: los tiempos oscilaron (hold 7,255 → 13,885 s; push_in 27,836 → 17,400 s), por lo que **no permiten afirmar una mejora de velocidad estable**. El ahorro confirmado es eliminar la conversión completa innecesaria; la reducción de copias preserva los píxeles y necesita un benchmark aislado para cuantificar su impacto.

Pruebas del cambio: **17 pruebas específicas superadas** (decisión de fuente, reutilización entre proyectos, invalidación, movimiento, atestación y publicación atómica). La batería amplia de exportación se detuvo para no seguir compitiendo con el render activo: 57 pruebas habían pasado y 10 habían fallado. Las 10 se reprodujeron contra `058beaf`, la versión anterior a este cambio: son fallos previos, no una batería completa aprobada. Evidencia local: `work/performance-250/baseline-failures.log` y `native-comparison.json`.
