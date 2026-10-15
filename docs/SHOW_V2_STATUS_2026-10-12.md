# Show v2: estado de la integración (12 de octubre)

Continúa `docs/SHOW_HANDOFF_2026-10-12.md` (rama `codex/crossplatform-ui-audit`, hasta
`d6aaa4b7`). Aquello era un prototipo de navegador; esto lo lleva al render real y a una
revisión plano a plano. No sustituye ni modifica los documentos de la auditoría.

## En el render real (con tests)
- **Reencuadre de todo el plano** (`core/show_framing.py`, `core/show_render.py`): planos
  abierto, medio y primer plano (1.0 / 1.3 / 1.65) centrados en la cara del presentador,
  con aire sobre la cabeza; acercamientos lentos y aperturas ("reveal") en planos largos.
  Cara: OpenCV Haar sobre fotogramas clave con CLAHE (6 % → 73 % de detección en C0271),
  filtrando caras de fotos de la pared. Nunca se agranda un recorte flotante.
- **Cortes a recursos** (`cutaway_filters`): la imagen pasa a otro vídeo mientras el audio
  sigue siendo el del presentador.
- **Personaje que habla** (`core/show_inserts.py`): fotograma congelado del recurso (o foto),
  acercamiento de 1,2 s, encuadre quieto mientras suena su propia voz; la narración principal
  se pausa y continúa después; los subtítulos posteriores se desplazan. La región
  (rectángulo o polígono reducido a su contorno convexo) se mueve con sus píxeles originales
  y deja negro detrás: boca (volumen RMS, umbral 0,008 × 14 como el prototipo), cabeceo,
  lateral o giro. No es sincronía labial. Fotograma negro rechazado salvo permiso explícito.
- **Boca sobre vídeo en movimiento** (`gag_render.move_mouth`): sigue pequeños movimientos de
  cámara por correlación de plantilla; si pierde el objeto, deja de moverse.
- **Humo arcoíris**: receta de partículas del prototipo; colocado sobre la imagen original y
  re-situado plano a plano según el zoom; puede anclarse a la taza (seguimiento por plantilla).
- **Fórmulas alrededor del presentador**: orbitan su cara (posición real en pantalla).
- **Reacciones en pausas**: corte breve a un plano con cara (p. ej. C0277 escuchando) en la
  pausa tras una pregunta o exclamación.
- **Cancelación**: el render del Show se detiene al momento (antes seguía hasta el final).
- Regiones guardadas en coordenadas de su propia imagen: cambiar zoom/foco no las mueve; si el
  zoom las deja fuera de cuadro, la propuesta pide "reajustar".

## Revisión plano a plano
`core/show_proposals.py`, `core/show.py` (`prepare_show`, `preview_proposal`,
`render_reviewed_show`), rutas `/api/v1/show/review*`, `web/show_review.js` (paso 4).
Cada efecto es una propuesta con motivo y confianza: previsualización (render real corto),
ajuste, aprobar o descartar; añadir humo, personaje, corte, fórmulas o palabra. Nada se
renderiza hasta "Render approved". Selección: rectángulo por defecto, puntos avanzados con
cierre (primer punto, Enter o botón), deshacer/rehacer (Cmd/Ctrl+Z), copiar/cortar/pegar.

## Recursos de Channel Prueba (no subidos a ningún sitio)
C0272: primer plano de la nevera (0–22 s) y figurita amarilla de la planta (78–112 s).
C0273: plano de cocina vacía (5–11 s) y figurita metálica (49–55, 61–75 s); voz "Hello, good morning".
C0274: fotomatón, billete de 20 coronas, japonés, criatura naranja (205–244 s, muy estable),
mapache (267–292 s, con la voz "hey bro, I'm also here…"), nevera general.
C0277: el presentador escuchando (48–76 s), útil como reacción.
El emparejamiento automático por CLIP solo propone candidatos (márgenes pequeños); decide la
revisión. Con Claude conectado (OAuth vía Claude Code) podrá proponer con más criterio.

