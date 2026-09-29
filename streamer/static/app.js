// Media Syntaxis Streamer - panel
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const num = n => n == null ? '—' : n >= 1000 ? (n / 1000).toFixed(1).replace('.', ',') + ' Mb/s' : Math.round(n) + ' kb/s';

let flujos = [], actual = null, paso = 'entrada', editandoSalida = null, canales = [];

async function api(metodo, ruta, cuerpo) {
  // rutas relativas: el panel funciona igual en :8095 que tras Caddy en /streamer/
  const r = await fetch(ruta.replace(/^\//, ''), {method: metodo, headers: {'Content-Type': 'application/json'},
    body: cuerpo ? JSON.stringify(cuerpo) : undefined});
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) { location.href = d.login || '/login?volver=/streamer/'; throw new Error('Sesión caducada'); }
  if (!r.ok) {
    const det = d.detail;
    throw new Error(Array.isArray(det) ? det.map(x => x.msg).join('; ') : det || 'Error ' + r.status);
  }
  return d;
}

const ESTADO_FLUJO = {
  en_el_aire: ['ok', 'En el aire'], esperando: ['av', 'Esperando señal'],
  detenido: ['off', 'Detenido'], sin_mediamtx: ['er', 'MediaMTX no responde'],
};
const ESTADO_SALIDA = {
  conectada: ['ok', 'Conectada'], publicando: ['ok', 'Publicando'], conectando: ['av', 'Conectando'],
  esperando: ['av', 'Esperando entrada'], reintentando: ['er', 'Reintentando'], detenida: ['off', 'Detenida'],
};
const pill = ([tono, txt]) => `<span class="pill ${tono}">${esc(txt)}</span>`;
const punto = e => ({conectada: '', publicando: '', conectando: 'av', esperando: 'av', reintentando: 'er', detenida: 'off'}[e] ?? 'off');

function describirEntrada(f) {
  return f.entrada === 'recorder' ? `Recorder · ${f.canal}` : `SRT pull · ${f.url.replace(/^srt:\/\//, '')}`;
}

// ---------------------------------------------------------------- navegación
function vista(v) {
  $('#v-flujos').hidden = v !== 'flujos';
  $('#v-editor').hidden = v !== 'editor';
  if (v === 'flujos') { actual = null; history.replaceState(null, '', '#'); pintarLista(); }
  seguirLienzo();
  window.scrollTo(0, 0);
}
function irPaso(p) {
  paso = p;
  $$('.pasos button').forEach(b => b.setAttribute('aria-selected', b.dataset.paso === p));
  ['entrada', 'comp', 'salidas', 'registro'].forEach(x => $('#p-' + x).hidden = x !== p);
  if (p === 'registro') cargarRegistro();
  seguirLienzo();
}
$$('[data-ir]').forEach(b => b.onclick = () => vista(b.dataset.ir));
$$('.pasos button').forEach(b => b.onclick = () => {
  if (!actual && b.dataset.paso !== 'entrada') return;   // primero hay que guardar la entrada
  irPaso(b.dataset.paso);
});

// ---------------------------------------------------------------- lista
function pintarLista() {
  const cont = $('#lista');
  if (!flujos.length) {
    cont.innerHTML = `<div class="panel vacio">Todavía no hay flujos.<br><button class="btn pri" onclick="nuevoFlujo()">Crear el primero</button></div>`;
    return;
  }
  cont.innerHTML = flujos.map(f => {
    const act = f.salidas.filter(s => ['conectada', 'publicando'].includes(s.estado)).length;
    return `<article class="panel flujo" tabindex="0" data-id="${esc(f.id)}">
      <div class="mini ${f.estado === 'en_el_aire' ? 'aire' : ''}">${f.estado === 'en_el_aire' ? 'Señal' : 'Sin señal'}</div>
      <div class="meta">
        <div class="fila"><h3>${esc(f.nombre)}</h3>${pill(ESTADO_FLUJO[f.estado])}</div>
        <div class="fila"><span class="chip"><b>ENTRADA</b> ${esc(describirEntrada(f))}</span>${f.pistas.length ? `<span class="chip"><b>PISTAS</b> ${esc(f.pistas.join(' · '))}</span>` : ''}</div>
        <div class="fila">${f.salidas.map(s => `<span class="chip"><i class="${punto(s.estado)}"></i>${esc(s.tipo.toUpperCase())} · ${esc(s.nombre)}</span>`).join('') || '<span class="nota">Sin salidas</span>'}</div>
      </div>
      <div class="datos"><div><small>Entrada</small><span class="num">${num(f.kbps)}</span></div><div><small>Salidas</small><span class="num">${act}/${f.salidas.length}</span></div></div>
    </article>`;
  }).join('');
  $$('#lista .flujo').forEach(a => {
    a.onclick = () => abrirFlujo(a.dataset.id);
    a.onkeydown = e => { if (e.key === 'Enter') abrirFlujo(a.dataset.id); };
  });
}

// ---------------------------------------------------------------- editor
function nuevoFlujo() {
  actual = null;
  $('#e-titulo').textContent = 'Nuevo flujo';
  $('#e-sub').textContent = 'Elige de dónde llega la señal. Después podrás añadir los destinos.';
  $('#e-acc').innerHTML = ''; $('#e-stats').hidden = true;
  $('#f-flujo').reset(); $('#av-flujo').textContent = '';
  $('#b-guardar').textContent = 'Crear flujo';
  tipoEntrada('recorder');
  irPaso('entrada'); vista('editor'); cargarCanales();
  $('#i-nombre').focus();
}
$('#b-nuevo').onclick = nuevoFlujo;

