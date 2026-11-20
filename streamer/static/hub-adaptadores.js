// Media Syntaxis Streamer - hub de señales: Adaptadores. El motor (MediaMTX y FFmpeg), los
// adaptadores de NDI y de Zixi con lo que le falta a cada uno, y hasta dónde está comprobada cada
// forma de entrar al hub con cada forma de salir.

const AD = {val: null, t: 0, comprobando: false};
const NIVEL_CORTO = {real: 'Equipos reales', laboratorio: 'Banco de pruebas', pendiente: 'Pendiente'};
const TEXTO_ADAPTADOR = {
  ndi: 'Recibe fuentes NDI de la red local y publica salidas del hub como fuentes NDI con el nombre que elijas.',
  zixi: 'Recibe y envía contribuciones Zixi a través del componente de Zixi instalado junto al hub.',
};

async function leerValidacion() {
  try { AD.val = await pide('GET', '/api/validacion'); } catch { return; }
  if (H.sec === 'adaptadores') SEC.adaptadores.pinta();
}
SEC.adaptadores = {
  entra() { AD.t = Date.now(); leerAdaptadores(); leerValidacion(); },
  pinta() {
    const s = $('#s-adaptadores');
    if (!s._montada) {
      s._montada = true;
      s.innerHTML = `<div class="vista">
        <p class="ch gr" style="margin:0;max-width:90ch">SRT, RTMP, HLS y WebRTC funcionan sin ningún adaptador. NDI y Zixi se habilitan por separado: si falta uno, sus fuentes y destinos quedan en espera y el resto del hub sigue operando.</p>
        <div class="pares">${['mtx', 'ffmpeg', 'ndi', 'zixi'].map(k => `<div class="caja" id="ad-${k}"></div>`).join('')}</div>
        <div class="caja" id="ad-val"></div></div>`;
    }
    if (Date.now() - AD.t > 30000) { AD.t = Date.now(); leerAdaptadores(); leerValidacion(); }
    const a = H.adapt, m = H.m;
    if (!a || !m) { poner('#ad-mtx', `<div class="vacio">${esc(H.fallo || 'Cargando…')}</div>`); return; }
    const par = (k, v) => `<div class="par"><span>${k}</span><span>${v}</span></div>`;
    const cab = (titulo, chip, lado) => `<div class="cab"><h2 class="grande">${titulo}</h2>${chip}<span class="hueco"></span>${lado}</div>`;

    // el motor
    const sis = m.sistema, entran = m.fuentes.filter(f => f.con_senal).length, salen = m.destinos.filter(d => d.ruta?.al_aire).length;
    poner('#ad-mtx', `${cab('MediaMTX', a.mediamtx.en_marcha ? etq('bien', 'En marcha') : etq('mal', 'No responde'), '<span class="ch gr">Núcleo de enrutamiento</span>')}
      <p class="ch gr" style="margin:0 0 10px">Recibe y entrega las señales por SRT, RTMP, HLS y WebRTC sin tocar el vídeo ni el audio.</p>
      ${par('Versión', esc(a.mediamtx.version || '—'))}
      ${par('En marcha desde', a.mediamtx.desde ? `${hora(a.mediamtx.desde, false)} <span class="gr">(${dur(ahora() - a.mediamtx.desde)})</span>` : '—')}
      ${par('Supervisor del hub', sis.supervisor ? 'En marcha: vigila cada proceso y lo relanza si cae' : '<span style="color:#ff8a9a">No responde: nada se relanza si cae</span>')}
      ${par('Entradas y salidas abiertas', `${plural(entran, 'entrada con señal', 'entradas con señal')} · ${plural(salen, 'salida al aire', 'salidas al aire')}`)}`);
    const cod = a.ffmpeg.codificador, gpu = (H.carga?.gpu || [])[0];
    poner('#ad-ffmpeg', `${cab('FFmpeg', a.ffmpeg.version ? etq('bien', 'Disponible') : etq('mal', 'No se encuentra'), '<span class="ch gr">Conversiones</span>')}
      <p class="ch gr" style="margin:0 0 10px">Convierte solo lo que un destino no acepta y comparte cada conversión entre las rutas que la piden.</p>
      ${par('Versión', esc(a.ffmpeg.version || '—'))}
      ${par('Codificador de vídeo', cod === 'h264_nvenc' ? `NVENC en la GPU${gpu ? ` (${esc(gpu.nombre)})` : ''}` : cod === 'libx264' ? 'x264 en la CPU' : cod ? esc(cod) : 'Se decide con la primera conversión de vídeo')}
      ${par('Cómo se elige', 'El hub prueba NVENC al necesitar la primera conversión; si la GPU no codifica, usa x264 en la CPU')}
      ${par('Audio', 'AAC y Opus')}`);

    for (const [k, x] of Object.entries(a.adaptadores)) pintarAdaptador(k, x, cab);
    pintarValidacion(a);
  },
};

