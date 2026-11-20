// Media Syntaxis Streamer - hub de señales: lo común a sus secciones (cabecera, navegación, datos en
// vivo y piezas de pantalla). Va después de app.js, del que usa $, $$, esc, num, yo, flujos, vista,
// abrirFlujo, refrescar, seguirLienzo, cargarHls e inactivo, y antes de las secciones (hub-*.js), que
// se apuntan en SEC. Arranca con iniciarHub(), que llama app.js cuando ya sabe quién mira.

const SEC = {};        // sección → {pinta(), entra()?}
const ACC = {};        // acción (data-a de un botón o data-envia de un formulario) → función(elemento, evento)
const CAMBIA = {};     // formulario (data-form) → función(campo, valor, elemento), al escribir en él
const SECCIONES = ['fuentes', 'destinos', 'rutas', 'monitoreo', 'adaptadores', 'ajustes', 'flujos'];
const H = {
  m: null,             // lo último de /api/matriz
  carga: null,         // lo último de /api/carga
  sec: null,           // sección a la vista
  puede: new Set(),    // permisos de quien mira
  fallo: '',           // por qué no hay datos nuevos
  hist: {},            // «f:<fuente>» o «r:<destino>» → [[t, kb/s]…] del último minuto
  pruebas: {},         // id → última prueba de conexión; {probando: true} mientras corre
  claves: {},          // id → {dir, hasta}: una dirección con sus claves, a la vista un minuto
  adapt: null,         // lo último de /api/adaptadores
  pulsando: 0,         // desde cuándo hay un botón del ratón o un dedo apretado
  pendiente: false,    // quedó algo sin pintar por no quitarle el suelo a una pulsación
  foco: null,          // {ruta: destino} | {fuente: id}: lo que Monitoreo debe abrir al entrar, pedido desde otra sección
};
const CLAVES_A_LA_VISTA = 60000;

// ---------------------------------------------------------------- pedir
async function pide(metodo, ruta, cuerpo) {
  let r;
  try {
    r = await fetch(ruta.replace(/^\//, ''), {method: metodo, headers: {'Content-Type': 'application/json'},
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo)});
  } catch { throw new Error('El panel no responde'); }
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) { location.href = d.login || '/login?volver=/streamer/'; throw new Error('Sesión caducada'); }
  if (!r.ok) {
    // el detalle puede ser un texto, la lista de un campo mal escrito o un objeto con lo que hay que confirmar
    const det = d.detail, objeto = det && typeof det === 'object' && !Array.isArray(det);
    const e = new Error(Array.isArray(det) ? det.map(x => x.msg).join('; ') : objeto ? det.mensaje || 'Error ' + r.status : det || 'Error ' + r.status);
    e.estado = r.status; e.detalle = objeto ? det : null;
    throw e;
  }
  return d;
}

// ---------------------------------------------------------------- pintar
// Cada trozo de pantalla se repinta entero cuando cambia su HTML. No se repinta bajo una pulsación
// (el clic se perdería al cambiar el botón de debajo) ni con texto seleccionado dentro: queda
// pendiente. Se conservan el foco (data-k) y el desplazamiento lateral (data-desliza).
const apretado = () => H.pulsando && Date.now() - H.pulsando < 3000;
function poner(el, html, forzar = false) {        // forzar: lo pide una acción de quien mira, y se pinta siempre
  if (typeof el === 'string') el = $(el);
  if (!el) return;
  if (!forzar) {
    if (el._html === html) return;
    const sel = getSelection();
    if (apretado() || (sel && !sel.isCollapsed && el.contains(sel.anchorNode))) { H.pendiente = true; return; }
  }
  const foco = el.contains(document.activeElement) ? document.activeElement : null;
  const k = foco?.dataset.k, ini = foco?.selectionStart, fin = foco?.selectionEnd;
  const lados = [...el.querySelectorAll('[data-desliza]')].map(x => [x.dataset.desliza, x.scrollLeft]);
  el.innerHTML = html; el._html = html;
  for (const [d, x] of lados) { const n = el.querySelector(`[data-desliza="${d}"]`); if (n) n.scrollLeft = x; }
  if (k) {
    const n = el.querySelector(`[data-k="${CSS.escape(k)}"]`);
    if (n) { n.focus({preventScroll: true}); if (ini != null) try { n.setSelectionRange(ini, fin); } catch {} }
  }
}
function pintarTodo() { pintarCabecera(); pintarSeccion(); }
function pintarSeccion() { if (H.sec !== 'flujos') SEC[H.sec]?.pinta(); }

