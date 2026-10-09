/* Read-only guided tour: no project mutation, media loading or pipeline actions. */
(() => {
  const steps = [
    ['Tu próximo montaje', '#newProject', 'Nuevo proyecto empieza con los archivos vacíos y el nombre New Jam. Tus proyectos anteriores siguen disponibles en el historial.'],
    ['Una edición para cada idea', '#editTypeDialog .platforms', 'YouTube monta una canción; Reel prepara un clip corto; 360 conserva la vista navegable; Medley combina canciones; Backstage cuenta lo que ocurre entre bastidores.'],
    ['Ponle nombre', '#videoName', 'Escribe el título de la canción. Se usará para identificar el proyecto y el vídeo que estás renderizando.'],
    ['Arrastra tus archivos', '#dropZone', 'Añade vídeos, audio y, si quieres, tu logotipo. Cada archivo muestra su importación. Medley puede usar el audio integrado de los vídeos; los requisitos cambian según el modo.'],
    ['Guarda antes de seguir', '#confirmFiles', 'Al continuar eliges la ubicación del proyecto, por ejemplo en tu disco externo. Allí se guardan los archivos de trabajo y podrás volver a abrirlo desde el historial.'],
    ['El fragmento exacto', '.trim-box', 'Escucha el audio y fija inicio y final. Revisa estos tiempos si cambias de pista: pertenecen al audio que has elegido ahora.'],
    ['Preparación en segundo plano', '#settingsPreparation', 'Mientras ajustas los parámetros, el programa puede preparar los vídeos. El mensaje y las barras te muestran el trabajo ya realizado.'],
    ['Momentos musicales', '#instrumentHighlightsOption', 'Activa este análisis para buscar actividad vocal y posibles solos instrumentales. Añade tiempo de análisis y reutiliza resultados guardados; revisa después las tomas propuestas.'],
    ['Reparte las cámaras', '#cameraMix', 'Los pesos orientan cuánto aparece cada cámara. No necesitan sumar 100. La cámara fija también puede incorporar un movimiento de zoom suave.'],
    ['Enseña dónde está cada músico', '[data-spherical-landmark="singer"]', 'En 360, encuadra al cantante y asigna el sujeto correcto. Guarda el ángulo. “Use automatically” permite incluir o excluir esa vista del montaje.'],
    ['Fine tune: afina el encuadre', '[data-spherical-landmark="singer"] details', 'En Advanced angles puedes afinar yaw (giro), pitch (altura), FOV (amplitud) y roll (inclinación). La proyección cambia la curvatura: revisa los rostros y los bordes antes de guardar.'],
    ['Un Reel a tu medida', '#reelOptions', 'Ajusta duración, formato vertical u horizontal y densidad de cortes. Con una sola fuente, el recorrido disponible puede ser una toma continua.'],
    ['Historias entre canciones', '#backstageOptions', 'En Backstage eliges la duración y puedes añadir mensajes. Después revisas la propuesta de montaje antes de renderizar.'],
    ['Medley Populi', '#medleyOptions', 'Combina vídeos de canciones diferentes y elige su audio. Ajusta duración total, negro entre canciones y fundidos para que cada cambio musical se entienda.'],
    ['Pon el montaje en marcha', '#startWizard', 'Make it inicia el proceso con los parámetros elegidos. Las fases de preparación, edición y exportación tienen su propio progreso.'],
    ['Sigue el trabajo real', '#progressDetails', 'Abre los detalles para ver qué está haciendo el programa. Puedes copiar el informe si algo falla. Cancelar detiene el trabajo; no necesitas cerrar la aplicación.'],
    ['Revisa antes de renderizar', '#renderReviewedTop', 'En Frames revisas las tomas propuestas, ajustas las que lo necesiten y reemplazas las rechazadas. Render Frames está también arriba para continuar sin bajar al final.'],
    ['Texto que acompaña al vídeo', '.compose-captions-panel', 'En los modos con editor de composición puedes transcribir voz, importar SRT o LRC, corregir texto y tiempos, exportar SRT e incrustar los subtítulos.'],
    ['Tu identidad visual', '.compose-overlays-panel', 'Añade imágenes, vídeos y logotipo. Ajusta su posición y duración en la línea de tiempo. También puedes reutilizar superposiciones anteriores.'],
    ['El resultado y las herramientas', '.wizard-nav', 'Revisa el vídeo y abre su ubicación desde Resultado. El menú superior reúne las acciones del programa. En Herramientas puedes buscar actualizaciones; en Ayuda puedes repetir este tutorial.']
  ];
  let dialog, index = -1, previousFocus, pending = false, spotlight = null;
  const el = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; if (text) n.textContent = text; return n; };
  function build() {
    if (dialog) return;
    dialog = el('dialog', 'einstein-tour');
    dialog.setAttribute('aria-labelledby', 'tourTitle');
    dialog.innerHTML = `<svg class="tour-shade" aria-hidden="true"><defs><mask id="tourMask"><rect width="100%" height="100%" fill="white"/><ellipse id="tourHole" fill="black"/></mask></defs><rect width="100%" height="100%" fill="rgba(0,0,0,.82)" mask="url(#tourMask)"/><ellipse id="tourRing" fill="none" stroke="#f7d776" stroke-width="3"/></svg><div class="tour-demo" aria-hidden="true" inert></div><div class="tour-guide"><img src="/tutorial-einstein.png" alt="Einstein te acompaña en el tutorial"/><section class="tour-bubble"><span class="tour-count"></span><h2 id="tourTitle"></h2><p class="tour-copy"></p><p class="tour-error" role="status"></p><div class="tour-actions"></div></section></div><button class="tour-close" aria-label="Cerrar tutorial">×</button>`;
    document.body.append(dialog);
    dialog.querySelector('.tour-close').onclick = dismiss;
    dialog.addEventListener('cancel', e => { e.preventDefault(); dismiss(); });
    // Keep tutorial controls away from application-wide keyboard/click handlers.
    for (const event of ['click', 'keydown', 'keyup', 'input', 'change']) dialog.addEventListener(event, e => e.stopPropagation());
    window.addEventListener('resize', position);
  }
  function button(label, action, primary = false) {
    const b = el('button', primary ? 'tour-primary' : '', label); b.type = 'button'; b.onclick = action;
    dialog.querySelector('.tour-actions').append(b); return b;
  }
  function open() {
    build(); previousFocus = document.activeElement;
    if (!dialog.open) dialog.showModal();
  }
  function close() { dialog.close(); window.dispatchEvent(new Event('tutorialclosed')); dialog.querySelector('.tour-demo').replaceChildren(); previousFocus?.focus?.(); }
  async function answer(value) {
    if (pending) return false;
    pending = true;
    dialog.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      const r = await fetch('/api/v1/tutorial', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({answer: value})});
      if (!r.ok) throw new Error('No se pudo guardar tu respuesta. Vuelve a intentarlo.');
      return true;
    } catch (error) { dialog.querySelector('.tour-error').textContent = error.message; return false; }
    finally { pending = false; dialog.querySelectorAll('button').forEach(b => b.disabled = false); }
  }
  async function dismiss() { if (pending) return; if (index === -1 && !await answer('no')) return; close(); }
  function position() {
    if (!dialog?.open) return;
    const preview = dialog.querySelector('.tour-demo');
    const r = (spotlight || preview).getBoundingClientRect();
    const visible = index >= 0;
    for (const id of ['tourHole', 'tourRing']) {
      const ring = dialog.querySelector('#' + id);
      ring.setAttribute('cx', r.x + r.width / 2); ring.setAttribute('cy', r.y + r.height / 2);
      ring.setAttribute('rx', visible ? r.width / 2 + 16 : 0); ring.setAttribute('ry', visible ? r.height / 2 + 16 : 0);
    }
  }
  function show() {
    const [title, selector, copy] = steps[index];
    dialog.classList.remove('tour-welcome');
    dialog.querySelector('#tourTitle').textContent = title;
    dialog.querySelector('.tour-count').textContent = `${index + 1} / ${steps.length} · ZUCKER EDITOR`;
    dialog.querySelector('.tour-copy').textContent = copy;
    dialog.querySelector('.tour-error').textContent = '';
    const demo = dialog.querySelector('.tour-demo'); demo.hidden = false; spotlight = null; demo.replaceChildren(el('span', 'tour-preview-label', 'VISTA DE DEMOSTRACIÓN · ' + title));
    const source = document.querySelector(selector);
    const bounds = source?.getBoundingClientRect();
    if (bounds?.width && bounds.height && bounds.top >= 0 && bounds.bottom < window.innerHeight * .45 && !source.closest('dialog')) {
      spotlight = source; demo.hidden = true;
    } else if (source) {
      const clone = source.cloneNode(true);
      // Never retain IDs, media sources or actionable controls in the demonstration.
      for (const node of [clone, ...clone.querySelectorAll('*')]) {
        node.removeAttribute('id'); node.removeAttribute('autofocus');
        for (const attr of [...node.attributes]) if (attr.name.startsWith('on')) node.removeAttribute(attr.name);
        if (['VIDEO', 'AUDIO', 'SOURCE', 'IFRAME'].includes(node.tagName)) { node.removeAttribute('src'); node.removeAttribute('autoplay'); node.setAttribute('preload', 'none'); }
      }
      clone.hidden = false;
      if (clone.tagName === 'DETAILS') clone.open = true;
      demo.append(clone);
    }
    const actions = dialog.querySelector('.tour-actions'); actions.replaceChildren();
    const back = button('Anterior', () => { index--; show(); }); back.disabled = index === 0;
    button(index === steps.length - 1 ? 'Terminar' : 'Siguiente →', () => { if (index === steps.length - 1) close(); else { index++; show(); } }, true).focus();
    position();
  }
  function start() { open(); index = 0; show(); }
  async function offer() {
    const response = await fetch('/api/v1/tutorial');
    if (!response.ok) return;
    const state = await response.json();
    if (state.answer) return;
    open(); index = -1; dialog.classList.add('tour-welcome');
    dialog.querySelector('#tourTitle').textContent = '¿Quieres aprender lo que puede hacer Sugar Mixer?';
    dialog.querySelector('.tour-count').textContent = 'BIENVENIDO · TU GUÍA CON EINSTEIN';
    dialog.querySelector('.tour-copy').textContent = 'Te acompaño por las herramientas de Zucker Editor, paso a paso. Tú marcas el ritmo.';
    dialog.querySelector('.tour-actions').replaceChildren();
    button('No', async () => { if (await answer('no')) close(); });
    button('Sí, enséñame', async () => { if (await answer('yes')) { index = 0; show(); } }, true).focus();
    position();
  }
  window.EditorTutorial = {start, offer, isOpen: () => Boolean(dialog?.open)};
})();