## Dirección de dibujo
Solo lápiz, ceras y rotulador, imperfectos; sin borde de pegatina. "Sobre papel blanco"
hacía pintar el papel, la mesa y los lápices: ahora "aislado sobre fondo blanco" con
exclusiones explícitas (guidance 1.8, 5 pasos). Validación visual de la versión 3 en curso.

## Pendiente de validar antes de darlo por terminado
- Recorrido completo con el material real: preparar → revisar → render → componer/exportar.
- Empaquetado con diffusers/transformers (pines en `requirements.txt`) en macOS y Windows.
- Interfaz de revisión probada en el navegador de la app.
- Seguimiento de boca para personajes que se mueven mucho; voz opcional grabada por el usuario.

## Validación por etapas (proyecto de prueba con el material real, fuera del repositorio)
- Transcripción: Whisper sin detector de voz elegía el idioma en el silencio inicial
  (noruego para una charla en inglés) y se saltaba tramos: 34 palabras frente a 497 en cinco
  minutos. Con detector de voz: idioma correcto, texto completo y 5× más rápido. Corregido;
  el corte de C0271 vuelve a 6:22 (antes 2:56 por el fallo).
- Preparación completa: 6 min 07 s (transcripción 4 min, encuadre de cara 2 min; el análisis de
  recursos queda en caché). 15 escenas.
- Propuestas: con el umbral inicial salían 86 (demasiadas, muchas al azar). Subido el listón de
  los cortes (0,015, mín. 12 s entre ellos), los planos del presentador reservados para
  reacciones y los dibujos del director espaciados a 10 s.
- Storyboard en el navegador de la app: identidad (título con letra manuscrita, logo del
  proyecto), intro, escenas con su vídeo de previsualización real preparado en segundo plano,
  decisiones guardadas en el proyecto.
- Etapas 1–3 configuradas por la API real: título + logo, intro de 4 s, escena 1 aceptada
  (cortes a la figurita metálica, la criatura y la figurita amarilla con el audio del
  presentador), escena 2 con la figurita amarilla recortada por macOS hablando con su voz de
  C0274, escena 3 con humo arcoíris anclado a la taza.
- Render final revisado (etapa 4): 12 min 15 s en este Mac Intel; 6:31,6 de vídeo
  (tarjeta 2 s + intro 4 s + 6:21,8 de montaje + 3,8 s del personaje hablando), 1920×1080 a
  30 fps; vídeo y audio terminan a la vez (391,57 / 391,59 s). Comprobado por fotogramas:
  tarjeta con logo y título, intro, cortes a recursos con el audio del presentador, la
  figurita amarilla hablando sobre el plano congelado con la voz de C0274 y la narración
  reanudada después.
- Fallo encontrado y corregido: el humo arcoíris se dibujaba pero no se veía (la receta del
  prototipo, 6,5 % de opacidad en tonos pastel, desaparece sobre una taza clara a contraluz).
  Ahora 30 % y colores más saturados; solo aparece en los planos donde la taza está en cuadro.
- Fallo encontrado y corregido: si los tres intentos de dibujo salían con fondo sucio se usaba
  el último. Además el pulgar pasaba el control (bordes blancos) siendo un 75 % relleno negro.
  Ahora un dibujo con fondo sucio o con más del 45 % de su recuadro en negro se descarta y se
  avisa (los garabatos de trazo quedan por debajo del 39 %).
- Fallo encontrado y corregido: la figurita cortaba al presentador a mitad de "know?". Las
  intervenciones esperan ahora a la pausa más cercana (hasta 1 s) o al final de la palabra.
- Humo: en los primeros planos que dejaban solo el borde de la taza abajo, flotaba sobre la
  maceta. Ahora solo aparece si se ve al menos el 60 % de la taza.
