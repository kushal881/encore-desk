/* First-time guided tour (issue #43): a dimmed page with one spot lit and a short card. Plain JS, no outside scripts.
   startTour(steps, doneUrl): steps = [{sel, title, text}] (sel may be missing: the card shows in the middle).
   Next / Back / Skip / step dots; Esc skips; never changes data; tells the server once when finished or skipped. */
(function () {
  function el(tag, css, html) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (html != null) e.innerHTML = html; return e; }
  window.startTour = function (steps, doneUrl) {
    if (!steps || !steps.length || document.getElementById('tour')) return;
    var i = 0, root = el('div', 'position:fixed;inset:0;z-index:1000'), spot = el('div', 'position:fixed;border-radius:10px;box-shadow:0 0 0 9999px rgba(15,25,30,.6);transition:all .2s;pointer-events:none');
    var card = el('div', 'position:fixed;background:#fff;border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,.35);padding:16px;max-width:360px;width:calc(100% - 32px);font:14px/1.5 Inter,system-ui,sans-serif;color:#17191C');
    root.id = 'tour'; card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true'); card.setAttribute('aria-labelledby', 'tourt'); card.tabIndex = -1;
    root.appendChild(spot); root.appendChild(card); document.body.appendChild(root);
    function finish() { root.remove(); document.removeEventListener('keydown', key); if (doneUrl) { try { fetch(doneUrl, { method: 'POST', credentials: 'same-origin' }); } catch (e) {} } }
    function key(e) { if (e.key === 'Escape') finish(); else if (e.key === 'ArrowRight') go(1); else if (e.key === 'ArrowLeft') go(-1); }
    function go(d) { var n = i + d; if (n >= steps.length) return finish(); if (n < 0) return; i = n; show(); }
    function show() {
      var s = steps[i], t = null, narrow = window.innerWidth < 520;
      if (s.sel) { try { t = document.querySelector(s.sel); } catch (e) { t = null; } }   /* an unsupported selector: the card shows in the middle */
      if (t) t.scrollIntoView({ block: 'center', behavior: 'instant' });
      var r = t ? t.getBoundingClientRect() : null;
      if (r && r.width) { spot.style.cssText += ';display:block;left:' + (r.left - 6) + 'px;top:' + (r.top - 6) + 'px;width:' + (r.width + 12) + 'px;height:' + (r.height + 12) + 'px'; }
      else { spot.style.cssText += ';display:block;left:50%;top:50%;width:0;height:0'; }
      var dots = steps.map(function (_, k) { return '<span style="display:inline-block;width:8px;height:8px;border-radius:4px;margin-right:4px;background:' + (k === i ? '#0E6B5C' : '#D6D3CA') + '"></span>'; }).join('');
      card.innerHTML = '<div class="small" style="color:#5B5F66;font-size:12px">' + (i + 1) + ' / ' + steps.length + '</div><div id="tourt" style="font-weight:600;font-size:16px;margin:2px 0 6px"></div><div id="tourx"></div>' +
        '<div style="display:flex;align-items:center;gap:8px;margin-top:12px;flex-wrap:wrap"><span aria-hidden="true">' + dots + '</span><span style="flex:1"></span>' +
        '<button type="button" id="tskip" style="min-height:40px;border:0;background:none;color:#5B5F66;cursor:pointer;font:inherit">Skip tour</button>' +
        (i ? '<button type="button" id="tback" style="min-height:40px;padding:0 14px;border:1px solid #0E6B5C;background:#fff;color:#0E6B5C;border-radius:10px;font:600 14px Inter,sans-serif;cursor:pointer">Back</button>' : '') +
        '<button type="button" id="tnext" style="min-height:40px;padding:0 16px;border:1px solid #0E6B5C;background:#0E6B5C;color:#fff;border-radius:10px;font:600 14px Inter,sans-serif;cursor:pointer">' + (i === steps.length - 1 ? 'Finish' : 'Next') + '</button></div>';
      card.querySelector('#tourt').textContent = s.title; card.querySelector('#tourx').textContent = s.text;
      card.querySelector('#tskip').onclick = finish; card.querySelector('#tnext').onclick = function () { go(1); };
      var b = card.querySelector('#tback'); if (b) b.onclick = function () { go(-1); };
      if (narrow || !r || !r.width) { card.style.left = '16px'; card.style.right = 'auto'; card.style.bottom = narrow ? '16px' : 'auto'; card.style.top = narrow ? 'auto' : '30%'; if (!narrow) card.style.left = 'calc(50% - 180px)'; }
      else { var below = r.bottom + 16 + 200 < window.innerHeight; card.style.bottom = 'auto'; card.style.top = (below ? r.bottom + 16 : Math.max(16, r.top - 16 - card.offsetHeight)) + 'px';
             card.style.left = Math.max(16, Math.min(r.left, window.innerWidth - card.offsetWidth - 16)) + 'px'; }
      card.focus();
    }
    document.addEventListener('keydown', key); show();
  };
})();