function pintarAdaptador(k, x, cab) {
  const esp = x.en_espera || {fuentes: [], destinos: []}, espera = [esp.fuentes.length && plural(esp.fuentes.length, 'fuente', 'fuentes'), esp.destinos.length && plural(esp.destinos.length, 'destino', 'destinos')].filter(Boolean);
  const listas = (x.variantes || []).map(v => [v.nombre, `Recibir: ${v.recibir}. Publicar: ${v.publicar}.`]).concat((x.modos || []).map(v => [v.nombre, v.detalle]));
  poner('#ad-' + k, `${cab(`Adaptador ${esc(x.nombre)}`, x.listo ? etq('bien', 'Habilitado') : etq('adaptador', 'No habilitado'),
      `<button class="bt chico" type="button" data-a="ad-comprobar" data-k="ad-comprobar-${k}"${AD.comprobando ? ' disabled' : ''}${sinPermiso('ajustes', 'comprobar los adaptadores')}>Comprobar de nuevo</button>`)}
    <p class="ch gr" style="margin:0 0 4px">${TEXTO_ADAPTADOR[k] || ''}</p>
    <p class="ch" style="margin:0"><span class="gr">Componente que requiere:</span> ${esc(x.componente || '—')}</p>
    <h3 class="sub">${x.listo ? 'Requisitos' : 'Requisitos pendientes'}</h3>
    <ol class="requisitos">${(x.requisitos || []).map(r => `<li><b>${esc(r.que)}</b> ${r.ok ? etq('bien', 'Listo') : etq('regular', 'Pendiente')}<small>${esc(r.detalle || '')}</small></li>`).join('')}</ol>
    ${listas.length ? `<h3 class="sub">${k === 'zixi' ? 'Modos y límites de la licencia' : 'Variantes'}</h3>${listas.map(([n, t]) => `<div class="par"><span>${esc(n)}</span><span>${esc(t)}</span></div>`).join('')}` : ''}
    <p class="ayuda" style="margin-top:10px">Última comprobación: ${hora(x.comprobado)}${espera.length ? ` · ${espera.join(' y ')} en espera: ${esc([...esp.fuentes, ...esp.destinos].join(', '))}` : ''}.
      ${x.listo && x.validacion !== 'real' ? ' Habilitado, pero sin validar con equipos reales: mira la tabla de abajo.' : ''}</p>`);
}
ACC['ad-comprobar'] = async () => {
  AD.comprobando = true; SEC.adaptadores.pinta();
  try { H.adapt = await pide('POST', '/api/adaptadores/comprobar'); decir('Adaptadores comprobados ahora.', 'ok'); }
  finally { AD.comprobando = false; AD.t = Date.now(); await leerMatriz(); if (H.sec === 'adaptadores') SEC.adaptadores.pinta(); }
};