- Render de verificación (11 min): "know?" entero, la figurita habla de 36,2 a 40,0 s, el
  presentador sigue en 40,06 s; humo visible sobre la taza en los planos abiertos.
- Dirección de dibujo en uso real: todavía no cumple. DreamShaper 8 tira a ilustración pulida
  (guitarra de grabado, bombilla tipo logotipo, aerógrafo y brillos) incluso pidiendo "dibujo
  de niño con ceras"; probadas seis variantes de instrucciones. Cada dibujo tarda ~70 s aquí.

## Mejoras de la maqueta de Codex integradas por etapas (12 de octubre, noche)
Cada etapa con tests, probada en la previsualización de la app (navegador) y en un render real.
1. **Entradas del personaje** (`show_inserts.ENTRANCES`): corte directo (habla al instante),
   acercamiento (lo de antes) o foto con salto (tarjeta al 78 % con esquinas redondeadas que sube
   con un rebote sobre el plano congelado y cae al terminar su voz). Un personaje recortado sobre
   el plano congelado entra igual: al instante, subiendo desde abajo, o la cámara se acerca y él
   con ella (se dibuja antes del acercamiento). Selector "Enters" en el editor y en el menú del
   elemento. Comprobado: previsualización de la escena 2 y render completo (391,30 s; la
   figurita sube, aterriza y habla).
2. **Biblioteca de fotos**: "Photos of every steady moment" saca una foto cada 4 s (y al menos
   una) de cada tramo quieto y nítido que encontró el análisis de recursos, solo de los vídeos
   de recursos, a tamaño completo y una sola vez. En el material real: 95 fotos de C0272, C0273,
   C0274 y C0277, agrupadas por vídeo en el estudio de personajes; recorte comprobado.
3. **Avance y progreso**: barra "N de 15 escenas decididas", ✓ en las escenas decididas,
   "Accept and next →", "Go to the first scene to decide" y "Watch the whole show" (tarjeta,
   intro y previsualización de cada escena seguidas). Comprobado en la app con las 15 escenas.
4. **Prompts editables**: cada dibujo muestra el texto exacto que recibirá el pintor, editable
   antes de pintarse; nada se pinta hasta "Preview" o el render; cada texto es un dibujo nuevo
   de la biblioteca y el anterior se conserva. Se pueden añadir dibujos a una escena. Comprobado:
   la bombilla con texto propio se pintó distinta y la anterior sigue en la biblioteca.

Fallos que ya existían, encontrados al probar y corregidos: el menú del elemento se cerraba
nada más abrirse con un clic (solo funcionaba con el botón derecho); el menú se salía de la
pantalla; la cola de previsualizaciones admitía duplicados y no daba prioridad a la escena que
se pide; el estudio fallaba al cargar la foto (el evento de carga se tomaba por un trazo).
`work/` (grabaciones y muestras privadas, 5,2 GB) queda ignorado por git.

## Dibujos con ChatGPT a través de Codex: probado, no integrado
`codex exec` (CLI 0.160, sesión de ChatGPT, sin clave de API) generó guitarra, pulgar y bombilla
con fondo transparente, unos 60 s cada uno, en `~/.codex/generated_images/<sesión>/`. Hay que
cerrar su entrada estándar. Sin comprobar todavía: si las condiciones del plan permiten
automatizarlo desde Zucker y cuánto cupo gasta. No es el generador predeterminado hasta que el
usuario lo decida.

## Pedido después, pendiente
- Ambiente de sala bajo las intervenciones y fundidos en los empalmes (el corte a silencio se nota).
- Música: carpeta "Music" en la biblioteca, pistas sin derechos (p. ej. Kevin MacLeod, CC BY con
  atribución), propuestas por momento y bajadas bajo la voz. Descargas solo con permiso.
- Censura: "pip" sobre palabrotas (es/en) desde la transcripción y, opcional, algo sobre la boca.