// ---------------------------------------------------------------- textos
const ahora = () => H.m?.t ?? Date.now() / 1000;          // la hora de la estación, no la del navegador
function hace(t) {
  if (!t) return 'nunca';
  const s = Math.max(0, ahora() - t);
  if (s < 10) return 'ahora';
  if (s < 90) return `hace ${Math.round(s / 5) * 5} s`;
  if (s < 5400) return `hace ${Math.round(s / 60)} min`;
  if (s < 129600) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
}
function hora(t, segundos = true) {
  if (!t) return '—';
  const d = new Date(t * 1000), dia = x => x.toLocaleDateString('en-CA');
  const h = d.toLocaleTimeString('es', {hour: '2-digit', minute: '2-digit', ...(segundos ? {second: '2-digit'} : {})});
  return dia(d) === dia(new Date(ahora() * 1000)) ? h : d.toLocaleDateString('es', {day: 'numeric', month: 'short'}) + ' ' + h;
}
const cuando = t => { const h = hora(t, false); return h.includes(' ') ? `el ${h}` : `a las ${h}`; };     // «a las 17:00» o «el 19 nov 17:00»
function dur(s) {
  s = Math.max(0, Math.round(s));
  if (s < 90) return s + ' s';
  if (s < 5400) return Math.round(s / 60) + ' min';
  if (s < 172800) return `${Math.floor(s / 3600)} h ${String(Math.round(s % 3600 / 60)).padStart(2, '0')} min`;
  return `${Math.floor(s / 86400)} d ${Math.round(s % 86400 / 3600)} h`;
}
const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
const etq = (cls, txt, titulo) => `<span class="etq ${cls || ''}"${titulo ? ` title="${esc(titulo)}"` : ''}>${esc(txt)}</span>`;
const tiene = p => H.puede.has(p);
// NDI y Zixi van por un adaptador: mientras no esté habilitado, lo suyo se guarda y espera
const adaptadorListo = p => !H.m?.protocolos[p]?.adaptador || !!H.adapt?.adaptadores?.[p]?.listo;
async function leerAdaptadores() {
  try { H.adapt = await pide('GET', '/api/adaptadores'); } catch { return; }
  pintarSeccion();
}
// lo que le falta a un botón que quien mira no puede usar
const sinPermiso = (p, que) => tiene(p) ? '' : ` disabled title="Tu rol (${esc(yo.rol)}) no puede ${esc(que)}"`;

// ---------------------------------------------------------------- estados
const BOLA = {recibiendo: 'ok', enviando: 'ok', reconectando: 'av', error: 'er', adaptador: 'mo'};
// El estado de una fuente tal como se enseña. Con el adaptador sin habilitar no es un error de
// operación: es «adaptador pendiente», con lo que le falta.
function estadoFuente(f) {
  const e = f.estado || {estado: 'esperando_fuente', nombre: 'Esperando fuente', detalle: '', evidencia: []};
  if (f.adaptador && !f.adaptador.listo) return {...e, cls: 'adaptador', nombre: 'Adaptador pendiente', detalle: f.adaptador.falta};
  return {...e, cls: e.estado};
}
// El de un destino es el de su ruta. Sin ruta no tiene estado de operación: está libre.
function estadoDestino(d) {
  if (d.adaptador && !d.adaptador.listo) return {cls: 'adaptador', nombre: 'Adaptador pendiente', detalle: d.adaptador.falta, evidencia: []};
  if (!d.ruta) return {cls: 'apagado', nombre: 'Sin ruta', detalle: 'Ninguna fuente está conectada a este destino', evidencia: []};
  return {...d.ruta.estado, cls: d.ruta.estado.estado, sin_confirmar: d.ruta.sin_confirmar};
}
const bola = e => `<span class="pt ${BOLA[e.cls] || ''}"></span>`;
// un «enviando» que nadie confirma (un reenvío WHIP) lo dice a su lado
const etqEstado = e => etq(e.cls, e.nombre, e.detalle) + (e.sin_confirmar ? ' ' + etq('pendiente', 'Sin confirmar', 'El hub envía, pero el otro equipo no informa de si la recibe') : '');
function evidenciaHtml(e) {
  const filas = (e.evidencia || []).map(x => `<li>${esc(x.que)}${x.desde ? ` <span class="gr">desde las ${hora(x.desde)}</span>` : ''}</li>`).join('');
  return `<div class="evid"><p>${esc(e.detalle || '')}</p>${filas ? `<ul>${filas}</ul>` : ''}
    ${e.actualizado ? `<p class="xs gr">Estado comprobado a las ${hora(e.actualizado)} (${hace(e.actualizado)})</p>` : ''}</div>`;
}
function pistasTexto(p) {            // [vídeo, audio] de una fuente, para una línea de tabla
  if (!p) return ['—', '—'];
  const a = p.audio || [], textos = [...new Set(a.map(x => x.texto))];
  return [p.video ? p.video.texto : 'Sin vídeo',
    !a.length ? 'Sin audio' : a.length === 1 ? a[0].texto : `${a.length} pistas: ${textos.join(', ')}`];
}
const nombrePista = p => p.nombre_pista + (p.nombre && !p.nombre_pista.includes(p.nombre) ? ` (${p.nombre})` : p.idioma && !p.nombre_pista.includes(p.idioma) ? ` (${p.idioma})` : '');