function abrirFlujo(id) {
  actual = id; history.replaceState(null, '', '#' + id);
  const f = flujos.find(x => x.id === id);
  if (!f) return vista('flujos');
  $('#f-flujo').reset(); $('#av-flujo').textContent = '';
  $('#i-nombre').value = f.nombre;
  $('#i-url').value = f.url; $('#i-sid').value = f.streamid; $('#i-lat').value = f.latencia_ms;
  $('#i-pass').placeholder = f.tiene_passphrase ? 'Guardada (vacío = conservar)' : '';
  tipoEntrada(f.entrada, f.canal);
  $('#b-guardar').textContent = 'Guardar entrada';
  irPaso('salidas');
  vista('editor'); cargarCanales(); cargarComp(f); pintarEditor();
}

function pintarEditor() {
  const f = flujos.find(x => x.id === actual);
  if (!f) return;
  $('#e-titulo').textContent = f.nombre;
  $('#e-sub').textContent = describirEntrada(f);
  $('#e-acc').innerHTML = `${pill(ESTADO_FLUJO[f.estado])}
    <button class="btn" id="b-onoff" data-admin>${f.activo ? 'Detener flujo' : 'Iniciar flujo'}</button>
    <button class="btn peligro" id="b-borrar" data-admin>Borrar</button>`;
  $('#b-onoff').onclick = () => accion(`/api/flujos/${f.id}/${f.activo ? 'detener' : 'iniciar'}`);
  confirmarBorrado($('#b-borrar'), async () => { await api('DELETE', `/api/flujos/${f.id}`); await refrescar(); vista('flujos'); });
  const st = $('#e-stats'); st.hidden = false;
  st.innerHTML = `<div><small>Entrada</small><span class="num">${num(f.kbps)}</span></div>
    <div><small>Pistas</small><span>${esc(f.pistas.join(' · ') || '—')}</span></div>
    <div title="Lo que lee la entrada en MediaMTX: destinos, composición y reproductores HLS, también el lienzo y la vista previa de este panel, que cuentan hasta 1 min después de cerrarlos"><small>Lectores</small><span class="num">${f.lectores}</span></div>
    <div><small>Vista previa</small><span><a href="${esc(f.vista_previa)}" id="b-vista" title="Vista previa por el panel, con tu sesión" style="color:var(--acento2)">Abrir</a></span></div>`;
  $('#b-vista').onclick = async e => {
    e.preventDefault();
    const w = window.open('', '_blank');           // abrir ya: tras el await el navegador lo bloquearía
    try {
      const r = await api('POST', `/api/flujos/${f.id}/vista-previa`);
      abrirVista(w, r.url);
    } catch (err) { if (w) w.close(); alertaEditor(err.message); }
  };
  pintarSalidas(f);
  estadoComp(f);
  seguirLienzo();                    // el estado del flujo y de la composición decide qué se ve en el lienzo
}

function tipoEntrada(t, canal) {
  $$('input[name=entrada]').forEach(r => r.checked = r.value === t);
  $('#f-rec').hidden = t !== 'recorder'; $('#f-pull').hidden = t !== 'pull';
  if (canal !== undefined) $('#canales').dataset.elegido = canal;
}
$$('input[name=entrada]').forEach(r => r.onchange = () => tipoEntrada(r.value));

async function cargarCanales() {
  try { canales = await api('GET', '/api/recorder/canales'); } catch { canales = []; }
  const elegido = $('#canales').dataset.elegido || '';
  $('#canales').innerHTML = canales.length ? canales.map(c => `
    <button type="button" class="canal" data-id="${esc(c.id)}" aria-pressed="${c.id === elegido}">
      <b>${esc(c.id)}</b><small>${esc(c.nombre)}</small>${pill(c.publicando ? ['ok', 'Recibiendo'] : c.reenvio_udp ? ['av', 'Reenvío activado'] : ['off', 'Sin reenvío'])}
    </button>`).join('') : '<p class="nota">No se encontraron canales del Recorder en este equipo.</p>';
  $$('#canales .canal').forEach(b => b.onclick = () => {
    $('#canales').dataset.elegido = b.dataset.id;
    $$('#canales .canal').forEach(x => x.setAttribute('aria-pressed', x === b));
  });
}

$('#f-flujo').onsubmit = async e => {
  e.preventDefault();
  const av = $('#av-flujo'); av.className = 'aviso'; av.textContent = '';
  const entrada = $('input[name=entrada]:checked').value;
  const d = {nombre: $('#i-nombre').value.trim(), entrada, url: $('#i-url').value.trim(),
    streamid: $('#i-sid').value.trim(), passphrase: $('#i-pass').value,
    latencia_ms: +$('#i-lat').value || 400, canal: $('#canales').dataset.elegido || ''};
  if (entrada === 'recorder' && !d.canal) { av.className = 'aviso error'; av.textContent = 'Elige un canal del Recorder'; return; }
  try {
    if (actual) { await api('PUT', `/api/flujos/${actual}`, d); av.className = 'aviso ok'; av.textContent = 'Guardado'; }
    else { const r = await api('POST', '/api/flujos', d); actual = r.id; await refrescar(); abrirFlujo(r.id); irPaso('salidas'); return; }
    await refrescar();
  } catch (err) { av.className = 'aviso error'; av.textContent = err.message; }
};

