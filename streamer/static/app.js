// Media Syntaxis Streamer - panel
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const num = n => n == null ? '—' : n >= 1000 ? (n / 1000).toFixed(1).replace('.', ',') + ' Mb/s' : Math.round(n) + ' kb/s';

let flujos = [], actual = null, paso = 'entrada', editandoSalida = null, canales = [];

async function api(metodo, ruta, cuerpo) {
  const r = await fetch(ruta, {method: metodo, headers: {'Content-Type': 'application/json'},
    body: cuerpo ? JSON.stringify(cuerpo) : undefined});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sesión caducada'); }
  const d = await r.json().catch(() => ({}));
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
  window.scrollTo(0, 0);
}
function irPaso(p) {
  paso = p;
  $$('.pasos button').forEach(b => b.setAttribute('aria-selected', b.dataset.paso === p));
  ['entrada', 'comp', 'salidas', 'registro'].forEach(x => $('#p-' + x).hidden = x !== p);
  if (p === 'registro') cargarRegistro();
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
  vista('editor'); cargarCanales(); pintarEditor();
}

function pintarEditor() {
  const f = flujos.find(x => x.id === actual);
  if (!f) return;
  $('#e-titulo').textContent = f.nombre;
  $('#e-sub').textContent = describirEntrada(f);
  $('#e-acc').innerHTML = `${pill(ESTADO_FLUJO[f.estado])}
    <button class="btn" id="b-onoff">${f.activo ? 'Detener flujo' : 'Iniciar flujo'}</button>
    <button class="btn peligro" id="b-borrar">Borrar</button>`;
  $('#b-onoff').onclick = () => accion(`/api/flujos/${f.id}/${f.activo ? 'detener' : 'iniciar'}`);
  confirmarBorrado($('#b-borrar'), async () => { await api('DELETE', `/api/flujos/${f.id}`); await refrescar(); vista('flujos'); });
  const st = $('#e-stats'); st.hidden = false;
  st.innerHTML = `<div><small>Entrada</small><span class="num">${num(f.kbps)}</span></div>
    <div><small>Pistas</small><span>${esc(f.pistas.join(' · ') || '—')}</span></div>
    <div><small>Lectores</small><span class="num">${f.lectores}</span></div>
    <div><small>Vista previa</small><span><a href="${esc(f.vista_previa)}" target="_blank" rel="noopener" style="color:var(--acento2)">Abrir</a></span></div>`;
  pintarSalidas(f);
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
      ? `<span class="num copiar" data-copiar="${esc(s.url_m3u8)}" title="Copiar">${esc(s.url_m3u8)}</span> · <a href="${esc(s.url)}" target="_blank" rel="noopener" style="color:var(--acento2)">ver</a>`
      : `<span class="num">${esc(detalle)}</span>`;
    const err = s.estado === 'reintentando' && s.error ? `<div class="err">${esc(s.error)}</div>` : '';
    const rein = s.reinicios ? `<div class="nota">${s.reinicios} reinicio${s.reinicios > 1 ? 's' : ''}</div>` : '';
    return `<tr data-id="${esc(s.id)}">
      <td><span class="tipo">${esc(s.tipo.toUpperCase())}</span></td>
      <td>${esc(s.nombre)}<br>${enlace}${err}</td>
      <td>${pill(ESTADO_SALIDA[s.estado])}${rein}</td>
      <td class="num">${s.tipo === 'hls' ? '—' : num(s.kbps)}</td>
      <td><div class="acciones-fila">
        <button class="btn peq" data-a="onoff">${s.activo ? 'Detener' : 'Iniciar'}</button>
        <button class="btn peq" data-a="editar">Editar</button>
        <button class="btn peq peligro" data-a="borrar">Borrar</button></div></td></tr>`;
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
    $('#d-nom').value = s.nombre;
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
  const d = {nombre: $('#d-nom').value.trim(), tipo};
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
    $('#b-salir').hidden = !s.con_clave;
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

$('#b-salir').onclick = async () => { await api('POST', '/api/salir'); location.href = '/login'; };
(async () => {
  await refrescar(); sistema();
  const h = location.hash.slice(1);
  if (h && flujos.some(f => f.id === h)) abrirFlujo(h);
  setInterval(refrescar, 2000);
  setInterval(sistema, 5000);
  setInterval(() => { if (paso === 'registro' && actual) cargarRegistro(); }, 5000);
})();