// ---------------------------------------------------------------- avisos, confirmaciones y portapapeles
let avisoTimer = null;
function decir(texto, tipo = 'error') {
  const d = $('#h-dlg'), p = d.open && d.querySelector('.pie-aviso') || $('#h-aviso');
  clearTimeout(avisoTimer);
  p.textContent = texto || ''; p.className = 'aviso ' + tipo; p.hidden = !texto;
  if (texto && p.id) avisoTimer = setTimeout(() => { p.hidden = true; }, tipo === 'ok' ? 6000 : 15000);
}
// Pregunta en un diálogo y devuelve si se aceptó. El foco empieza en «Cancelar». Con `antes`, al
// aceptar se llama a esa función con el diálogo: si lanza un error, se enseña y el diálogo sigue
// abierto (para los formularios, que así no pierden lo escrito).
function preguntar({titulo, html = '', aceptar = 'Aceptar', clase = 'pri', cancelar = 'Cancelar', ancho = false, antes = null}) {
  const d = $('#h-dlg');
  d.innerHTML = `<h3>${esc(titulo)}</h3><div class="cuerpo">${html}</div><p class="aviso error pie-aviso" role="alert" hidden></p>
    <div class="pie"><button class="bt" type="button" data-dlg="no">${esc(cancelar)}</button>${aceptar ? `<button class="bt ${clase}" type="button" data-dlg="si">${esc(aceptar)}</button>` : ''}</div>`;
  d.classList.toggle('ancho', ancho);
  return new Promise(ok => {
    const fin = v => { d.removeEventListener('close', alCerrar); d.onclick = null; if (d.open) d.close(); ok(v); };
    // el aviso de cierre llega con retraso: si es el de un diálogo anterior y este ya está abierto, no cuenta
    const alCerrar = () => { if (!d.open) fin(false); };
    d.addEventListener('close', alCerrar);
    d.onclick = async ev => {
      const b = ev.target.closest('[data-dlg]');
      if (!b || b.disabled) return;
      if (b.dataset.dlg !== 'si' || !antes) return fin(b.dataset.dlg === 'si');
      const aviso = d.querySelector('.pie-aviso');
      b.disabled = true; aviso.hidden = true;
      try { await antes(d); fin(true); }
      catch (e) { aviso.textContent = e.message; aviso.hidden = false; b.disabled = false; }
    };
    d.showModal();
    (d.querySelector('[autofocus]') || d.querySelector('[data-dlg="no"]')).focus();
  });
}
// Un formulario en un diálogo cuyo envío puede cortar rutas al aire. `leer(dialogo)` saca lo escrito
// (y puede rechazarlo con un error) y `enviar(valores, corta)` hace la petición. Si el hub contesta
// que el cambio corta rutas al aire, se enseñan y, si se confirma, se repite diciendo cuáles.
async function dialogoAjuste({titulo, html, aceptar = 'Guardar', ancho = false, leer, enviar}) {
  let valores, corte = null, hecho = true;
  const ok = await preguntar({titulo, html, aceptar, ancho, antes: async d => {
    valores = leer(d);
    try { hecho = await enviar(valores); }
    catch (e) { if (e.estado === 409 && e.detalle?.corta) corte = e.detalle; else throw e; }
  }});
  if (!ok) return null;
  if (corte) {
    const lista = corte.al_aire.map(a => `<li>«${esc(a.fuente_nombre)}» hacia «${esc(a.destino_nombre)}» <span class="gr">(${esc(a.estado_nombre.toLowerCase())})</span></li>`).join('');
    if (!await preguntar({titulo: 'Este cambio corta lo que está al aire', clase: 'peligro', aceptar: 'Cambiar y cortar',
      html: `<div class="alaire" style="margin-top:0">Estas rutas se cortan y quien esté conectado tendrá que volver a conectar:<ul>${lista}</ul></div>`})) return null;
    hecho = await enviar(valores, corte.corta);
  }
  return hecho ?? true;
}
async function copiar(texto, boton) {
  try { await navigator.clipboard.writeText(texto); }
  catch {
    const t = document.createElement('textarea'), d = $('#h-dlg');
    t.value = texto; t.style.cssText = 'position:fixed;opacity:0';
    (d.open ? d : document.body).append(t); t.select();
    let bien = false;
    try { bien = document.execCommand('copy'); } catch {}
    t.remove();
    if (!bien) return decir('No se pudo copiar: selecciona el texto y cópialo a mano');
  }
  if (boton) {
    const antes = boton.textContent;
    boton.textContent = 'Copiado';
    setTimeout(() => { if (boton.isConnected) boton.textContent = antes; }, 1500);
  }
}
ACC.copiar = el => copiar(el.dataset.t, el);

// ---------------------------------------------------------------- direcciones y claves
// La dirección que el hub entrega (a un codificador o a un receptor) o a la que el hub llama. Las
// claves van ocultas: «Mostrar claves» las pide, queda anotado y se ven un minuto. Las de los
// equipos ajenos no vuelven nunca: solo se dice si hay una guardada.
const lineaDir = (rotulo, url) => `<div class="rotulo" style="margin-top:12px">${esc(rotulo)}</div>
  <div class="dir"><code>${esc(url)}</code><button class="bt chico" type="button" data-a="copiar" data-t="${esc(url)}">Copiar</button></div>`;