// ---------------------------------------------------------------- salidas
function pintarSalidas(f) {
  const tb = $('#t-sal');
  if (!f.salidas.length) { tb.innerHTML = '<tr><td colspan="5" class="nota">Aún no hay destinos. Añade uno SRT, RTMP o HLS.</td></tr>'; return; }
  tb.innerHTML = f.salidas.map(s => {
    const detalle = s.tipo === 'srt' ? `${s.url} · ${s.modo} · ${s.latencia_ms} ms` : s.url;
    const enlace = s.tipo === 'hls'
      ? `<span class="num copiar" data-copiar="${esc(s.url_m3u8)}" title="Copiar">${esc(s.url_m3u8)}</span> · <a href="${esc(s.ver || s.url)}" target="_blank" rel="noopener" style="color:var(--acento2)">ver</a>`
      : `<span class="num">${esc(detalle)}</span>`;
    const err = s.estado === 'reintentando' && s.error ? `<div class="err">${esc(s.error)}</div>` : '';
    const rein = s.reinicios ? `<div class="nota">${s.reinicios} reinicio${s.reinicios > 1 ? 's' : ''}</div>` : '';
    return `<tr data-id="${esc(s.id)}">
      <td><span class="tipo">${esc(s.tipo.toUpperCase())}</span></td>
      <td>${esc(s.nombre)}${s.fuente === 'compuesta' ? ' <span class="chip"><b>COMPUESTA</b></span>' : ''}<br>${enlace}${err}</td>
      <td>${pill(ESTADO_SALIDA[s.estado])}${rein}</td>
      <td class="num">${s.tipo === 'hls' ? '—' : num(s.kbps)}</td>
      <td><div class="acciones-fila">
        <button class="btn peq" data-a="onoff">${s.activo ? 'Detener' : 'Iniciar'}</button>
        <button class="btn peq" data-a="editar" data-admin>Editar</button>
        <button class="btn peq peligro" data-a="borrar" data-admin>Borrar</button></div></td></tr>`;
  }).join('');
  $$('#t-sal tr[data-id]').forEach(tr => {
    const s = f.salidas.find(x => x.id === tr.dataset.id);
    tr.querySelector('[data-a=onoff]').onclick = () => accion(`/api/salidas/${s.id}/${s.activo ? 'detener' : 'iniciar'}`);
    tr.querySelector('[data-a=editar]').onclick = () => abrirDestino(s);
    confirmarBorrado(tr.querySelector('[data-a=borrar]'), async () => { await api('DELETE', `/api/salidas/${s.id}`); await refrescar(); });
  });
  $$('#t-sal [data-copiar]').forEach(el => el.onclick = () => {
    navigator.clipboard?.writeText(el.dataset.copiar).then(() => { el.title = 'Copiado'; }).catch(() => {
      const r = document.createRange(); r.selectNodeContents(el); getSelection().removeAllRanges(); getSelection().addRange(r);
    });
  });
}

function tipoDestino(t) {
  $$('.tipos button').forEach(b => b.setAttribute('aria-pressed', b.dataset.tipo === t));
  $$('#f-dest [data-t]').forEach(d => d.hidden = d.dataset.t !== t);
}
$$('.tipos button').forEach(b => b.onclick = () => tipoDestino(b.dataset.tipo));

function abrirDestino(s) {
  editandoSalida = s || null;
  $('#f-dest').reset(); $('#d-aviso').textContent = '';
  $('#d-titulo').textContent = s ? 'Editar destino' : 'Añadir destino';
  $('#d-nota').hidden = !s;
  $$('.tipos button').forEach(b => b.disabled = !!s && b.dataset.tipo !== s.tipo);
  if (s) {
    $('#d-nom').value = s.nombre; $('#d-fuente').value = s.fuente || 'limpia';
    if (s.tipo === 'srt') { $('#d-srturl').value = s.url; $('#d-modo').value = s.modo; $('#d-lat').value = s.latencia_ms; $('#d-sid').value = s.streamid; }
    if (s.tipo === 'rtmp') $('#d-rtmpurl').value = s.url;
  }
  tipoDestino(s ? s.tipo : 'srt');
  try { $('#dlg').showModal(); } catch { $('#dlg').setAttribute('open', ''); }
}
$('#b-add').onclick = () => abrirDestino(null);
$('#d-cancel').onclick = () => $('#dlg').close();
$('#f-dest').onsubmit = e => { e.preventDefault(); $('#d-ok').click(); };
$('#d-ok').onclick = async () => {
  const tipo = $('.tipos button[aria-pressed=true]').dataset.tipo;
  const d = {nombre: $('#d-nom').value.trim(), tipo, fuente: $('#d-fuente').value};
  if (tipo === 'srt') Object.assign(d, {url: $('#d-srturl').value.trim(), modo: $('#d-modo').value,
    latencia_ms: +$('#d-lat').value || 300, passphrase: $('#d-pass').value, streamid: $('#d-sid').value.trim()});
  if (tipo === 'rtmp') Object.assign(d, {url: $('#d-rtmpurl').value.trim(), clave: $('#d-clave').value.trim()});
  if (!d.nombre) { $('#d-aviso').textContent = 'Ponle un nombre al destino'; return; }
  try {
    if (editandoSalida) await api('PUT', `/api/salidas/${editandoSalida.id}`, d);
    else await api('POST', `/api/flujos/${actual}/salidas`, d);
    $('#dlg').close(); await refrescar();
  } catch (err) { $('#d-aviso').textContent = err.message; }
};

