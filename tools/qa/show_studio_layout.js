/* Show Studio uses the whole window when there is room: the picture large on the left
   with its tools, transport and timeline; on the right a column that stays in view with the
   source section, the scene workshop and the less frequent controls. Narrow windows keep the
   single column. Only the placement changes: every element keeps its id, handlers and order. */
(function () {
  'use strict';
  const WIDE = 1280;
  const SIDE = ['.sourceSections', '.sceneWorkshop', '.workspaceSecondary', '.workspaceFooter'];
  const css = `
  @media (min-width: ${WIDE}px) {
    body main { max-width: none; padding: 12px clamp(16px, 2vw, 32px); }
    .studioWide { display: grid; grid-template-columns: minmax(0, 1fr) clamp(300px, 24vw, 460px); column-gap: clamp(14px, 1.4vw, 24px); align-items: start; }
    .studioWide > :not(.studioSide) { grid-column: 1; min-width: 0; }
    .studioWide > .studioSide { grid-column: 2; grid-row: 1 / span 60; position: sticky; top: 8px; max-height: calc(100vh - 16px); overflow: auto; display: grid; gap: 10px; align-content: start; }
    .studioSide > * { margin: 0; }
    .studioSide #sceneCards { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
    .studioSide .controls { flex-wrap: wrap; }
    .studioWide .screen canvas { width: min(100%, calc((100vh - 150px) * 16 / 9)); height: auto; margin: 0 auto; display: block; }
  }`;

  function mount() {
    const section = document.querySelector('main > section');
    if (!section || section.querySelector(':scope > .studioSide')) return;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.append(style);
    const side = document.createElement('aside');
    side.className = 'studioSide';
    side.setAttribute('aria-label', 'Scenes and options');
    section.append(side);
    section.classList.add('studioWide');
    // Modules add their blocks after loading: move each one into the side column as it appears.
    // Each moved block leaves a marker where it was, and goes back there when the window narrows.
    const homes = new Map();
    const wide = window.matchMedia(`(min-width: ${WIDE}px)`);
    const place = () => {
      if (!wide.matches) {
        for (const [block, marker] of homes) if (block.parentElement === side) marker.after(block);
        return;
      }
      for (const selector of SIDE) {
        const block = section.querySelector(`:scope > ${selector}`);
        if (!block) continue;
        if (!homes.has(block)) { const marker = document.createComment('studio home'); block.before(marker); homes.set(block, marker); }
        side.append(block);
      }
      for (const selector of SIDE) {                 // in this order, whatever order the blocks arrived in
        const block = side.querySelector(`:scope > ${selector}`);
        if (block) side.append(block);
      }
    };
    new MutationObserver(place).observe(section, { childList: true });
    wide.addEventListener('change', place);           // the window crossed the width
    window.addEventListener('resize', place);
    place();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
