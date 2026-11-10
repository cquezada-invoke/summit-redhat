/* App de pagos (demo). Toda operación va por HTTP/JSON al backend de JBoss: así el espejo del balanceador al data lake
   recibe cada request de negocio (rubro, región, medio, monto, canal). Sin frameworks. */
(() => {
  'use strict';
  const BASE = document.body.dataset.base || './';
  const CANAL = (matchMedia('(max-width: 720px)').matches || /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) ? 'app' : 'web';
  const $ = (s, el = document) => el.querySelector(s);
  const vista = $('#vista'), menu = $('#menu'), quien = $('#quien');
  const ESTADO = { yo: null, comercios: null };
  const dinero = new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
  const fFecha = new Intl.DateTimeFormat('es-CL', { timeZone: 'America/Santiago', dateStyle: 'short', timeStyle: 'short' });
  const hoy = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Santiago' }).format(new Date());

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const usd = (n) => dinero.format(Number(n || 0));
  const fecha = (iso) => (iso ? fFecha.format(new Date(iso)) : '');
  const chip = (e) => (e ? `<span class="chip ${esc(e)}">${esc(e)}</span>` : '');
  const TIPOS = { pago: 'Pago', transferencia: 'Transferencia', reembolso: 'Reembolso', anulacion: 'Anulación', abono: 'Abono' };
  const MEDIOS = { tarjeta: 'Tarjeta', qr: 'QR', transferencia: 'Transferencia' };

  function aviso(txt, mal) {
    const a = $('#aviso');
    a.textContent = txt; a.className = 'aviso' + (mal ? ' mal' : ''); a.hidden = false;
    clearTimeout(aviso.t); aviso.t = setTimeout(() => { a.hidden = true; }, 3800);
  }

  async function api(metodo, ruta, cuerpo) {
    const op = { method: metodo, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (cuerpo) { op.headers['Content-Type'] = 'application/json'; op.body = JSON.stringify(cuerpo); }
    const r = await fetch(BASE + ruta, op);
    let d = {};
    try { d = await r.json(); } catch (e) { /* respuesta sin JSON */ }
    if (r.status === 401 && ruta !== 'login') { ESTADO.yo = null; pintarLogin(); throw new Error(d.error || 'Sesión expirada'); }
    if (!r.ok) throw new Error(d.error || ('Error ' + r.status));
    return d;
  }
  const qs = (o) => Object.entries(o).filter(([, v]) => v !== '' && v != null).map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');

  // ───── sesión ─────
  function pintarLogin(msg) {
    menu.hidden = true; quien.innerHTML = '';
    vista.innerHTML = `<div class="login"><div class="tarjeta"><h1>Ingresar</h1>
      <form class="f" id="fLogin" autocomplete="on">
        <label>Usuario<input name="usuario" autocomplete="username" value="demo" required autofocus></label>
        <label>Clave<input name="clave" type="password" autocomplete="current-password" value="demo" required></label>
        <div id="eLogin">${msg ? `<div class="error">${esc(msg)}</div>` : ''}</div>
        <button class="p" type="submit">Entrar</button>
        <p class="sub">Demostración: usuario <b>demo</b> · clave <b>demo</b></p>
      </form></div></div>`;
    $('#fLogin').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const f = ev.target, b = $('button', f);
      b.disabled = true;
      try {
        ESTADO.yo = await api('POST', 'login', { usuario: f.usuario.value.trim(), clave: f.clave.value, canal: CANAL,
          dispositivo: CANAL === 'app' ? 'Móvil' : 'Navegador' });
        f.clave.value = '';
        entrar();
      } catch (e) {
        $('#eLogin').innerHTML = `<div class="error">${esc(e.message)}</div>`; b.disabled = false;
      }
    });
  }

  function entrar() {
    const yo = ESTADO.yo;
    quien.innerHTML = `<span>${esc(yo.nombre)} · ${esc(yo.cuenta)}</span><button type="button" id="salir">Salir</button>`;
    $('#salir').onclick = async () => { try { await api('POST', 'logout'); } catch (e) { /* igual sale */ } ESTADO.yo = null; location.hash = ''; pintarLogin(); };
    const items = [['resumen', 'Resumen'], ['pagar', 'Pagar'], ['transferir', 'Transferir'], ['historial', 'Historial'], ['reembolsos', 'Reembolsos']];
    if (yo.rol === 'supervisor') items.push(['admin', 'Administración']);
    menu.innerHTML = items.map(([k, t]) => `<a href="#${k}" data-v="${k}">${t}</a>`).join('');
    menu.hidden = false;
    rutear();
  }

  const VISTAS = { resumen, pagar, transferir, historial, reembolsos, admin };
  function rutear() {
    if (!ESTADO.yo) return;
    let v = (location.hash || '#resumen').slice(1).split('?')[0];
    if (!VISTAS[v] || (v === 'admin' && ESTADO.yo.rol !== 'supervisor')) v = 'resumen';
    menu.querySelectorAll('a').forEach((a) => a.classList.toggle('on', a.dataset.v === v));
    vista.innerHTML = '<p class="cargando">Cargando…</p>';
    VISTAS[v]().catch((e) => { vista.innerHTML = `<div class="error">${esc(e.message)}</div>`; });
  }
  addEventListener('hashchange', rutear);

  async function comercios() {
    if (!ESTADO.comercios) ESTADO.comercios = await api('GET', 'comercios');
    return ESTADO.comercios;
  }

  function filaMov(m, acciones) {
    const signo = Number(m.monto) >= 0 ? 'pos' : 'neg';
    const detalle = [m.rubro, MEDIOS[m.medio], m.canal === 'app' ? 'móvil' : m.canal].filter(Boolean).map(esc).join(' · ');
    return `<tr><td>${esc(fecha(m.fecha))}<div class="sub">${esc(m.codigo)}</div></td>
      <td>${esc(m.contraparte || TIPOS[m.tipo] || m.tipo)}<div class="sub">${esc(TIPOS[m.tipo] || m.tipo)}${detalle ? ' · ' + detalle : ''}${m.glosa ? ' · ' + esc(m.glosa) : ''}</div></td>
      <td>${chip(m.estado)} ${m.reembolso ? `<span class="chip ${esc(m.reembolso)}">reembolso ${esc(m.reembolso)}</span>` : ''}</td>
      <td class="n ${signo}">${Number(m.monto) < 0 ? '−' + usd(-m.monto) : '+' + usd(m.monto)}</td>${acciones ? `<td class="n">${acciones(m)}</td>` : ''}</tr>`;
  }
  const tablaMov = (l, acciones) => (l.length
    ? `<div class="tabla"><table class="mov"><thead><tr><th>Fecha</th><th>Detalle</th><th>Estado</th><th class="n">Monto</th>${acciones ? '<th></th>' : ''}</tr></thead>
       <tbody>${l.map((m) => filaMov(m, acciones)).join('')}</tbody></table></div>`
    : '<p class="vacio">Sin movimientos.</p>');

  // ───── vistas ─────
  async function resumen() {
    const d = await api('GET', 'saldo');
    vista.innerHTML = `<h1>Hola, ${esc(d.titular.split(' ')[0])}</h1>
      <div class="grilla">
        <div class="tarjeta cifra saldo">Saldo disponible<b>${usd(d.saldo)}</b><span class="sub">Cuenta ${esc(d.cuenta)} · ${esc(d.moneda)}</span></div>
        <div class="tarjeta cifra">Pagado y transferido este mes<b>${usd(d.gasto_mes)}</b><span class="sub">${d.operaciones_mes} operaciones</span></div>
        <div class="tarjeta"><h2>Acciones</h2><div class="medios">
          <button class="p" type="button" onclick="location.hash='pagar'">Pagar</button>
          <button class="s" type="button" onclick="location.hash='transferir'">Transferir</button>
          <button class="s" type="button" onclick="location.hash='reembolsos'">Pedir reembolso</button></div></div>
      </div>
      <div class="tarjeta"><h2>Últimos movimientos</h2>${tablaMov(d.movimientos)}
        <p><a href="#historial">Ver historial completo</a></p></div>`;
  }

  async function pagar() {
    const c = await comercios();
    vista.innerHTML = `<h1>Pagar</h1><div class="tarjeta"><form class="f" id="fPago">
      <label>Comercio<select name="comercio" required><option value="">Elige un comercio…</option>
        ${c.comercios.map((x) => `<option value="${esc(x.codigo)}" data-rubro="${esc(x.rubro)}">${esc(x.nombre)} · ${esc(x.rubro)}</option>`).join('')}</select></label>
      <div class="fila"><label>Monto (USD)<input name="monto" type="number" min="0.01" max="10000" step="0.01" inputmode="decimal" required></label>
        <label>Región<select name="region">${c.regiones.map((r) => `<option ${r === ESTADO.yo.region ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></label></div>
      <div><label style="margin-bottom:6px">Medio de pago</label><div class="medios">
        <label><input type="radio" name="medio" value="tarjeta" checked> Tarjeta</label>
        <label><input type="radio" name="medio" value="qr"> QR</label>
        <label><input type="radio" name="medio" value="transferencia"> Transferencia</label></div></div>
      <label>Glosa (opcional)<input name="glosa" maxlength="80"></label>
      <div id="eForm"></div><button class="p" type="submit">Pagar</button></form></div>`;
    $('#fPago').addEventListener('submit', (ev) => enviar(ev, async (f) => {
      const op = f.comercio.selectedOptions[0];
      const medio = f.medio.value;
      const cuerpo = { comercio: f.comercio.value, rubro: op.dataset.rubro, region: f.region.value, medio, monto: f.monto.value,
        moneda: 'USD', canal: CANAL, glosa: f.glosa.value };
      const r = await api('POST', medio === 'qr' ? 'qr/pagar' : 'pagos', cuerpo);
      aviso(`Pago ${r.codigo} aprobado · saldo ${usd(r.saldo)}`);
      location.hash = 'resumen';
    }));
  }

  async function transferir() {
    const [cu, c] = await Promise.all([api('GET', 'cuentas'), comercios()]);
    vista.innerHTML = `<h1>Transferir</h1><div class="tarjeta"><form class="f" id="fTrf">
      <label>Cuenta de destino<input name="destino" list="dCuentas" placeholder="CTA-000000" pattern="CTA-[0-9]{6}" required></label>
      <datalist id="dCuentas">${cu.cuentas.map((x) => `<option value="${esc(x.cuenta)}">${esc(x.titular)}</option>`).join('')}</datalist>
      <div class="fila"><label>Monto (USD)<input name="monto" type="number" min="0.01" max="10000" step="0.01" inputmode="decimal" required></label>
        <label>Región<select name="region">${c.regiones.map((r) => `<option ${r === ESTADO.yo.region ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></label></div>
      <label>Glosa (opcional)<input name="glosa" maxlength="80"></label>
      <div id="eForm"></div><button class="p" type="submit">Transferir</button></form>
      <p class="sub">Cuentas de demostración: ${cu.cuentas.map((x) => `${esc(x.cuenta)} (${esc(x.titular)})`).join(' · ')}</p></div>`;
    $('#fTrf').addEventListener('submit', (ev) => enviar(ev, async (f) => {
      const r = await api('POST', 'transferencias', { destino: f.destino.value.trim().toUpperCase(), monto: f.monto.value, moneda: 'USD',
        medio: 'transferencia', region: f.region.value, canal: CANAL, glosa: f.glosa.value });
      aviso(`Transferencia ${r.codigo} a ${r.titular} · saldo ${usd(r.saldo)}`);
      location.hash = 'resumen';
    }));
  }

  async function historial() {
    const c = await comercios();
    const opt = (l, sel) => '<option value="">Todos</option>' + l.map(([v, t]) => `<option value="${esc(v)}" ${v === sel ? 'selected' : ''}>${esc(t)}</option>`).join('');
    const f0 = historial.f || { desde: '', hasta: '', tipo: '', medio: '', rubro: '', estado: '', texto: '' };
    vista.innerHTML = `<h1>Historial</h1><div class="tarjeta"><form class="filtros" id="fFil">
      <label>Desde<input type="date" name="desde" value="${esc(f0.desde)}"></label>
      <label>Hasta<input type="date" name="hasta" value="${esc(f0.hasta)}"></label>
      <label>Tipo<select name="tipo">${opt(Object.entries(TIPOS), f0.tipo)}</select></label>
      <label>Medio<select name="medio">${opt(Object.entries(MEDIOS), f0.medio)}</select></label>
      <label>Rubro<select name="rubro">${opt(c.rubros.map((r) => [r, r]), f0.rubro)}</select></label>
      <label>Estado<select name="estado">${opt([['aprobado', 'Aprobado'], ['anulado', 'Anulado'], ['reembolsado', 'Reembolsado']], f0.estado)}</select></label>
      <label>Buscar<input name="texto" placeholder="Comercio, código…" value="${esc(f0.texto)}"></label>
      <button class="p" type="submit">Filtrar</button></form><div id="res"><p class="cargando">Cargando…</p></div></div>`;
    const cargar = async () => {
      const f = $('#fFil');
      historial.f = Object.fromEntries(new FormData(f));
      const d = await api('GET', 'movimientos?' + qs(historial.f));
      $('#res').innerHTML = `<p class="sub">${d.total} movimientos · entradas ${usd(d.entradas)} · salidas ${usd(d.salidas)}</p>` +
        tablaMov(d.movimientos, (m) => {
          if (m.tipo !== 'pago' || m.estado !== 'aprobado') return '';
          const delDia = m.fecha && new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Santiago' }).format(new Date(m.fecha)) === hoy();
          return (delDia ? `<button class="s rojo" data-anular="${esc(m.codigo)}">Anular</button> ` : '') +
            (m.reembolso ? '' : `<button class="s" data-reembolso="${esc(m.codigo)}">Reembolso</button>`);
        });
    };
    $('#fFil').addEventListener('submit', (ev) => { ev.preventDefault(); cargar().catch((e) => aviso(e.message, true)); });
    $('#res').addEventListener('click', async (ev) => {
      const b = ev.target.closest('button');
      if (!b) return;
      if (b.dataset.reembolso) { reembolsos.pago = b.dataset.reembolso; location.hash = 'reembolsos'; return; }
      if (b.dataset.anular && confirm(`¿Anular el pago ${b.dataset.anular}? El monto vuelve a tu saldo.`)) {
        try {
          const r = await api('POST', `pagos/${encodeURIComponent(b.dataset.anular)}/anular`, { canal: CANAL, motivo: 'anulado por el cliente' });
          aviso(`Pago ${r.codigo} anulado · saldo ${usd(r.saldo)}`); cargar();
        } catch (e) { aviso(e.message, true); }
      }
    });
    await cargar();
  }

  async function reembolsos() {
    const [mis, pagos] = await Promise.all([api('GET', 'reembolsos'), api('GET', 'movimientos?' + qs({ tipo: 'pago', estado: 'aprobado' }))]);
    const elegibles = pagos.movimientos.filter((m) => !m.reembolso);
    const pre = reembolsos.pago; reembolsos.pago = null;
    vista.innerHTML = `<h1>Reembolsos</h1><div class="grilla">
      <div class="tarjeta"><h2>Pedir reembolso</h2><form class="f" id="fRee">
        <label>Pago<select name="id" required><option value="">Elige un pago…</option>
          ${elegibles.map((m) => `<option value="${esc(m.codigo)}" data-monto="${esc(-m.monto)}" data-rubro="${esc(m.rubro || '')}" ${m.codigo === pre ? 'selected' : ''}>${esc(m.codigo)} · ${esc(m.contraparte)} · ${usd(-m.monto)}</option>`).join('')}</select></label>
        <label>Monto a reembolsar (USD, vacío = total)<input name="monto" type="number" min="0.01" step="0.01" inputmode="decimal"></label>
        <label>Motivo<select name="motivo" required><option value="">Elige un motivo…</option>
          <option>Producto no recibido</option><option>Cobro duplicado</option><option>Error de monto</option><option>Producto defectuoso</option><option>Arrepentimiento</option></select></label>
        <div id="eForm"></div><button class="p" type="submit">Solicitar</button></form></div>
      <div class="tarjeta"><h2>Mis solicitudes</h2>${mis.reembolsos.length ? `<div class="tabla"><table><thead><tr><th>Solicitud</th><th>Pago</th><th>Estado</th><th class="n">Monto</th></tr></thead><tbody>
        ${mis.reembolsos.map((r) => `<tr><td>${esc(r.codigo)}<div class="sub">${esc(fecha(r.fecha))} · ${esc(r.motivo)}</div></td><td>${esc(r.pago)}<div class="sub">${esc(r.comercio || '')}</div></td><td>${chip(r.estado)}</td><td class="n">${usd(r.monto)}</td></tr>`).join('')}
        </tbody></table></div>` : '<p class="vacio">No has pedido reembolsos.</p>'}</div></div>`;
    $('#fRee').addEventListener('submit', (ev) => enviar(ev, async (f) => {
      const op = f.id.selectedOptions[0];
      const r = await api('POST', 'reembolsos', { id: f.id.value, monto: f.monto.value, motivo: f.motivo.value, rubro: op.dataset.rubro,
        region: ESTADO.yo.region, moneda: 'USD', canal: CANAL });
      aviso(`Reembolso ${r.codigo} solicitado por ${usd(r.monto)}`);
      reembolsos();
    }));
  }

  async function admin() {
    const f = admin.fecha || hoy();
    const d = await api('GET', 'admin/resumen?' + qs({ fecha: f }));
    const max = Math.max(1, ...d.por_rubro.map((x) => Number(x.monto)));
    const tablaSimple = (l, k, titulo) => (l.length ? `<table><thead><tr><th>${titulo}</th><th class="n">Pagos</th><th class="n">Monto</th></tr></thead><tbody>
      ${l.map((x) => `<tr><td>${esc(k === 'medio' ? (MEDIOS[x[k]] || x[k]) : (x[k] === 'app' ? 'móvil (app)' : x[k]))}</td><td class="n">${x.pagos}</td><td class="n">${usd(x.monto)}</td></tr>`).join('')}</tbody></table>`
      : '<p class="vacio">Sin pagos.</p>');
    vista.innerHTML = `<div id="adm"><h1>Administración</h1>
      <div class="tarjeta"><form class="filtros" id="fDia" style="margin:0;max-width:420px"><label>Día<input type="date" name="fecha" value="${esc(d.fecha)}"></label>
        <button class="p" type="submit">Ver</button></form></div>
      <div class="grilla"><div class="tarjeta cifra">Pagos del día<b>${d.pagos}</b></div><div class="tarjeta cifra">Monto del día<b>${usd(d.monto)}</b></div>
        <div class="tarjeta cifra">Reembolsos por resolver<b>${d.reembolsos_pendientes.length}</b></div></div>
      <div class="tarjeta"><h2>Pagos del día por rubro</h2>${d.por_rubro.length ? `<div class="barras">${d.por_rubro.map((x) => `<div><span>${esc(x.rubro)}</span>
        <i style="width:${(100 * Number(x.monto) / max).toFixed(1)}%"></i><span>${usd(x.monto)} <span class="sub">(${x.pagos})</span></span></div>`).join('')}</div>` : '<p class="vacio">Sin pagos ese día.</p>'}</div>
      <div class="grilla"><div class="tarjeta"><h2>Por medio</h2>${tablaSimple(d.por_medio, 'medio', 'Medio')}</div>
        <div class="tarjeta"><h2>Por canal</h2>${tablaSimple(d.por_canal, 'canal', 'Canal')}</div></div>
      <div class="tarjeta"><h2>Reembolsos por resolver</h2>${d.reembolsos_pendientes.length ? `<div class="tabla"><table><thead><tr><th>Solicitud</th><th>Cliente</th><th>Pago</th><th class="n">Monto</th><th></th></tr></thead><tbody>
        ${d.reembolsos_pendientes.map((r) => `<tr><td>${esc(r.codigo)}<div class="sub">${esc(fecha(r.fecha))} · ${esc(r.motivo)}</div></td><td>${esc(r.cliente)}</td>
          <td>${esc(r.pago)}<div class="sub">${esc(r.comercio || '')} · ${esc(r.rubro || '')}</div></td><td class="n">${usd(r.monto)}</td>
          <td class="n"><button class="s" data-ok="${esc(r.codigo)}">Aprobar</button> <button class="s rojo" data-no="${esc(r.codigo)}">Rechazar</button></td></tr>`).join('')}
        </tbody></table></div>` : '<p class="vacio">No hay solicitudes pendientes.</p>'}</div></div>`;
    $('#fDia').addEventListener('submit', (ev) => { ev.preventDefault(); admin.fecha = ev.target.fecha.value; rutear(); });
    $('#adm').addEventListener('click', async (ev) => {
      const b = ev.target.closest('button[data-ok],button[data-no]');
      if (!b) return;
      const cod = b.dataset.ok || b.dataset.no;
      try {
        await api('POST', `reembolsos/${encodeURIComponent(cod)}/${b.dataset.ok ? 'aprobar' : 'rechazar'}`, { canal: CANAL });
        aviso(`Reembolso ${cod} ${b.dataset.ok ? 'aprobado' : 'rechazado'}`); rutear();
      } catch (e) { aviso(e.message, true); }
    });
  }

  async function enviar(ev, accion) {
    ev.preventDefault();
    const f = ev.target, b = $('button[type=submit]', f), e = $('#eForm', f);
    b.disabled = true; if (e) e.innerHTML = '';
    try { await accion(f); } catch (x) { if (e) e.innerHTML = `<div class="error">${esc(x.message)}</div>`; else aviso(x.message, true); } finally { b.disabled = false; }
  }

  // ───── arranque ─────
  api('GET', 'sesion').then((yo) => { ESTADO.yo = yo; entrar(); }).catch(() => { if (!ESTADO.yo) pintarLogin(); });
})();