// ---------------------------------------------------------------- composición (fase 2)
const ESTADO_COMP = {
  componiendo: ['ok', 'Componiendo'], arrancando: ['av', 'Arrancando'], esperando: ['av', 'Esperando señal'],
  reintentando: ['er', 'Reintentando'], desactivada: ['off', 'Desactivada'],
};
const CAPA_EJEMPLO = 'http://127.0.0.1:8095/static/capa-ejemplo.html';
let comp = null;      // copia editable de la composición del flujo abierto

function cargarComp(f) {
  const k = f.composicion;
  comp = {activa: k.activa, x: k.x, y: k.y, ancho: k.ancho, fondo: k.fondo, kbps: k.kbps,
    capas: [0, 1].map(i => ({url: '', activa: true, encima: true, ...(k.capas[i] || {})}))};
  $('#av-comp').textContent = '';
  formComp();
}

function formComp() {
  $('#c-activa').checked = comp.activa;
  $('#c-ancho').value = comp.ancho; $('#c-x').value = comp.x; $('#c-y').value = comp.y;
  $('#c-fondo').value = comp.fondo; $('#c-kbps').value = comp.kbps;
  $('#c-capas').innerHTML = comp.capas.map((c, i) => `
    <div class="capa2" data-i="${i}">
      <div class="capa-cab"><span class="orden">${i + 1}</span><b style="flex:1">Capa ${i + 1}</b>
        <select data-k="encima" style="width:auto"><option value="1">Encima del vídeo</option><option value="0">Debajo del vídeo</option></select>
        <button type="button" class="btn peq" data-k="recargar" data-admin title="Vuelve a cargar la página sin cortar la señal">Recargar</button>
        <span class="sw" title="Capa visible (mostrar y ocultar no corta la señal)"><input type="checkbox" data-k="activa"><span></span></span></div>
      <input type="text" data-k="url" placeholder="https://… (vacía: sin capa)">
    </div>`).join('');
  $$('#c-capas .capa2').forEach(el => {
    const c = comp.capas[+el.dataset.i];
    el.querySelector('[data-k=url]').value = c.url;
    el.querySelector('[data-k=activa]').checked = c.activa;
    el.querySelector('[data-k=encima]').value = c.encima ? '1' : '0';
    el.querySelector('[data-k=url]').oninput = e => { c.url = e.target.value.trim(); lienzo(); };
    el.querySelector('[data-k=activa]').onchange = e => { c.activa = e.target.checked; lienzo(); };
    el.querySelector('[data-k=encima]').onchange = e => { c.encima = e.target.value === '1'; lienzo(); };
    // recargar vale para la capa ya guardada (la n-ésima con dirección, como la numera el servidor)
    const guardadas = (flujos.find(f => f.id === actual)?.composicion.capas || []).filter(x => x.url);
    const n = guardadas.findIndex(x => x.url === c.url) + 1;
    const b = el.querySelector('[data-k=recargar]');
    b.hidden = !c.url || !n;
    b.onclick = async () => {
      const av = $('#av-comp');
      try { await api('POST', `/api/flujos/${actual}/composicion/capas/${n}/recargar`); av.className = 'aviso ok'; av.textContent = `Capa ${i + 1}: recargando sin cortar la señal.`; }
      catch (err) { av.className = 'aviso error'; av.textContent = err.message; }
    };
  });
  lienzo();
}

function ajustar() {                 // igual que en el servidor: el vídeo cabe entero, medidas pares
  comp.ancho = Math.max(320, Math.min(1920, +comp.ancho || 1920)) & ~1;
  const alto = Math.round(comp.ancho * 9 / 16) & ~1;
  comp.x = Math.max(0, Math.min(1920 - comp.ancho, +comp.x || 0)) & ~1;
  comp.y = Math.max(0, Math.min(1080 - alto, +comp.y || 0)) & ~1;
  return alto;
}