function claveVista(id) {
  const c = H.claves[id];
  if (c && c.hasta > Date.now()) return c.dir;
  delete H.claves[id];
  return null;
}
function direccionHtml(lado, o) {
  const dir = claveVista(o.id) || o.direccion, fuente = lado === 'fuente';
  let h = '';
  if (dir) {
    h += lineaDir(fuente ? 'Dirección para el codificador' : 'Dirección para el receptor', dir.url);
    if (dir.url_rtmps) h += lineaDir('La misma, por RTMPS', dir.url_rtmps);
    if (dir.pagina) h += lineaDir('Página para abrirla en un navegador', dir.pagina);
    h += '<div style="margin-top:10px">' + (dir.campos || []).map(c => `<div class="par"><span>${esc(c.nombre)}</span><span>${esc(c.valor)}</span></div>`).join('') + '</div>';
    const datos = `data-lado="${lado}" data-id="${esc(o.id)}"`;
    if (!dir.protegida) h += `<p class="ayuda">Esta dirección no lleva clave: la abre quien llegue a ella.</p>`;
    else if (dir.con_claves) h += `<p class="ayuda">Las claves están a la vista durante un minuto. La consulta queda en el registro de cambios.</p>`;
    else if (tiene('ver_claves')) h += `<div class="fila" style="margin-top:10px">
        <button class="bt chico" type="button" data-a="claves" ${datos} data-k="claves:${esc(o.id)}">Mostrar claves</button>
        ${tiene('editar') && !o.heredada ? `<button class="bt chico" type="button" data-a="clave-nueva" ${datos}>Cambiar la clave</button>` : ''}
        <span class="xs gr">Las claves van ocultas. Verlas queda anotado.</span></div>`;
    else h += `<p class="ayuda">Las claves van ocultas y tu rol (${esc(yo.rol)}) no puede verlas.</p>`;
  }
  const r = o.remoto;
  if (r) {
    h += lineaDir(fuente ? 'El hub llama a esta dirección' : 'El hub envía a esta dirección', r.url);
    const guardadas = [r.tiene_streamid && 'un stream ID', r.tiene_passphrase && 'una passphrase', r.tiene_clave && 'una clave',
      r.url_con_claves && 'una clave dentro de la dirección'].filter(Boolean);
    h += `<p class="ayuda">${guardadas.length ? `Hay guardada ${guardadas.join(' y ')}. ` : ''}Las claves del otro equipo se escriben al editar y no se vuelven a enseñar.</p>`;
  }
  return h;
}
ACC.claves = async el => {
  const {lado, id} = el.dataset;
  const dir = await pide('GET', `/api/${lado === 'fuente' ? 'fuentes' : 'destinos'}/${id}/claves`);
  H.claves[id] = {dir, hasta: Date.now() + CLAVES_A_LA_VISTA};
  setTimeout(pintarSeccion, CLAVES_A_LA_VISTA + 100);
  pintarSeccion();
};
ACC['clave-nueva'] = async el => {
  const {lado, id} = el.dataset, fuente = lado === 'fuente';
  const o = (fuente ? H.m.fuentes : H.m.destinos).find(x => x.id === id);
  if (!o) return;
  const aire = alAire(fuente ? {fuente: id} : {destino: id});
  if (!await preguntar({titulo: `Cambiar la clave de «${o.nombre}»`, aceptar: 'Cambiar la clave', clase: 'peligro',
    html: `<p>${fuente ? 'Quien esté publicando sigue hasta que reconecte; desde entonces necesita la clave nueva.'
      : 'Quien esté recibiendo sigue hasta que reconecte; desde entonces necesita la clave nueva.'}</p>${avisoAire(aire)}`})) return;
  const dir = await pide('POST', `/api/${fuente ? 'fuentes' : 'destinos'}/${id}/clave-nueva`);
  H.claves[id] = {dir, hasta: Date.now() + CLAVES_A_LA_VISTA};
  setTimeout(pintarSeccion, CLAVES_A_LA_VISTA + 100);
  decir('Clave cambiada. La nueva está a la vista durante un minuto.', 'ok');
  await leerMatriz();
};

// Las rutas al aire que toca un cambio: [destino…] de la matriz, por su fuente o por su destino.
function alAire({fuente, destino}) {
  return (H.m?.destinos || []).filter(d => d.ruta?.al_aire && (fuente ? d.ruta.fuente === fuente : d.id === destino));
}
function avisoAire(destinos) {
  if (!destinos.length) return '';
  const lista = destinos.map(d => `<li>«${esc(d.ruta.fuente_nombre)}» → «${esc(d.nombre)}» (${esc(d.ruta.estado.nombre.toLowerCase())})</li>`).join('');
  return `<div class="alaire"><b>Está al aire.</b> Este cambio corta o interrumpe:<ul>${lista}</ul>
    ${tiene('modificar_al_aire') ? '' : `Tu rol (${esc(yo.rol)}) no puede modificar lo que está al aire: el hub lo rechazará.`}</div>`;
}

