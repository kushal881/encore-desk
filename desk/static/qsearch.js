/* Quotation search as you type (issue #86), used by New project and the dashboard.
   <input data-qsearch data-results="ID" [data-pick="ID"]>  results from /new/search after 2 letters and a short pause.
   Up / Down move, Enter selects, Esc clears. Without data-pick, Select goes to /new?ref=…; with it, the Selected card
   from /new/pick is put into that element. A quotation already on the desk opens instead. */
(function () {
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function hi(s, q) {                        /* the matching letters in bold */
    var t = String(s || ''), i = t.toLowerCase().indexOf(q.toLowerCase());
    return i < 0 ? esc(t) : esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + q.length)) + '</mark>' + esc(t.slice(i + q.length));
  }
  function money(n, cur) { return n ? (cur === 'INR' || !cur ? '₹ ' : cur + ' ') + Math.round(n).toLocaleString('en-IN') : ''; }
  document.querySelectorAll('[data-qsearch]').forEach(function (inp) {
    var box = document.getElementById(inp.getAttribute('data-results')), pickId = inp.getAttribute('data-pick'), timer = null, cur = -1, last = '', seq = 0;
    if (!box) return;
    function items() { return [].slice.call(box.querySelectorAll('.qres')); }
    function mark(n) { var it = items(); it.forEach(function (x, i) { x.classList.toggle('on', i === n); x.setAttribute('aria-selected', i === n ? 'true' : 'false'); }); cur = n; if (it[n]) it[n].scrollIntoView({block: 'nearest'}); }
    function clear() { box.innerHTML = ''; cur = -1; last = ''; inp.setAttribute('aria-expanded', 'false'); }
    function choose(el) {
      if (!el) return;
      var ref = el.getAttribute('data-ref');
      if (el.getAttribute('data-desk') === '1') { location.href = '/orders/' + encodeURIComponent(ref); return; }
      if (!pickId) { location.href = '/new?ref=' + encodeURIComponent(ref); return; }
      var to = document.getElementById(pickId);
      to.innerHTML = '<div class="sub small">Reading ' + esc(ref) + ' …</div>';
      fetch('/new/pick?ref=' + encodeURIComponent(ref), {credentials: 'same-origin'}).then(function (r) { return r.text(); }).then(function (h) {
        to.innerHTML = h; clear(); inp.value = ''; inp.closest('.qsbox') && (inp.closest('.qsbox').style.display = 'none');
        document.dispatchEvent(new CustomEvent('qpicked', {detail: {ref: ref}}));
      });
    }
    function run() {
      var q = inp.value.trim();
      if (q.length < 2) { clear(); if (q.length === 1) box.innerHTML = '<div class="sub small" style="padding:8px">Type one more letter…</div>'; return; }
      if (q === last) return;
      last = q; var my = ++seq;
      box.innerHTML = '<div class="sub small" style="padding:8px">Searching…</div>';
      fetch('/new/search?q=' + encodeURIComponent(q), {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (d) {
        if (my !== seq) return;                                     /* an older answer: a newer search is on its way */
        var h = d.note ? '<div class="sub small" role="status" style="padding:8px;color:#9A4508">' + esc(d.note) + '</div>' : '';
        if (!d.results.length) h += '<div class="sub small" style="padding:8px">No quotation or client matches “' + esc(q) + '”.</div>';
        d.results.forEach(function (r) {
          h += '<div class="qres" role="option" data-ref="' + esc(r.ref) + '" data-desk="' + (r.on_desk ? 1 : 0) + '">' +
            '<span style="flex-grow:1;min-width:0"><b>' + hi(r.ref, q) + '</b> · ' + hi(r.client, q) +
            '<span class="sub small" style="display:block">' + esc([r.kind, r.date, money(r.amount, r.currency), r.lines + ' line' + (r.lines === 1 ? '' : 's'), r.salesperson].filter(Boolean).join(' · ')) + '</span></span>' +
            (r.on_desk ? '<span class="small" style="white-space:nowrap">Already on the desk · <a href="/orders/' + encodeURIComponent(r.ref) + '">Open ›</a></span>'
                       : '<button type="button" class="sm">Select</button>') + '</div>';
        });
        box.innerHTML = h; inp.setAttribute('aria-expanded', 'true'); cur = -1;
        items().forEach(function (el, i) { el.addEventListener('click', function (e) { if (e.target.tagName === 'A') return; choose(el); }); el.addEventListener('mouseenter', function () { mark(i); }); });
      }).catch(function () { if (my === seq) box.innerHTML = '<div class="sub small" style="padding:8px;color:#9A4508">The search could not be done just now. Try again.</div>'; });
    }
    inp.setAttribute('role', 'combobox'); inp.setAttribute('aria-controls', box.id); inp.setAttribute('aria-expanded', 'false'); inp.setAttribute('autocomplete', 'off');
    box.setAttribute('role', 'listbox');
    inp.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(run, 250); });
    inp.addEventListener('keydown', function (e) {
      var it = items();
      if (e.key === 'ArrowDown' && it.length) { e.preventDefault(); mark(Math.min(it.length - 1, cur + 1)); }
      else if (e.key === 'ArrowUp' && it.length) { e.preventDefault(); mark(Math.max(0, cur - 1)); }
      else if (e.key === 'Enter' && it.length) { e.preventDefault(); choose(it[cur >= 0 ? cur : 0]); }
      else if (e.key === 'Escape') { inp.value = ''; clear(); }
    });
  });
  document.addEventListener('click', function (e) {                 /* "Change" on the Selected card */
    if (!e.target.closest('[data-qchange]')) return;
    var card = e.target.closest('[data-qsel]'); if (card) card.innerHTML = '';
    document.querySelectorAll('.qsbox').forEach(function (b) { b.style.display = ''; var i = b.querySelector('[data-qsearch]'); if (i) i.focus(); });
    document.dispatchEvent(new CustomEvent('qpicked', {detail: {ref: ''}}));
  });
})();