function lienzo() {
  const alto = ajustar();
  $('#c-ancho').value = comp.ancho; $('#c-x').value = comp.x; $('#c-y').value = comp.y;
  $('#c-ancho-txt').textContent = `${comp.ancho}×${alto} · ${Math.round(comp.ancho / 19.2)} %`;
  const v = $('#c-video');
  Object.assign(v.style, {left: comp.x / 19.2 + '%', top: comp.y / 10.8 + '%', width: comp.ancho / 19.2 + '%', height: alto / 10.8 + '%'});
  $('#c-lienzo').style.background = comp.fondo;
  $$('#c-presets button').forEach(b => {
    const [x, y, w] = (flujos.find(f => f.id === actual)?.composicion.preajustes || {})[b.dataset.p] || [];
    b.setAttribute('aria-pressed', x === comp.x && y === comp.y && w === comp.ancho);
  });
  for (const lado of ['encima', 'debajo']) {
    const cont = $('#c-capas-' + lado);
    const urls = comp.capas.filter(c => c.activa && /^https?:\/\//.test(c.url) && (lado === 'encima') === c.encima).map(c => c.url);
    const actuales = [...cont.querySelectorAll('iframe')].map(f => f.dataset.url);
    if (urls.join('|') !== actuales.join('|')) {
      cont.innerHTML = urls.map(u => `<iframe data-url="${esc(u)}" src="${esc(vistaCapa(u))}" tabindex="-1" sandbox="allow-scripts allow-same-origin"></iframe>`).join('');
    }
  }
  escalarCapas();
}
// la capa de ejemplo se pide a 127.0.0.1 desde el Z8; en el navegador se ve desde este mismo panel
const vistaCapa = u => u.startsWith('http://127.0.0.1:8095/') ? u.replace('http://127.0.0.1:8095/', '') : u;
function escalarCapas() {
  const k = $('#c-lienzo').clientWidth / 1920;
  $$('#c-lienzo iframe').forEach(f => f.style.transform = `scale(${k})`);
}
new ResizeObserver(escalarCapas).observe($('#c-lienzo'));

$('#c-activa').onchange = e => { comp.activa = e.target.checked; };
$('#c-ancho').oninput = e => {           // al cambiar el ancho se mantiene el centro del vídeo
  const cx = comp.x + comp.ancho / 2, cy = comp.y + comp.ancho * 9 / 32;
  comp.ancho = +e.target.value; comp.x = Math.round(cx - comp.ancho / 2); comp.y = Math.round(cy - comp.ancho * 9 / 32);
  lienzo();
};
$('#c-x').onchange = e => { comp.x = +e.target.value; lienzo(); };
$('#c-y').onchange = e => { comp.y = +e.target.value; lienzo(); };
$('#c-fondo').oninput = e => { comp.fondo = e.target.value; lienzo(); };
$('#c-kbps').onchange = e => { comp.kbps = +e.target.value || 8000; };
$$('#c-presets button').forEach(b => b.onclick = () => {
  const p = (flujos.find(f => f.id === actual)?.composicion.preajustes || {})[b.dataset.p];
  if (p) { [comp.x, comp.y, comp.ancho] = p; lienzo(); }
});
$('#c-ejemplo').onclick = () => {
  const libre = comp.capas.findIndex(c => !c.url);
  const c = comp.capas[libre < 0 ? 0 : libre];
  Object.assign(c, {url: CAPA_EJEMPLO, activa: true, encima: true});
  if (JSON.stringify([comp.x, comp.y, comp.ancho]) === JSON.stringify([0, 0, 1920])) [comp.x, comp.y, comp.ancho] = [422, 0, 1498];
  formComp();
};
$('#f-comp').onsubmit = async e => {
  e.preventDefault();
  const av = $('#av-comp'); av.className = 'aviso'; av.textContent = '';
  const d = {...comp, capas: comp.capas.filter(c => c.url)};
  try {
    await api('PUT', `/api/flujos/${actual}/composicion`, d);
    av.className = 'aviso ok';
    av.textContent = d.activa ? 'Guardada. La composición arranca en unos segundos.' : 'Guardada (desactivada).';
    await refrescar();
  } catch (err) { av.className = 'aviso error'; av.textContent = err.message; }
};
$('#c-ver').onclick = async e => {
  e.preventDefault();
  const w = window.open('', '_blank');
  try {
    const r = await api('POST', `/api/flujos/${actual}/vista-previa?fuente=compuesta`);
    abrirVista(w, r.url);
  } catch (err) { if (w) w.close(); const av = $('#av-comp'); av.className = 'aviso error'; av.textContent = err.message; }
};
// la vista previa va por el panel (hls/<path>/, relativa): se resuelve contra esta página, que
// puede estar tras Caddy (/streamer/) o en la raíz de un túnel
function abrirVista(w, url) {
  const abs = new URL(url, location.href).href;
  if (w) { w.opener = null; w.location = abs; } else window.open(abs, '_blank', 'noopener');
}

// ---------------------------------------------------------------- lienzo en directo
// Mientras se ve el paso Composición (con un flujo abierto y la pestaña del navegador a la vista),
// el lienzo lleva vídeo en directo por el panel (hls/<path>/, como la vista previa). «Edición»: la
// entrada limpia en el recuadro del vídeo, con las capas encima o debajo como hasta ahora.
// «Resultado»: la señal compuesta real (comp_<flujo>) en todo el lienzo, solo con la composición
// activa. Un solo reproductor a la vez: al salir del paso, del flujo o de la pestaña, o al cambiar
// de flujo o de modo, se destruye y se cierra su conexión. También con el lienzo fuera de la
// pantalla (a los 2 s, para no rearrancar con cada vistazo) y tras INACTIVO_MAX sin tocar la página
// (hasta que se vuelva a usar): cada lienzo es la señal entera por el panel. Si falla o se para, se
// reintenta con espera creciente. HLS nativo donde lo hay (Safari); si no, hls.js, que sirve el
// propio MediaMTX y se carga una sola vez.
const MODO_LIENZO = 'msxs-lienzo';             // la elección se recuerda en cada navegador
const ESPERA_VIVO_MAX = 10000;
const INACTIVO_MAX = 10 * 60000;
let modoLienzo = 'edicion', modoDe = null;     // modo a la vista y flujo al que se aplicó la elección
let rp = null;                                 // reproductor en marcha: {video, hls, ctl, vigia, …}
let rpClave = null, rpGen = 0, rpEspera = 0, rpReintento = null, rpSinCodec = false, rpSono = false, rpBloqueado = false;
let sonando = false, nativoFallos = 0, cargaHls = null;
let enPantalla = true, fueraTimer = null, ultimoUso = Date.now(), dormido = false;
const lienzoVisible = () => paso === 'comp' && !!actual && !$('#v-editor').hidden && document.visibilityState === 'visible' && enPantalla;
function inactivo() {              // sin tocar la página INACTIVO_MAX: el lienzo se para hasta que se vuelva a usar
  if (!dormido && Date.now() - ultimoUso > INACTIVO_MAX) dormido = true;
  return dormido;
}
function usar() { ultimoUso = Date.now(); if (dormido) { dormido = false; seguirLienzo(); } }
['pointerdown', 'pointermove', 'keydown', 'wheel'].forEach(t => document.addEventListener(t, usar, {capture: true, passive: true}));
if (window.IntersectionObserver) new IntersectionObserver(es => {
  const dentro = es[es.length - 1].isIntersecting;
  clearTimeout(fueraTimer); fueraTimer = null;
  if (dentro && !enPantalla) { enPantalla = true; seguirLienzo(); }
  else if (!dentro && enPantalla) fueraTimer = setTimeout(() => { enPantalla = false; seguirLienzo(); }, 2000);
}).observe($('#c-lienzo'));

function leerModo() { try { return localStorage.getItem(MODO_LIENZO) === 'resultado' ? 'resultado' : 'edicion'; } catch { return 'edicion'; } }
function guardarModo(m) { try { localStorage.setItem(MODO_LIENZO, m); } catch {} }
$$('#c-modos button').forEach(b => b.onclick = () => {
  if (b.disabled || b.dataset.modo === modoLienzo) return;
  modoLienzo = b.dataset.modo; guardarModo(modoLienzo); seguirLienzo();
});
$('#c-lienzo').addEventListener('click', () => {    // el navegador no dejó arrancar solo: de cero, dentro del clic
  if (rpBloqueado) { rpBloqueado = false; seguirVivo(); }
});

function seguirLienzo() { seguirVivo(); seguirFoto(); }

function seguirVivo() {            // arranca, mantiene o para el reproductor según lo que está a la vista
  const f = lienzoVisible() ? flujos.find(x => x.id === actual) : null;
  const k = f?.composicion;
  if (f && modoDe !== f.id) {      // al abrir un flujo vale la elección guardada, si se puede ver
    modoDe = f.id;
    modoLienzo = leerModo() === 'resultado' && k.estado !== 'desactivada' ? 'resultado' : 'edicion';
  }
  if (k && k.estado === 'desactivada') modoLienzo = 'edicion';
  // Resultado se elige con la composición en marcha; ya elegido, aguanta sus rearranques (al guardar)
  const puede = !!k && (k.estado === 'componiendo' || (modoLienzo === 'resultado' && k.estado !== 'desactivada'));
  const bRes = $('#c-modos [data-modo=resultado]'), admin = yo.rol === 'admin';
  bRes.disabled = !puede;
  // «desactivada» también es un flujo detenido; el operador no puede activar ni iniciar nada
  bRes.title = puede ? 'La señal compuesta real, en directo'
    : k?.estado !== 'desactivada' ? 'Se podrá ver cuando la composición esté en marcha'
    : !f.activo ? (admin ? 'Inicia el flujo para ver el resultado' : 'Se podrá ver con el flujo en marcha')
    : admin ? 'Activa y guarda la composición para ver el resultado' : 'La composición está desactivada';
  $('#c-modo-nota').textContent = f && !puede ? bRes.title : '';
  $$('#c-modos button').forEach(b => b.setAttribute('aria-pressed', b.dataset.modo === modoLienzo));
  $('#c-lienzo').classList.toggle('resultado', modoLienzo === 'resultado');

  const res = modoLienzo === 'resultado', base = f && (res ? k.vista_previa : f.vista_previa);
  const clave = f ? `${f.id} ${modoLienzo} ${base}` : null;
  if (clave !== rpClave) { pararVivo(); rpClave = clave; rpEspera = 0; rpSinCodec = false; rpSono = false; rpBloqueado = false; }
  if (!f) return;
  if (!(res ? k.estado === 'componiendo' : f.estado === 'en_el_aire')) {    // no hay qué ver: sin peticiones
    pararVivo();
    return estadoVivo(res ? {esperando: 'Sin señal en la entrada', reintentando: 'La composición no arranca: mira su estado'}[k.estado]
        || (rpSono ? 'Reconectando…' : 'Conectando…')
      : {detenido: 'Flujo detenido', sin_mediamtx: 'MediaMTX no responde'}[f.estado] || 'Sin señal en la entrada');
  }
  if (inactivo()) { pararVivo(); return estadoVivo('En pausa por inactividad: pulsa en el lienzo para seguir en directo'); }
  if (rpBloqueado) return estadoVivo('Pulsa en el lienzo para verlo en directo');
  if (rpSinCodec) return estadoVivo(res ? 'Este navegador no puede reproducir la señal compuesta'
    : 'Este navegador no puede reproducir la entrada: se ve una foto cada 5 s');
  if (rp || rpReintento) return;                        // ya va, o espera para reintentar
  arrancarVivo(res ? $('#c-res-video') : $('#c-vivo'), base);
}

function arrancarVivo(video, base) {
  const gen = ++rpGen, r = rp = {video, hls: null, ctl: new AbortController(), ultimo: -1, quieto: 0, avances: 0};
  const url = new URL(base + 'index.m3u8', location.href).href;
  const fallo = () => { if (gen === rpGen) reintentarVivo(); };
  estadoVivo(rpEspera || rpSono ? 'Reconectando…' : 'Conectando…');
  video.muted = true;
  video.addEventListener('playing', () => { if (gen === rpGen) { ponerSonando(true); estadoVivo('En directo', true); } }, {signal: r.ctl.signal});
  video.addEventListener('error', fallo, {signal: r.ctl.signal});
  r.vigia = setInterval(() => vigilarVivo(r, fallo), 1000);
  // nativo solo mientras funcione: si falla dos veces sin llegar a verse, hls.js (si hay MSE)
  if (video.canPlayType('application/vnd.apple.mpegurl') && (nativoFallos < 2 || !(window.MediaSource || window.ManagedMediaSource))) {
    r.nativo = true; video.src = url; reproducir(video);
    return;
  }
  cargarHls(base).then(Hls => {
    if (gen !== rpGen) return;
    if (!Hls.isSupported()) return sinReproduccion();
    // HLS de baja latencia (el de MediaMTX) con poco búfer: basta con ver el directo
    const hls = r.hls = new Hls({lowLatencyMode: true, maxBufferLength: 4, maxMaxBufferLength: 8, backBufferLength: 0,
      maxLiveSyncPlaybackRate: 1.5});
    hls.on(Hls.Events.ERROR, (_, d) => {
      if (!d.fatal || gen !== rpGen) return;
      if (/IncompatibleCodecs|AddCodec/i.test(d.details)) sinReproduccion(); else fallo();
    });
    hls.on(Hls.Events.MANIFEST_PARSED, () => reproducir(video));
    hls.loadSource(url);
    hls.attachMedia(video);
  }, fallo);
}

function cargarHls(base) {         // hls.js del propio MediaMTX (hls/<path>/hls.min.js), una vez por página
  if (window.Hls) return Promise.resolve(window.Hls);
  if (!cargaHls) {
    const s = document.createElement('script');
    s.src = new URL(base + 'hls.min.js', location.href).href;
    cargaHls = new Promise((ok, mal) => {
      s.onload = () => window.Hls ? ok(window.Hls) : mal(new Error('hls.js'));
      s.onerror = mal;
    }).catch(err => { s.remove(); cargaHls = null; throw err; });      // la próxima vez, otra vez
    document.head.append(s);
  }
  return cargaHls;
}

function reproducir(v) {           // sin sonido se puede arrancar solo; si el navegador no deja, con un clic
  v.play()?.catch(err => {
    // no se queda cargando a la espera del clic: se suelta todo (conexión y sesión HLS) y al pulsar se
    // vuelve a empezar; la foto sigue de reserva en Edición
    if (err.name === 'NotAllowedError' && rp?.video === v) { pararVivo(); rpBloqueado = true; seguirVivo(); }
  });
}

function vigilarVivo(r, fallo) {   // cada segundo: ¿avanza el vídeo?
  const v = r.video;
  if (v.currentTime !== r.ultimo && v.readyState >= 2 && !v.paused) {
    r.ultimo = v.currentTime; r.quieto = 0; r.avances++;
    if (!sonando && r.avances >= 2) { ponerSonando(true); estadoVivo('En directo', true); }
    if (r.avances === 30) rpEspera = 0;            // medio minuto bien: la espera vuelve a empezar
    return;
  }
  if (++r.quieto === 3 && sonando) { ponerSonando(false); estadoVivo('Reconectando…'); }
  if (r.quieto >= (r.avances ? 8 : 15)) fallo();   // parado (o sin arrancar) demasiado tiempo
}

function reintentarVivo() {
  if (rp?.nativo && !rp.avances) nativoFallos++;
  pararVivo();
  rpEspera = Math.min(ESPERA_VIVO_MAX, Math.max(1000, rpEspera * 2));
  estadoVivo('Reconectando…');
  rpReintento = setTimeout(() => { rpReintento = null; seguirVivo(); }, rpEspera);
}

function sinReproduccion() {       // este navegador no puede con la señal: no se reintenta (en Edición, la foto)
  pararVivo();
  rpSinCodec = true;
  seguirVivo();
}

function pararVivo() {             // destruye el reproductor y cierra su conexión
  rpGen++;
  clearTimeout(rpReintento); rpReintento = null;
  if (rp) {
    const {video, hls, ctl, vigia} = rp;
    rp = null;
    clearInterval(vigia); ctl.abort();
    try { hls?.destroy(); } catch {}
    video.pause(); video.removeAttribute('src'); video.load();
  }
  ponerSonando(false);
  estadoVivo('');
}

function ponerSonando(s) {
  if (s === sonando) return;
  sonando = s;
  rpSono ||= s;
  $('#c-lienzo').classList.toggle('vivo', s);
  seguirFoto();                    // con el vídeo avanzando no hacen falta fotos; si se para, vuelven
}
function estadoVivo(txt, ok) { const n = $('#c-vivo-nota'); n.textContent = txt; n.classList.toggle('ok', !!ok); }
document.addEventListener('visibilitychange', () => {   // volver a la pestaña cuenta como usarla
  if (document.visibilityState === 'visible') { ultimoUso = Date.now(); dormido = false; }
  seguirLienzo();
});

// ---------------------------------------------------------------- foto de la entrada en el lienzo
// En Edición, mientras el vídeo en directo no avanza (arrancando, sin señal, reconectando o un
// navegador que no lo reproduce), el recuadro muestra una foto de la entrada limpia renovada cada
// 5 s. Con el vídeo avanzando no se piden fotos: cada una es otro lector de la señal. Sin foto (flujo
// parado o sin señal) queda el recuadro "VÍDEO". Los fallos se ignoran: se reintenta en la siguiente vuelta.
let fotoTimer = null, fotoDe = null, fotoPidiendo = false, fotoUrl = null;
const fotoVisible = () => lienzoVisible() && modoLienzo === 'edicion' && !sonando && !inactivo();
function ponerFoto(url) {
  const v = $('#c-video');
  v.style.backgroundImage = url ? `url("${url}")` : '';
  v.classList.toggle('foto', !!url);
  if (fotoUrl) URL.revokeObjectURL(fotoUrl);
  fotoUrl = url;
}
function seguirFoto() {           // arranca o para la foto según lo que está a la vista
  if (fotoDe !== actual) { ponerFoto(null); fotoDe = actual; clearTimeout(fotoTimer); fotoTimer = null; }
  if (!fotoVisible()) { clearTimeout(fotoTimer); fotoTimer = null; }
  else if (!fotoPidiendo && !fotoTimer) pedirFoto();
}
async function pedirFoto() {
  if (!fotoVisible()) { fotoTimer = null; return; }
  const fid = actual, inicio = Date.now(), ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 15000);
  fotoPidiendo = true;
  try {
    const r = await fetch(`api/flujos/${encodeURIComponent(fid)}/foto.jpg`, {cache: 'no-store', signal: ctl.signal});
    if (r.status === 204 || r.status === 404) { if (fid === fotoDe) ponerFoto(null); }
    else if (r.ok && (r.headers.get('content-type') || '').startsWith('image/')) {
      const url = URL.createObjectURL(await r.blob());
      const img = new Image(); img.src = url;
      try { await img.decode(); } catch { URL.revokeObjectURL(url); return; }
      if (fid === fotoDe) ponerFoto(url); else URL.revokeObjectURL(url);
    }
  } catch {} finally {
    clearTimeout(t); fotoPidiendo = false;
    clearTimeout(fotoTimer);         // la siguiente a los 5 s de pedir esta (enseguida si se cambió de flujo)
    fotoTimer = fotoVisible() ? setTimeout(pedirFoto, fid === actual ? Math.max(1000, 5000 - (Date.now() - inicio)) : 0) : null;
  }
}