// ---------------------------------------------------------------- pruebas de conexión
function pruebaHtml(r) {
  if (!r) return '';
  if (r.probando) return `<div class="prueba"><div class="linea1">${etq('', 'Probando')}<span class="gr">El hub está comprobando la conexión…</span></div></div>`;
  if (r.error) return `<div class="prueba"><div class="linea1">${etq('mal', 'No se pudo probar')}<span>${esc(r.error)}</span></div></div>`;
  const pasos = (r.pasos || []).map(p => `<li><i class="${p.ok == null ? 'na' : p.ok ? 'si' : 'no'}">${p.ok == null ? '·' : p.ok ? '✓' : '✕'}</i><b>${esc(p.que)}</b>
    <span>${esc(p.detalle || '')}${p.ms != null ? ` (${Math.round(p.ms)} ms)` : ''}</span></li>`).join('');
  return `<div class="prueba"><div class="linea1">${etq(r.ok ? 'bien' : 'mal', r.ok ? 'Correcta' : 'No pasa')}
    <span class="gr">Prueba de las ${hora(r.t)}</span><span>${esc(r.resumen || '')}</span></div>${pasos ? `<ul>${pasos}</ul>` : ''}</div>`;
}
async function probar(id, pedir) {            // corre una prueba y deja su resultado en H.pruebas[id]
  H.pruebas[id] = {probando: true};
  pintarSeccion();
  try { H.pruebas[id] = await pedir(); }
  catch (e) { H.pruebas[id] = {error: e.message}; }
  pintarSeccion();
  return H.pruebas[id];
}