// ---------------------------------------------------------------- validación de rutas
// Una casilla por cada forma de entrar con cada forma de salir. Lo que pide un adaptador sin
// habilitar no se puede ni probar: se dice aparte.
function faltaAdaptador(c, a) { return c.adaptador.length > 0 && Object.values(a.adaptadores).some(x => c.adaptador.includes(x.nombre) && !x.listo); }
function pintarValidacion(a) {
  const v = AD.val;
  if (!v) return poner('#ad-val', '<h2>Validación de rutas</h2><div class="vacio">Cargando…</div>');
  const filas = [], cols = [], de = {};
  for (const c of v.combinaciones) {
    if (!filas.some(x => x[0] === c.fuente)) filas.push([c.fuente, c.fuente_texto]);
    if (!cols.some(x => x[0] === c.destino)) cols.push([c.destino, c.destino_texto]);
    de[c.fuente + '>' + c.destino] = c;
  }
  const cabeza = t => { const [p, ...r] = t.split(' · '); return `<b>${esc(p)}</b>${r.length ? `<small>${esc(r.join(' · '))}</small>` : ''}`; };
  const celdas = filas.map(([f, tf]) => `<div class="vl-h">${cabeza(tf)}</div>` + cols.map(([d, td]) => {
    const c = de[f + '>' + d], falta = c.nivel === 'pendiente' && faltaAdaptador(c, a), cls = falta ? 'adaptador' : c.nivel === 'pendiente' ? 'apagado' : c.nivel;
    const dicho = falta ? 'requiere un adaptador que aún no está habilitado' : c.nombre.toLowerCase() + (c.t ? ` ${cuando(c.t)}` : '');
    return `<button class="vl ${cls}" type="button" data-a="ad-celda" data-f="${esc(f)}" data-d="${esc(d)}" data-k="vl:${esc(f)}>${esc(d)}" title="Entrada ${esc(tf)}. Salida ${esc(td)}: ${esc(dicho)}">${falta ? 'Adaptador' : NIVEL_CORTO[c.nivel]}</button>`;
  }).join('')).join('');
  const cuenta = n => v.combinaciones.filter(c => c.nivel === n).length;
  poner('#ad-val', `<div class="cab"><h2>Validación de rutas</h2><span class="leyenda">${etq('real', v.niveles.real)}${etq('laboratorio', v.niveles.laboratorio)}${etq('apagado', v.niveles.pendiente)}${etq('adaptador', 'Requiere un adaptador sin habilitar')}</span></div>
    <p class="ch gr" style="margin:0 0 12px;max-width:100ch">Cada casilla dice hasta dónde se ha probado esa forma de entrada con esa forma de salida. «Banco de pruebas» es el laboratorio: señales de prueba y programas que hacen de cámara y de receptor.
      «Equipos reales» lo anota quien lo comprueba con la cámara, el receptor o la plataforma de verdad, con el equipo usado y el resultado.
      Hoy hay ${cuenta('real')} con equipos reales, ${cuenta('laboratorio')} en el banco y ${cuenta('pendiente')} pendientes. ${tiene('ajustes') ? 'Pulsa una casilla para anotar una validación.' : 'Pulsa una casilla para ver su detalle.'}</p>
    <div class="desliza" data-desliza="val"><div class="vl-mz" style="--n:${cols.length}">
      <div class="vl-h esq"><small>Entrada \\ Salida</small></div>${cols.map(([, t]) => `<div class="vl-h col">${cabeza(t)}</div>`).join('')}${celdas}</div></div>`);
}
ACC['ad-celda'] = async el => {
  const c = AD.val?.combinaciones.find(x => x.fuente === el.dataset.f && x.destino === el.dataset.d);
  if (!c) return;
  const puede = tiene('ajustes'), falta = faltaAdaptador(c, H.adapt);
  const html = `<div class="par" style="border-top:0"><span>Entrada</span><span>${esc(c.fuente_texto)}</span></div>
    <div class="par"><span>Salida</span><span>${esc(c.destino_texto)}</span></div>
    <div class="par"><span>Hoy</span><span>${etq(c.nivel === 'pendiente' ? 'apagado' : c.nivel, c.nombre)}${c.t ? ` <span class="gr">${cuando(c.t)}${c.quien ? ` por ${esc(c.quien)}` : ''}</span>` : ''}</span></div>
    ${c.nota ? `<p class="ch" style="margin:8px 0 0">${esc(c.nota)}</p>` : ''}
    ${falta ? `<p class="ch morado" style="margin:8px 0 0">Requiere el adaptador ${esc(c.adaptador.join(' y '))}, que aún no está habilitado: no se puede probar todavía.</p>` : ''}
    ${puede ? `<h3 class="sub">Anotar una validación</h3>
      <div class="campos"><label class="campo ancho"><span>Nivel</span><select data-c="nivel">
          <option value="real">${esc(AD.val.niveles.real)}</option><option value="laboratorio">${esc(AD.val.niveles.laboratorio)}</option><option value="pendiente">${esc(AD.val.niveles.pendiente)}</option></select></label>
        <label class="campo ancho"><span>Con qué equipo se comprobó, cuándo y con qué resultado</span><textarea data-c="nota" rows="3" maxlength="500" placeholder="Cámara y codificador, receptor o plataforma, fecha y lo que se vio y se oyó"></textarea>
          <small>Obligatoria para «equipos reales». Una validación con equipos reales no la rebaja una prueba de banco: para retirarla, anótala como pendiente.</small></label></div>` : ''}`;
  if (!puede) return preguntar({titulo: 'Validación de esta forma de conexión', html, aceptar: '', cancelar: 'Cerrar'});
  let anotado = true;
  const ok = await preguntar({titulo: 'Validación de esta forma de conexión', html, aceptar: 'Anotar', antes: async d => {
    const nivel = d.querySelector('[data-c="nivel"]').value, nota = d.querySelector('[data-c="nota"]').value.trim();
    if (nivel === 'real' && !nota) throw new Error('Anota con qué equipo se comprobó');
    anotado = (await pide('PUT', '/api/validacion', {fuente: c.fuente, destino: c.destino, nivel, nota})).anotado;
  }});
  if (!ok) return;
  decir(anotado ? 'Validación anotada.' : 'No se ha cambiado: ya estaba verificada con equipos reales.', anotado ? 'ok' : 'error');
  await leerValidacion();
};
