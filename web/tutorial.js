/* Read-only guided tour: no project mutation, media loading or pipeline actions. */
(() => {
  const steps = [
    ['Your next edit', '#newProject', 'New project starts with no files and the name New Jam. Your previous projects stay available under Existing projects.'],
    ['One edit for every idea', '#editTypeDialog .platforms', 'YouTube edits one song; Reel makes a short clip; 360 keeps the view explorable; Medley combines songs; Backstage tells what happens behind the scenes.'],
    ['Give it a name', '#videoName', 'Type the song title. It identifies the project and the video you are rendering.'],
    ['Drop your files', '#dropZone', 'Add videos, audio and, if you like, your logo. Each file shows its import progress. Medley can use the audio built into the videos; requirements change with the mode.'],
    ['Save before you continue', '#confirmFiles', 'When you continue, you choose where the project lives, for example on your external drive. Working files are stored there and you can reopen it from Existing projects.'],
    ['The exact excerpt', '.trim-box', 'Listen to the audio and set the start and end. Check these times if you change track: they belong to the audio you have chosen now.'],
    ['Background preparation', '#settingsPreparation', 'While you adjust the parameters, the app can prepare the videos. The message and bars show the work already done.'],
    ['Musical moments', '#instrumentHighlightsOption', 'Turn on this analysis to look for vocal activity and possible instrumental solos. It adds analysis time and reuses saved results; review the proposed shots afterwards.'],
    ['Balance the cameras', '#cameraMix', 'The weights guide how much each camera appears. They do not need to add up to 100. The fixed camera can also add a gentle zoom movement.'],
    ['Show where each musician is', '[data-spherical-landmark="singer"]', 'In 360, frame the singer and assign the right subject. Save the angle. “Use automatically” includes or excludes that view from the edit.'],
    ['Fine tune the framing', '[data-spherical-landmark="singer"] details', 'In Advanced angles you can fine-tune yaw (turn), pitch (height), FOV (width) and roll (tilt). The projection changes the curvature: check faces and edges before saving.'],
    ['A Reel made to measure', '#reelOptions', 'Set the length, vertical or horizontal format and cut density. With a single source, the available footage may be one continuous take.'],
    ['Stories between songs', '#backstageOptions', 'In Backstage you choose the length and can add messages. Then you review the proposed edit before rendering.'],
    ['Medley Populi', '#medleyOptions', 'Combine videos of different songs and choose their audio. Set the total length, black between songs and fades so every musical change is clear.'],
    ['Start the edit', '#startWizard', 'Make it starts the process with the chosen parameters. Preparation, editing and export each show their own progress.'],
    ['Follow the real work', '#progressDetails', 'Open the details to see what the app is doing. You can copy the report if something fails. Cancel stops the work; you do not need to close the app.'],
    ['Review before rendering', '#renderReviewedTop', 'In Frames you review the proposed shots, adjust the ones that need it and replace the rejected ones. Render Frames is also at the top so you can continue without scrolling down.'],
    ['Text that goes with the video', '.compose-captions-panel', 'In the modes with the composition editor you can transcribe speech, import SRT or LRC, fix text and timing, export SRT and burn in the captions.'],
    ['Your visual identity', '.compose-overlays-panel', 'Add images, videos and your logo. Adjust their position and duration on the timeline. You can also reuse previous overlays.'],
    ['The result and the tools', '.wizard-nav', 'Watch the video and open its location from Result. The top menu gathers the app actions. In Tools you can check for updates; in Help you can replay this tutorial.']
  ];
  let dialog, index = -1, previousFocus, pending = false, spotlight = null;
  const el = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; if (text) n.textContent = text; return n; };
  function build() {
    if (dialog) return;
    dialog = el('dialog', 'einstein-tour');
    dialog.setAttribute('aria-labelledby', 'tourTitle');
    dialog.innerHTML = `<svg class="tour-shade" aria-hidden="true"><defs><mask id="tourMask"><rect width="100%" height="100%" fill="white"/><ellipse id="tourHole" fill="black"/></mask></defs><rect width="100%" height="100%" fill="rgba(0,0,0,.82)" mask="url(#tourMask)"/><ellipse id="tourRing" fill="none" stroke="#f7d776" stroke-width="3"/></svg><div class="tour-demo" aria-hidden="true" inert></div><div class="tour-guide"><img src="/tutorial-einstein.png" alt="Einstein guides you through the tutorial"/><section class="tour-bubble"><span class="tour-count"></span><h2 id="tourTitle"></h2><p class="tour-copy"></p><p class="tour-error" role="status"></p><div class="tour-actions"></div></section></div><button class="tour-close" aria-label="Close tutorial">×</button>`;
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
      if (!r.ok) throw new Error('Your answer could not be saved. Please try again.');
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
    const demo = dialog.querySelector('.tour-demo'); demo.hidden = false; spotlight = null; demo.replaceChildren(el('span', 'tour-preview-label', 'DEMO VIEW · ' + title));
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
    const back = button('Back', () => { index--; show(); }); back.disabled = index === 0;
    button(index === steps.length - 1 ? 'Finish' : 'Next →', () => { if (index === steps.length - 1) close(); else { index++; show(); } }, true).focus();
    position();
  }
  function start() { open(); index = 0; show(); }
  async function offer() {
    const response = await fetch('/api/v1/tutorial');
    if (!response.ok) return;
    const state = await response.json();
    if (state.answer) return;
    open(); index = -1; dialog.classList.add('tour-welcome');
    dialog.querySelector('#tourTitle').textContent = 'Want to learn what Zucker Editor can do?';
    dialog.querySelector('.tour-count').textContent = 'WELCOME · YOUR GUIDE, EINSTEIN';
    dialog.querySelector('.tour-copy').textContent = 'I will walk you through the tools in Zucker Editor, step by step. You set the pace.';
    dialog.querySelector('.tour-actions').replaceChildren();
    button('No', async () => { if (await answer('no')) close(); });
    button('Yes, show me', async () => { if (await answer('yes')) { index = 0; show(); } }, true).focus();
    position();
  }
  window.EditorTutorial = {start, offer, isOpen: () => Boolean(dialog?.open)};
})();