// ---------------------------------------------------------------- formularios
// Lo escrito se guarda en el estado del formulario al teclear (CAMBIA), y el formulario solo se
// repinta cuando cambia su estructura: así una lectura de datos no pisa lo que se está escribiendo.
// botones: [[acción, texto]…] junto al campo («Mostrar», «Generar»).
function campo(c, rotulo, valor, o = {}) {
  const botones = (o.botones || []).map(([a, t]) => `<button class="bt" type="button" data-a="${a}" data-c="${c}">${esc(t)}</button>`).join('');
  const attrs = [`type="${o.tipo || 'text'}"`, `data-c="${c}"`, `data-k="c:${c}"`, `value="${esc(valor ?? '')}"`,
    o.pon && `placeholder="${esc(o.pon)}"`, o.min != null && `min="${o.min}"`, o.max != null && `max="${o.max}"`,
    o.largo && `maxlength="${o.largo}"`, o.fijo && 'disabled', `autocomplete="${o.tipo === 'password' ? 'new-password' : 'off'}"`, 'spellcheck="false"'];
  const entrada = `<input ${attrs.filter(Boolean).join(' ')}>`;
  return `<label class="campo${o.ancho ? ' ancho' : ''}"><span>${esc(rotulo)}</span>
    ${botones ? `<span class="con-boton">${entrada}${botones}</span>` : entrada}${o.ayuda ? `<small>${esc(o.ayuda)}</small>` : ''}</label>`;
}
function seleccion(c, rotulo, valor, opciones, o = {}) {      // opciones: [[valor, texto]…]
  return `<label class="campo${o.ancho ? ' ancho' : ''}"><span>${esc(rotulo)}</span>
    <select data-c="${c}" data-k="c:${c}"${o.fijo ? ' disabled' : ''}>${opciones.map(([v, t]) => `<option value="${esc(v)}"${String(v) === String(valor) ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>
    ${o.ayuda ? `<small>${esc(o.ayuda)}</small>` : ''}</label>`;
}
ACC.ojo = el => {                    // enseña u oculta lo escrito en un campo de clave
  const i = el.parentElement.querySelector('input'), ver = i.type === 'password';
  i.type = ver ? 'text' : 'password';
  el.textContent = ver ? 'Ocultar' : 'Mostrar';
};
function fraseNueva(n = 24) {        // una passphrase al azar, de letras y cifras que no se confunden al dictarlas
  const letras = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789', azar = crypto.getRandomValues(new Uint8Array(n * 2));
  let s = '';
  for (const b of azar) { if (b < 224 && s.length < n) s += letras[b % 56]; }
  return s.padEnd(n, 'x');
}
const entero = v => v === '' || v == null || !/^\d+$/.test(String(v).trim()) ? null : Number(v);
// Quién llama a quién y hacia dónde va la señal, dibujado. haciaDerecha: quien inicia está a la izquierda.
const FLECHA = der => `<svg viewBox="0 0 80 12" aria-hidden="true"><path d="${der ? 'M0 6h74M67 1l8 5-8 5' : 'M80 6H6M13 1L5 6l8 5'}" fill="none" stroke="currentColor" stroke-width="2"/></svg>`;
function esquemaHtml(izq, der, conexionDer, textoConexion, textoSenal) {
  const caja = ([rot, txt]) => `<div class="dato txt"><b>${esc(rot)}</b><span>${esc(txt)}</span></div>`;
  return `<div class="esquema">${caja(izq)}<div class="flechas">
      <div style="color:var(--suave)"><b>Conexión</b>${FLECHA(conexionDer)}<span style="color:var(--texto)">${esc(textoConexion)}</span></div>
      <div style="color:var(--acento)"><b>Señal</b>${FLECHA(true)}<span style="color:var(--texto)">${esc(textoSenal)}</span></div>
    </div>${caja(der)}</div>`;
}
const equipoHub = () => 'Hub en ' + (H.carga?.equipo?.nombre || 'esta estación');

// ---------------------------------------------------------------- vista previa
// La señal por HLS a través del panel (hls/<id>/), con el reproductor de MediaMTX. Un solo
// reproductor a la vez, sin sonido hasta que se pida, y parado con la pestaña oculta, fuera de su
// sección o tras un rato sin tocar la página. caja: el .previa a la vista; base: «hls/<id>/» o nada.
const VP = {caja: null, base: null, video: null, hls: null, timer: null, espera: 0, gen: 0, sonido: false, sinCodec: false};
function previa(caja, base) {
  const ver = caja && base && H.sec !== 'flujos' && document.visibilityState === 'visible' && !inactivo();
  if (!ver) { pararPrevia(); VP.caja = VP.base = null; return; }
  if (VP.caja === caja && VP.base === base) return;
  pararPrevia();
  VP.caja = caja; VP.base = base; VP.espera = 0; VP.sinCodec = false;
  arrancarPrevia();
}
function pararPrevia() {
  VP.gen++;
  clearTimeout(VP.timer); VP.timer = null;
  if (VP.hls) { try { VP.hls.destroy(); } catch {} VP.hls = null; }
  if (VP.video) { VP.video.removeAttribute('src'); try { VP.video.load(); } catch {} VP.video.remove(); VP.video = null; }
  VP.caja?.classList.remove('viva');
}
function arrancarPrevia() {
  const gen = ++VP.gen, caja = VP.caja, base = VP.base;
  const v = VP.video = document.createElement('video');
  v.muted = !VP.sonido; v.autoplay = true; v.playsInline = true; v.tabIndex = -1;
  v.setAttribute('disablepictureinpicture', '');
  caja.prepend(v);
  const url = new URL(base + 'index.m3u8', location.href).href;
  const fallo = () => {
    if (gen !== VP.gen) return;
    pararPrevia();
    VP.espera = Math.min((VP.espera || 1000) * 2, 10000);
    VP.timer = setTimeout(() => { if (VP.caja === caja && VP.base === base) arrancarPrevia(); }, VP.espera);
  };
  v.addEventListener('playing', () => { if (gen === VP.gen) { caja.classList.add('viva'); VP.espera = 0; } });
  v.addEventListener('error', fallo);
  const tocar = () => v.play()?.catch(() => { if (gen === VP.gen && !v.muted) { v.muted = true; VP.sonido = false; pintarSonido(); v.play()?.catch(() => {}); } });
  if (v.canPlayType('application/vnd.apple.mpegurl') && !(window.MediaSource || window.ManagedMediaSource)) { v.src = url; tocar(); return; }
  cargarHls(base).then(Hls => {
    if (gen !== VP.gen) return;
    if (!Hls.isSupported()) { VP.sinCodec = true; return pararPrevia(); }
    const h = VP.hls = new Hls({lowLatencyMode: true, maxBufferLength: 4, maxMaxBufferLength: 8, backBufferLength: 0, maxLiveSyncPlaybackRate: 1.5});
    h.on(Hls.Events.ERROR, (_, d) => {
      if (!d.fatal || gen !== VP.gen) return;
      if (/IncompatibleCodecs|AddCodec/i.test(d.details)) { VP.sinCodec = true; pararPrevia(); pintarSeccion(); } else fallo();
    });
    h.on(Hls.Events.MANIFEST_PARSED, tocar);
    h.loadSource(url);
    h.attachMedia(v);
  }, fallo);
}
function pintarSonido() {
  for (const b of $$('#hub .previa .sonido')) b.textContent = VP.sonido ? 'Quitar el sonido' : 'Oír el sonido';
}
ACC.sonido = () => {
  VP.sonido = !VP.sonido;
  if (VP.video) { VP.video.muted = !VP.sonido; if (VP.sonido) VP.video.play()?.catch(() => {}); }
  pintarSonido();
};
// El recuadro de una vista previa: se monta una vez y luego solo cambian sus textos y su clase.
const previaHtml = id => `<div class="previa" id="${id}"><span class="t1"></span><span class="t2"></span>
  <button class="bt chico sonido" type="button" data-a="sonido">Oír el sonido</button></div>`;
function textoPrevia(caja, cls, t1, t2) {
  caja.className = 'previa ' + (cls || '') + (caja.classList.contains('viva') ? ' viva' : '');
  caja.querySelector('.t1').textContent = t1 || '';
  caja.querySelector('.t2').textContent = VP.sinCodec && VP.caja === caja ? 'Este navegador no puede reproducir esta señal' : t2 || '';
}

// ---------------------------------------------------------------- medidores de audio
// Un juego de medidores a la vez, como la vista previa: los de la fuente que se está mirando. Las
// barras se montan cuando cambian las pistas y después solo se mueven: una por canal, con el pico de
// la última décima de segundo, de −60 a 0 dB. Pedir los niveles mantiene encendido el medidor de la
// estación; al dejar de pedirlos se apaga solo.
const NV = {caja: null, id: null, datos: null, timer: null};
function medidores(caja, f) {          // f: la fuente cuyas pistas se miden; sin caja, se deja de medir
  if (caja && f) {
    const pistas = f.pistas?.audio || [];
    const firma = JSON.stringify([f.id, !!f.pistas, pistas.map(p => [p.pista, p.texto, p.canales, nombrePista(p)])]);
    if (caja._firma !== firma) {
      caja._firma = firma;
      const estereo = pistas.every(p => p.canales === 2);
      caja.innerHTML = !pistas.length ? `<p class="ch gr">${f.pistas ? 'La señal no trae audio.' : 'No hay audio que medir mientras no llegue señal.'}</p>`
        : pistas.map(p => `<div class="pista" data-p="${p.pista}"><div class="ley"><span><b>${esc(nombrePista(p))}</b> · ${esc(p.texto)}</span><span class="pico">—</span></div>
            ${'<div class="barra"><span style="width:0"></span></div>'.repeat(Math.max(1, Math.min(p.canales || 2, 8)))}</div>`).join('')
          + `<div class="escala"><span>−60 dB</span><span>${estereo ? 'Izquierdo arriba, derecho abajo' : 'Una barra por canal'}</span><span>0 dB</span></div>
             <p class="ayuda nivel-nota"></p>`;
    }
  } else if (caja && caja._firma !== '') { caja._firma = ''; caja.innerHTML = '<p class="ch gr">No hay audio que medir mientras no llegue señal.</p>'; }
  const id = caja && f && f.con_senal && (f.pistas?.audio || []).length && !inactivo() ? f.id : null;
  if (id !== NV.id || caja !== NV.caja) {
    NV.id = id; NV.caja = caja; NV.datos = null;
    clearInterval(NV.timer); NV.timer = null;
    if (id) {
      const lee = async () => {
        if (document.visibilityState !== 'visible' || !NV.caja?.offsetParent) return;      // la caja no está a la vista: no se pide
        try { const d = await pide('GET', `/api/fuentes/${id}/niveles`); if (NV.id === id) { NV.datos = d; moverMedidores(); } }
        catch (e) { if (NV.id === id) { NV.datos = {error: e.message}; moverMedidores(); } }
      };
      lee();
      NV.timer = setInterval(lee, 1000);
    }
  }
  moverMedidores();
}
function moverMedidores() {
  const n = NV.datos, caja = NV.caja;
  if (!caja) return;
  const nota = caja.querySelector('.nivel-nota');
  if (nota) nota.textContent = !NV.id ? 'Los medidores se encienden cuando hay señal.' : !n ? '' : n.error || (n.arrancando ? 'Encendiendo los medidores…' : '');
  for (const pista of caja.querySelectorAll('.pista')) {
    const p = n?.pistas?.find(x => String(x.pista) === pista.dataset.p);
    let tope = null;
    pista.querySelectorAll('.barra span').forEach((b, i) => {
      const db = p?.canales?.[i]?.pico ?? null;
      b.style.width = db == null ? '0' : Math.max(0, Math.min(100, (db + 60) / 60 * 100)) + '%';
      b.className = db == null ? '' : db >= -3 ? 'tope' : db >= -9 ? 'alto' : '';
      if (db != null && (tope == null || db > tope)) tope = db;
    });
    pista.querySelector('.pico').textContent = tope == null ? '—' : tope <= -100 ? 'Silencio' : `−${Math.abs(Math.round(tope))} dB de pico`;
  }
}

// ---------------------------------------------------------------- cabecera
function pintarModulos() {
  // la misma botonera que el Renderer: solo los módulos que quien mira tiene en su hoja de usuarios
  const base = (yo.recorder_url || '').replace(/\/+$/, ''), mods = yo.modulos, admin = yo.rol === 'admin';
  const botones = yo.via !== 'recorder' ? [] : [
    (admin || !mods || mods.includes('recorder')) && ['Recorder', base + '/#recorder'],
    mods?.includes('showeditor') && ['Show Editor', base + '/#editor'],
    yo.renderer_url && (admin || mods?.includes('renderer')) && ['Renderer', yo.renderer_url + '/'],
    ['Streamer', ''],
    admin && ['System Monitor', base + '/#sysmon'],
  ].filter(Boolean);
  const nav = $('#modulos');
  nav.hidden = botones.length < 2;
  nav.innerHTML = botones.map(([t, u]) => u ? `<a href="${esc(u)}">${t}</a>` : `<a href="#fuentes" aria-current="page">${t}</a>`).join('');
}
function pintarCabecera() {
  const m = H.m, el = $('#resumen');
  if (!m) return poner(el, H.fallo ? `<span class="mal">${esc(H.fallo)}</span>` : 'Cargando…');
  // una ruta guardada que espera a su adaptador no cuenta como activa
  const s = m.sistema, n = m.destinos.filter(d => d.ruta && estadoDestino(d).cls !== 'adaptador').length, avisos = Object.keys(s.problemas || {}).length;
  const partes = [
    !s.mediamtx ? '<span class="mal">MediaMTX no responde</span>' : !s.supervisor ? '<span class="mal">El supervisor no responde</span>'
      : s.reposo ? '<span class="mal">Servicio apagado</span>' : 'Motor en marcha',
    plural(n, 'ruta activa', 'rutas activas'),
    H.carga?.cpu_pct != null && `CPU ${Math.round(H.carga.cpu_pct)} %`,
    avisos && `<a class="mal" href="#ajustes">${plural(avisos, 'aviso', 'avisos')} del motor</a>`,
    H.fallo && `<span class="mal">sin datos nuevos: ${esc(H.fallo)}</span>`,
  ].filter(Boolean);
  poner(el, partes.join(' · '));
  const av = $('#av-reposo');
  av.hidden = !s.reposo;
  if (s.reposo) av.textContent = 'El servicio de esta estación está apagado desde el System Monitor del Recorder'
    + (s.reposo.por ? ` (${s.reposo.por})` : '') + ': no sale ninguna señal. Al encenderlo vuelven solos los flujos y las rutas que estaban activos.';
}

// ---------------------------------------------------------------- secciones
// La sección va en el hash. Un hash que es el id de un flujo abre ese flujo, como siempre.
function irA() {
  const h = decodeURIComponent(location.hash.slice(1));
  const flujo = !SECCIONES.includes(h) && flujos.some(f => f.id === h) ? h : null;
  const sec = flujo ? 'flujos' : SECCIONES.includes(h) ? h : 'fuentes', cambia = sec !== H.sec;
  H.sec = sec;
  for (const a of $$('#secciones a')) a.dataset.sec === sec ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current');
  $('#hub').hidden = sec === 'flujos';
  $('#clasico').hidden = sec !== 'flujos';
  for (const s of SECCIONES) { const el = $('#s-' + s); if (el) el.hidden = s !== sec; }
  decir('');
  if (sec === 'flujos') {
    previa(null);
    if (flujo) abrirFlujo(flujo); else vista('flujos');
    refrescar();
  } else {
    seguirLienzo();                                 // el lienzo de los flujos se para al salir de ellos
    if (cambia) {                                   // la vista previa y los medidores son de la sección que se deja
      previa(null); medidores(null);
      SEC[sec]?.entra?.(); window.scrollTo(0, 0);
    }
    pintarSeccion();
  }
}

// ---------------------------------------------------------------- datos en vivo
let leyendo = false, vueltas = 0;
async function leerMatriz() {
  if (leyendo) return;
  leyendo = true;
  try {
    const m = await pide('GET', '/api/matriz');
    H.m = m; H.puede = new Set(m.permisos); H.fallo = '';
    const pon = (k, v) => { const h = H.hist[k] ||= []; h.push([m.t, v || 0]); while (h[0][0] < m.t - 62) h.shift(); };
    for (const f of m.fuentes) pon('f:' + f.id, f.kbps);
    for (const d of m.destinos) if (d.ruta) pon('r:' + d.id, d.ruta.metricas?.kbps); else delete H.hist['r:' + d.id];
  } catch (e) { H.fallo = e.message; }
  leyendo = false;
  pintarTodo();
}
async function leerCarga() {
  try { H.carga = await pide('GET', '/api/carga'); } catch { return; }
  pintarCabecera();
  if (H.sec === 'monitoreo') pintarSeccion();
}
function tic() {
  if (document.visibilityState !== 'visible') return;
  vueltas++;
  if (H.sec === 'flujos' && vueltas % 5) return;    // con los flujos a la vista, solo para la cabecera
  leerMatriz();
  if (!H.carga || vueltas % (H.sec === 'monitoreo' ? 2 : 4) === 0) leerCarga();
}

// ---------------------------------------------------------------- arranque
function iniciarHub() {
  pintarModulos();
  pide('GET', '/api/sistema').then(s => { $('#marca-modulo').title = 'Streamer ' + s.version; }, () => {});
  addEventListener('pointerdown', () => { H.pulsando = Date.now(); }, true);
  for (const t of ['pointerup', 'pointercancel']) addEventListener(t, () => {
    H.pulsando = 0;
    if (H.pendiente) { H.pendiente = false; setTimeout(pintarTodo, 0); }
  }, true);
  const hub = $('#hub');
  hub.addEventListener('click', ev => {
    const el = ev.target.closest('[data-a]');
    if (!el || el.disabled || !ACC[el.dataset.a]) return;
    ev.preventDefault();
    getSelection()?.removeAllRanges();              // un texto seleccionado no debe dejar sin pintar lo que la acción cambie
    Promise.resolve().then(() => ACC[el.dataset.a](el, ev)).catch(e => decir(e.message));
  });
  hub.addEventListener('submit', ev => {
    ev.preventDefault();
    const f = ACC[ev.target.dataset.envia];
    if (f) Promise.resolve().then(() => f(ev.target, ev)).catch(e => decir(e.message));
  });
  for (const t of ['input', 'change']) hub.addEventListener(t, ev => {
    const el = ev.target, form = el.closest?.('[data-form]');
    if (!form || !el.dataset.c || !CAMBIA[form.dataset.form]) return;
    CAMBIA[form.dataset.form](el.dataset.c, el.type === 'checkbox' ? el.checked : el.value, el, t);
  });
  addEventListener('hashchange', irA);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') tic(); else previa(null); });
  irA();
  leerMatriz();
  leerCarga();
  leerAdaptadores();
  setInterval(tic, 2000);
}