function estadoComp(f) {
  const k = f.composicion;
  $('#c-estado').innerHTML = pill(ESTADO_COMP[k.estado] || ['off', k.estado]);
  const partes = [];
  if (k.estado === 'componiendo') partes.push(`${k.fps ? k.fps.toFixed(2).replace('.', ',') + ' fps' : ''}`, num(k.kbps_salida));
  if (k.reinicios) partes.push(`${k.reinicios} reinicio${k.reinicios > 1 ? 's' : ''}`);
  if (k.error) partes.push(k.error);
  $('#c-datos').textContent = partes.filter(Boolean).join(' · ');
}

// ---------------------------------------------------------------- utilidades
async function accion(ruta) {
  try { await api('POST', ruta); await refrescar(); } catch (err) { alertaEditor(err.message); }
}
function alertaEditor(txt) { const av = $('#av-flujo'); av.className = 'aviso error'; av.textContent = txt; irPaso('entrada'); }

function confirmarBorrado(boton, hacer) {
  // el visor no muestra confirm(): el botón pide un segundo clic
  let t;
  boton.onclick = async () => {
    if (!boton.classList.contains('confirmar')) {
      boton.classList.add('confirmar'); boton.dataset.txt = boton.textContent; boton.textContent = '¿Seguro? Borrar';
      t = setTimeout(() => { boton.classList.remove('confirmar'); boton.textContent = boton.dataset.txt; }, 4000);
      return;
    }
    clearTimeout(t);
    try { await hacer(); } catch (err) { alertaEditor(err.message); }
  };
}

