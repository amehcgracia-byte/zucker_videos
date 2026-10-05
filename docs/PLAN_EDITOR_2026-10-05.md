# Zucker Editor: próximos bloques de edición

Petición del 5 de octubre de 2026. Los bloques se implementan y comprueban por separado. Las funcionalidades futuras que aparecen aquí todavía no están implementadas.

## 1. New Project — corregido en código

Botón legible en temas claro y oscuro, nombre New Jam y cuadro vacío. Restablecer también los vídeos apartados, las filas de importación y la selección de audio. Abortar transferencias anteriores e ignorar las respuestas tardías para que un archivo antiguo no reaparezca en el proyecto nuevo. Verificar la cancelación de un trabajo activo antes de cambiar de proyecto.

## 2. Highlights musicales — análisis y decisiones

La edición actual ya usa pulsos, compases, energía y cambios de sección. La aproximación existente a voz usa energía armónica: puede confundir instrumentos con cantante. No se debe presentar como identificación fiable de solos.

Primera capa ligera: cambios de sección, ataques, pausas, subidas y bajadas de intensidad; guardar intervalos y razones en un artefacto por proyecto, usando el audio ya decodificado. Relacionar movimientos y cortes con esos eventos y con las cámaras disponibles.

Segunda capa: pistas separadas de voz/instrumentos, obtenidas de stems aportados por el usuario o de separación local opcional. Demucs separa voz, batería, bajo y acompañamiento; su modelo de seis fuentes añade guitarra y piano. Separar instrumentos no identifica automáticamente un solo: añadir prominencia relativa, duración de frase, actividad vocal y confianza. Mantener el audio máster intacto y reutilizar el análisis en caché externa.

Con confianza suficiente: entrada vocal → cantante, guitarra prominente sin voz → guitarrista, piano prominente → pianista. Con confianza baja: cobertura equilibrada, sin inventar un músico dominante. La vista del cantante confirmada es la persona de pelo claro con guitarra. Ofrecer correcciones manuales de intervalos y mostrar el motivo de la toma.

Fuentes técnicas: https://librosa.org/doc/main/auto_tutorials/01-intro/05-onsets.html y https://github.com/facebookresearch/demucs/blob/main/README.md

## 3. Biblioteca Random Frames — tomas de recurso

Guardar fragmentos de vídeo reutilizables, no solamente fotogramas estáticos. Carpeta del disco seleccionado: Random Frames/<sesión>/index.json y miniaturas. Cada entrada referencia archivo original, intervalo, tipo (público, fuego, ambiente, detalles), calidad, origen y sesión. Evitar duplicar los vídeos completos.

Primera versión: selección manual de intervalos y etiquetas, vista previa y comprobación de fuentes disponibles. Indexación automática posterior: muestreo limitado, foco/estabilidad, geometría de escenario y clasificación visual. Una detección de personas sola no distingue escenario de público; fuego requiere un detector o revisión específica.

Elegir recursos del mismo evento/lugar. No reutilizar silenciosamente material de otra sesión ni de Internet. Evitar repetición y limitar porcentaje de recursos; el vídeo principal continúa siendo la mayor parte del montaje. El audio máster sigue continuo y el recurso no aporta audio de cámara.

## 4. Una Sony + un máster — montaje de rescate

La entrada de una cámara con máster ya admite YouTube. Añadir búsqueda de intervalos desenfocados, movimientos bruscos y encuadres sin sujeto útil; conservar los intervalos buenos, añadir movimientos moderados y cubrir defectos con la biblioteca de la sesión. Si faltan recursos, mostrarlo y permitir resolverlo en revisión; no rellenar con imágenes incompatibles. Guardar la procedencia y la razón de cada sustitución en edit_plan.json.

Prueba: un vídeo con tramos buenos y malos conocidos, más dos recursos; verificar continuidad del máster, duración, límites de cada recurso, mayoría de cámara principal y ausencia de repeticiones consecutivas.

## 5. INSV — auditar y procesar solo lo necesario

Ya hay clasificación raw_insv, detección de parejas de lentes y conversión FFmpeg. La normalización actual incluye una salida plana después de la conversión; eso debe separarse de la fuente esférica para conservar un MP4 360 editable.

Auditar archivos reales y sus variantes: una lente/dos archivos, metadatos de orientación, proyección y estabilización. Crear un proxy pequeño para analizar; después convertir los intervalos utilizados, con margen para cortes y movimientos, y caché por fuente/intervalo/calibración. Ofrecer como alternativa convertir todo el archivo a MP4 equirectangular. Comprobar costuras y orientación con la escena real; una conversión geométrica de FFmpeg no garantiza la estabilización de Insta360 Studio.

## 6. Medley — varios temas y sus highlights

Añadir Medley como modo una vez disponible el análisis musical. Seleccionar canciones y vídeos, elegir aproximadamente dos o tres minutos por tema, preservar frases completas y permitir editar intervalos. Diez temas de dos/tres minutos producen unos veinte/treinta minutos, más separaciones.

Entre canciones: fundido de imagen a negro, breve intervalo negro configurable y fundido de audio. Mostrar nombre, fuente y rango de cada canción; elegir highlights musicales y visuales por canción. Normalizar niveles con un objetivo común, respetar el máster correspondiente y guardar el montaje para reabrirlo. Render a archivo temporal, comprobación audiovisual y publicación atómica del archivo final.

## Validación y entregas

Un commit por bloque comprobado; no añadir botones de funcionalidades que todavía no funcionan. Primera entrega: New Project. Siguientes dependencias: highlights y biblioteca → rescate de una cámara y Medley. INSV se puede trabajar de forma independiente con los originales. Mantener los fixes de movimiento, encuadres, reparto de músicos y render atómico.
