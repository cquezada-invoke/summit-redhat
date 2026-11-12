/* Hora local de quien mira: el servidor entrega en UTC (ISO 8601 con Z) y aquí se convierte a la zona del navegador.
   - <time datetime="…Z" data-local="fecha-hora|segundos|hora|hora-seg|fecha|rel"> se reescribe al cargar y cada vez que
     entra contenido nuevo a la página (MutationObserver: navegación parcial, fetch, diálogos).
   - Los title con «dd-mm-aaaa HH:MM UTC» también se pasan a la hora local.
   - window.horaLocal.fmt(iso, modo) / .rel(iso) / .convertir(nodo) para el JavaScript que arma tablas desde JSON.
   - Los campos <input name="tz"> (formulario de ingreso) reciben la zona del navegador para los correos. */
(function () {
  'use strict';
  if (window.horaLocal) return;
  var zona = 'UTC';
  try { zona = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (e) { /* navegador antiguo */ }
  var lang = (navigator.language || 'es-CL');
  var loc = /^es-CL$/i.test(lang) ? 'es-CL' : 'es';
  var partes;
  try {
    partes = new Intl.DateTimeFormat(loc, { timeZone: zona, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
  } catch (e) { partes = null; }
  var rtf = null;
  try { rtf = new Intl.RelativeTimeFormat(loc, { numeric: 'auto', style: 'short' }); } catch (e) { /* sin relativo nativo */ }

  function aFecha(v) {
    if (v == null || v === '') return null;
    if (v instanceof Date) return isNaN(v) ? null : v;
    if (typeof v === 'number') return new Date(v > 1e11 ? v : v * 1000);
    var s = String(v).trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;            // solo día: no tiene hora, no se convierte
    var m = s.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:[.,]\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$/);
    if (!m) {                                                    // otros formatos con zona explícita («Tue, 11 Nov 2026 22:07:00 GMT»)
      var o = /(GMT|UTC|[+-]\d{2}:?\d{2}|Z)$/.test(s) ? new Date(s) : null;
      return o && !isNaN(o) ? o : null;
    }
    var z = m[3] || 'Z';                                         // sin zona = UTC (así se guarda en FaceOps/FaceAdmin)
    if (z !== 'Z' && z.indexOf(':') < 0) z = z.slice(0, 3) + ':' + z.slice(3);
    var d = new Date(m[1] + 'T' + (m[2].length === 5 ? m[2] + ':00' : m[2]) + z);
    return isNaN(d) ? null : d;
  }
  function p(d) {
    var o = {};
    if (partes) { partes.formatToParts(d).forEach(function (x) { o[x.type] = x.value; }); if (o.hour === '24') o.hour = '00'; }
    else {
      var q = function (n) { return (n < 10 ? '0' : '') + n; };
      o = { day: q(d.getDate()), month: q(d.getMonth() + 1), year: d.getFullYear(), hour: q(d.getHours()), minute: q(d.getMinutes()), second: q(d.getSeconds()) };
    }
    return o;
  }
  function offset(d) {
    var m = -d.getTimezoneOffset();                               // la zona del navegador es la que se muestra
    try {                                                         // por si el navegador no usa su propia zona en Date
      var s = new Intl.DateTimeFormat('en-US', { timeZone: zona, timeZoneName: 'shortOffset' }).formatToParts(d)
        .filter(function (x) { return x.type === 'timeZoneName'; })[0].value;   // «GMT-3», «GMT+5:30», «GMT»
      var k = s.match(/GMT([+-])(\d{1,2})(?::(\d{2}))?/);
      m = k ? (k[1] === '-' ? -1 : 1) * (parseInt(k[2], 10) * 60 + parseInt(k[3] || '0', 10)) : 0;
    } catch (e) { /* se queda con getTimezoneOffset */ }
    if (!m) return 'UTC';
    var h = Math.floor(Math.abs(m) / 60), r = Math.abs(m) % 60;
    return 'UTC' + (m > 0 ? '+' : '−') + h + (r ? ':' + (r < 10 ? '0' : '') + r : '');
  }
  function etiqueta(d) { return 'Hora local: ' + zona + ', ' + offset(d || new Date()); }
  function fmt(v, modo) {
    var d = aFecha(v);
    if (!d) return v == null || v === '' ? '—' : String(v).replace('T', ' ').slice(0, 16);
    if (modo === 'rel') return rel(d);
    var o = p(d), f = o.day + '-' + o.month + '-' + o.year, h = o.hour + ':' + o.minute;
    switch (modo) {
      case 'hora': return h;
      case 'hora-seg': return h + ':' + o.second;
      case 'fecha': return f;
      case 'segundos': return f + ' ' + h + ':' + o.second;
      default: return f + ' ' + h;
    }
  }
  function rel(v) {
    var d = aFecha(v);
    if (!d) return '—';
    var s = Math.round((d.getTime() - Date.now()) / 1000), a = Math.abs(s);   // ambos en UTC (epoch)
    var u = a < 60 ? ['second', 1] : a < 3600 ? ['minute', 60] : a < 86400 ? ['hour', 3600] : a < 2592000 ? ['day', 86400] : a < 31536000 ? ['month', 2592000] : ['year', 31536000];
    var n = Math.round(s / u[1]);
    if (rtf) return rtf.format(n, u[0]);
    var nom = { second: 's', minute: 'min', hour: 'h', day: 'd', month: 'meses', year: 'años' }[u[0]];
    return (n <= 0 ? 'hace ' : 'en ') + Math.abs(n) + ' ' + nom;
  }
  var RX_ISO = /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}| UTC)?/g;
  function texto(s, modo) { return s == null ? '' : String(s).replace(RX_ISO, function (x) { return fmt(x.replace(' UTC', 'Z'), modo); }); }   // horas dentro de un texto
  var RX_TITULO = /(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2})(?::(\d{2}))? UTC/g;
  function convTitulo(el) {
    var t = el.getAttribute('title');
    if (!t || t.indexOf(' UTC') < 0 || el.hasAttribute('data-local')) return;
    var n = utcATexto(t);
    if (n !== t) el.setAttribute('title', n + '\n' + etiqueta());
  }
  function utcATexto(t) {                                         // «11-11-2026 22:07 UTC» (texto_utc del servidor) → hora local
    return t.replace(RX_TITULO, function (_, D, M, Y, h, mi, s) {
      var d = new Date(Y + '-' + M + '-' + D + 'T' + h + ':' + mi + ':' + (s || '00') + 'Z');
      return fmt(d, s ? 'segundos' : 'fecha-hora');
    });
  }
  function convTextos(raiz) {                                     // textos armados en el servidor con texto_utc
    if (!document.createTreeWalker || raiz.nodeType !== 1 && raiz.nodeType !== 9) return;
    var w = document.createTreeWalker(raiz, 4, null), n, xs = [];
    while ((n = w.nextNode())) if (n.data.indexOf(' UTC') > 0) xs.push(n);
    xs.forEach(function (x) {
      var p = x.parentNode;
      if (!p || p.nodeName === 'SCRIPT' || p.nodeName === 'STYLE' || p.nodeName === 'TEXTAREA' || (p.hasAttribute && p.hasAttribute('data-local'))) return;
      var nv = utcATexto(x.data);
      if (nv !== x.data) { x.data = nv; if (p.setAttribute && !p.getAttribute('title')) p.setAttribute('title', etiqueta()); }
    });
  }
  function uno(el) {
    var d = aFecha(el.getAttribute('datetime') || el.getAttribute('data-ts'));
    if (!d) return;
    var modo = el.getAttribute('data-local') || 'fecha-hora';
    var txt = modo === 'rel' ? rel(d) : fmt(d, modo);
    if (el.textContent !== txt) el.textContent = txt;
    var tip = etiqueta(d) + (modo === 'rel' ? ' · ' + fmt(d, 'fecha-hora') : '') + '\nUTC: ' + d.toISOString().slice(0, 16).replace('T', ' ');
    if (el.getAttribute('title') !== tip) el.setAttribute('title', tip);
  }
  function convertir(raiz) {
    raiz = raiz || document;
    if (raiz.nodeType === 1 && raiz.hasAttribute('data-local')) uno(raiz);
    if (raiz.nodeType === 1 && raiz.hasAttribute('title')) convTitulo(raiz);
    if (!raiz.querySelectorAll) return;
    var xs = raiz.querySelectorAll('[data-local]');
    for (var i = 0; i < xs.length; i++) uno(xs[i]);
    convTextos(raiz);
    var ts = raiz.querySelectorAll('[title*=" UTC"]');
    for (var j = 0; j < ts.length; j++) convTitulo(ts[j]);
    var zs = raiz.querySelectorAll('input[name="tz"]');
    for (var k = 0; k < zs.length; k++) if (!zs[k].value) zs[k].value = zona;
  }
  window.horaLocal = { zona: zona, locale: loc, fmt: fmt, rel: rel, texto: texto, etiqueta: etiqueta, convertir: convertir, aFecha: aFecha };
  function iniciar() {
    convertir(document);
    if (window.MutationObserver) {
      new MutationObserver(function (ms) {
        for (var i = 0; i < ms.length; i++) {
          var m = ms[i];
          if (m.type === 'attributes') { if (m.attributeName === 'title') convTitulo(m.target); else if (m.target.hasAttribute('data-local')) uno(m.target); continue; }
          for (var j = 0; j < m.addedNodes.length; j++) { var n = m.addedNodes[j]; if (n.nodeType === 1) convertir(n); else if (n.parentElement && n.parentElement.hasAttribute('data-local')) uno(n.parentElement); else if (n.nodeType === 3 && n.parentElement && n.data.indexOf(' UTC') > 0) convTextos(n.parentElement); }
        }
      }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['datetime', 'title'] });
    }
    setInterval(function () { var xs = document.querySelectorAll('[data-local="rel"]'); for (var i = 0; i < xs.length; i++) uno(xs[i]); }, 60000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar); else iniciar();
})();