async function cargarRegistro() {
  if (!actual) return;
  const ev = await api('GET', `/api/eventos?flujo=${encodeURIComponent(actual)}&limite=100`).catch(() => []);
  $('#reg').innerHTML = ev.length ? ev.map(e => `<div><time class="num">${new Date(e.t * 1000).toLocaleString('es')}</time><span>${esc(e.texto)}</span></div>`).join('')
    : '<p class="nota">Sin eventos todavía.</p>';
}

async function sistema() {
  try {
    const s = await api('GET', '/api/sistema');
    $('#sis-mtx').className = 'pill ' + (s.mediamtx ? 'ok' : 'er');
    const sup = s.supervisor_visto_s;
    $('#sis-sup').className = 'pill ' + (sup == null ? 'off' : sup < 10 ? 'ok' : 'er');
    $('#sis-sup').title = sup == null ? 'Sin datos del supervisor' : `Última revisión hace ${sup} s`;
  } catch {}
}

async function refrescar() {
  try { flujos = await api('GET', '/api/flujos'); } catch { return; }
  if ($('.confirmar') || $('#dlg').open) return;   // no repintar a mitad de una confirmación
  if ($('#v-editor').hidden) pintarLista();
  else if (actual) pintarEditor();
}

$('#b-salir').onclick = async () => { const r = await api('POST', '/api/salir'); location.href = r.login || '/login'; };
let yo = {rol: 'operador'};
async function cargarYo() {
  try { yo = await api('GET', '/api/yo'); } catch { return; }
  $('#quien').textContent = `${yo.email} · ${yo.rol}`;
  document.body.classList.toggle('operador', yo.rol !== 'admin');
  if (yo.via === 'recorder') { $('#lnk-recorder').href = yo.recorder_url; $('#lnk-recorder').hidden = false; }
}
(async () => {
  await cargarYo(); await refrescar(); sistema();
  const h = location.hash.slice(1);
  if (h && flujos.some(f => f.id === h)) abrirFlujo(h);
  setInterval(refrescar, 2000);
  setInterval(sistema, 5000);
  setInterval(() => { if (paso === 'registro' && actual) cargarRegistro(); }, 5000);
})();
