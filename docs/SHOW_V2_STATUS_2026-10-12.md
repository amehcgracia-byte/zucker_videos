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
