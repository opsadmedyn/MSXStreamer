// Media Syntaxis Streamer - hub de señales: Monitoreo. Qué entra, qué sale por cada ruta, con qué
// evidencia se dice cada estado y cuándo se comprobó, y la carga de la estación.

const MO = {sel: null, tareas: false};       // sel: {tipo: 'fuente' | 'ruta', id}; una ruta se nombra por su destino
const COLS_MONITOR = '150px minmax(0,1fr) 84px 96px';
const SIGNIFICA = [
  ['esperando_fuente', 'Esperando fuente', 'La entrada está preparada, pero nadie envía señal.'],
  ['recibiendo', 'Recibiendo medios', 'Llegan vídeo y audio válidos a la entrada.'],
  ['enviando', 'Enviando', 'La salida entrega señal y el receptor la acepta.'],
  ['esperando_receptor', 'Esperando receptor', 'La dirección está disponible, pero nadie la ha abierto.'],
  ['reconectando', 'Reconectando', 'Había señal y se cortó. El hub lo reintenta solo.'],
  ['error', 'Error', 'El hub dejó de intentarlo y hace falta que alguien intervenga.'],
  ['pendiente', 'Sin confirmar', 'El hub envía, pero el otro equipo no informa de si la recibe: hay que mirarlo allí.'],
  ['adaptador', 'Adaptador pendiente', 'Falta habilitar el adaptador de NDI o de Zixi. Lo guardado espera sin enviar.'],
];
const suma = lista => lista.reduce((a, b) => a + (b || 0), 0);
const gigas = mb => (mb / 1024).toFixed(1).replace('.', ',');

SEC.monitoreo = {
  entra() {                           // otra sección pidió abrir aquí una ruta o una fuente
    if (H.foco) MO.sel = H.foco.ruta ? {tipo: 'ruta', id: H.foco.ruta} : {tipo: 'fuente', id: H.foco.fuente};
    H.foco = null;
  },
  pinta() {
    const s = $('#s-monitoreo'), m = H.m;
    if (!s._montada) {
      s._montada = true;
      s.innerHTML = `<div class="vista"><div class="cols">
          <div class="vista" style="flex:999 1 620px"><div class="caja" id="mo-entradas"></div><div class="caja" id="mo-salidas"></div></div>
          <aside class="caja" style="flex:1 1 420px" id="mo-det" hidden>
            <div id="mo-titulo"></div>${previaHtml('mo-previa')}<div class="pie-previa" id="mo-pie"></div>
            <h3 class="sub">Evidencia</h3><div id="mo-evid"></div>
            <h3 class="sub">Audio por pista</h3><div id="mo-audio"></div>
            <h3 class="sub">Métricas</h3><div id="mo-metricas"></div>
            <h3 class="sub">Bitrate en el último minuto</h3><div id="mo-bitrate"></div>
          </aside></div>
        <div class="caja" id="mo-carga"></div>
        <div class="caja"><h2>Qué significa cada estado</h2><div class="significa">${SIGNIFICA.map(([c, n, t]) => `<div><span>${etq(c, n)}</span><span>${t}</span></div>`).join('')}</div></div></div>`;
    }
    if (!m) { poner('#mo-entradas', `<div class="vacio">${esc(H.fallo || 'Cargando…')}</div>`); return; }
    const rutas = m.destinos.filter(d => d.ruta);
    const vale = MO.sel && (MO.sel.tipo === 'ruta' ? rutas.some(d => d.id === MO.sel.id) : m.fuentes.some(f => f.id === MO.sel.id));
    if (!vale) {                      // de entrada, lo que más falta hace mirar: una ruta con problemas, o la primera fuente con señal
      const mal = rutas.find(d => ['error', 'reconectando'].includes(d.ruta.estado.estado));
      const f = m.fuentes.find(x => x.con_senal) || m.fuentes[0];
      MO.sel = mal ? {tipo: 'ruta', id: mal.id} : f ? {tipo: 'fuente', id: f.id} : null;
    }
    entradasMonitor(m);
    salidasMonitor(m, rutas);
    detalleMonitor(m);
    pintarCargaEstacion(m);
  },
};

// ---------------------------------------------------------------- las dos listas
// La evidencia de un estado en una frase: lo que pasa y los hechos que lo respaldan.
function evidenciaTexto(e) {
  const partes = [e.detalle, ...(e.evidencia || []).map(x => x.que + (x.desde ? ` desde las ${hora(x.desde)}` : ''))].filter(Boolean);
  return partes.map(p => /[.:!?]$/.test(p) ? p : p + '.').join(' ');
}
function filaMonitor(tipo, id, e, titulo, kbps) {
  const sel = MO.sel?.tipo === tipo && MO.sel.id === id;
  return `<div class="linea alta${sel ? ' elegida' : ''}">
    <div>${etqEstado(e)}</div>
    <div><button class="nombre" type="button" data-a="mo-ver" data-t="${tipo}" data-id="${esc(id)}" data-k="mo:${tipo}:${esc(id)}">${titulo}</button>
      <div class="sub">${esc(evidenciaTexto(e))}</div></div>
    <div class="n">${kbps != null ? num(kbps) : '—'}</div>
    <div class="n ch">${e.actualizado ? `${hora(e.actualizado)}<div class="xs gr">${hace(e.actualizado)}</div>` : '—'}</div></div>`;
}
const tablaMonitor = (id, segunda, filas, vacio) => filas.length ? `<div class="rejilla" data-desliza="${id}" style="--cols:${COLS_MONITOR};--ancho:640px"><div>
    <div class="cabeza"><span>Estado</span><span>${segunda}</span><span>Bitrate</span><span>Actualizado</span></div>${filas.join('')}</div></div>` : `<div class="vacio">${vacio}</div>`;

function entradasMonitor(m) {
  const pendientes = m.fuentes.filter(f => estadoFuente(f).cls === 'adaptador').length;
  const total = suma(m.fuentes.map(f => f.con_senal ? f.kbps : 0));
  const filas = m.fuentes.map(f => { const e = estadoFuente(f); return filaMonitor('fuente', f.id, e, esc(f.nombre), e.cls === 'adaptador' ? null : f.con_senal ? f.kbps : 0); });
  poner('#mo-entradas', `<div class="cab"><h2>Entradas</h2><small class="gr">${num(total)} recibidos${pendientes ? ` · ${plural(pendientes, 'fuente con adaptador pendiente no se mide', 'fuentes con adaptador pendiente no se miden')}` : ''}</small></div>
    ${tablaMonitor('mo-e', 'Fuente y evidencia', filas, 'No hay fuentes. <a href="#fuentes">Añadir una</a>')}`);
}
function salidasMonitor(m, rutas) {
  const total = suma(rutas.map(d => d.ruta.metricas?.kbps));
  const filas = rutas.map(d => filaMonitor('ruta', d.id, estadoDestino(d), `${esc(d.ruta.fuente_nombre)} <span class="hacia" aria-label="hacia">→</span> ${esc(d.nombre)}`,
    estadoDestino(d).cls === 'adaptador' ? null : d.ruta.metricas?.kbps));
  poner('#mo-salidas', `<div class="cab"><h2>Salidas por ruta</h2><small class="gr">${num(total)} enviados · cada salida se reconecta por su cuenta</small></div>
    ${tablaMonitor('mo-s', 'Ruta y evidencia', filas, 'No hay rutas activas. Se trazan en la <a href="#rutas">Matriz de rutas</a>.')}`);
}
ACC['mo-ver'] = el => { MO.sel = {tipo: el.dataset.t, id: el.dataset.id}; SEC.monitoreo.pinta(); };

// ---------------------------------------------------------------- el detalle de lo elegido
function detalleMonitor(m) {
  const s = MO.sel, d = s?.tipo === 'ruta' ? m.destinos.find(x => x.id === s.id) : null;
  const f = m.fuentes.find(x => x.id === (d ? d.ruta.fuente : s?.id));
  $('#mo-det').hidden = !f;
  if (!f) { previa(null); medidores(null); return; }
  const ruta = d?.ruta, e = ruta ? estadoDestino(d) : estadoFuente(f), ef = estadoFuente(f);
  poner('#mo-titulo', `<div class="titulo" style="margin-bottom:10px"><h2>${ruta ? `${esc(f.nombre)} <span class="hacia" aria-label="hacia">→</span> ${esc(d.nombre)}` : esc(f.nombre)}</h2>${etqEstado(e)}</div>`);

  // vista previa: lo que sale por la ruta (su conversión, si la tiene y está en marcha) o lo que llega de la fuente
  const conv = ruta?.metricas?.conversion, propia = ruta && (!conv || conv.desde) && ruta.vista?.hls;
  const base = f.con_senal && ef.cls === 'recibiendo' ? (propia ? ruta.vista.hls : f.vista?.hls) : null;
  const caja = $('#mo-previa'), mal = ruta && ['error', 'reconectando'].includes(e.cls);
  textoPrevia(caja, e.cls, e.nombre, !base ? (ruta && !f.con_senal ? 'La fuente no tiene señal' : e.detalle)
    : inactivo() ? 'En pausa por inactividad: mueve el ratón para seguir viéndola' : 'Abriendo la vista previa…');
  previa(caja, base);
  const otras = ruta ? m.destinos.filter(x => x.id !== d.id && x.ruta?.fuente === f.id && x.ruta.estado.estado === 'enviando').length : 0;
  poner('#mo-pie', `<span>${!ruta ? 'Lo que llega de la fuente, por HLS, unos segundos por detrás del directo'
      : mal && base ? `La fuente sigue llegando: lo que falla es esta salida.${otras ? ` ${otras === 1 ? 'Otra salida' : `Otras ${otras} salidas`} de «${esc(f.nombre)}» ${otras === 1 ? 'sigue' : 'siguen'} enviando.` : ''}`
      : propia && conv ? 'La señal ya convertida que sale por esta ruta, por HLS, unos segundos por detrás' : 'La señal que alimenta esta ruta, por HLS, unos segundos por detrás'}</span>`);

  // evidencia, con lo que quien mira puede hacer
  const reintentar = ruta && ['error', 'reconectando'].includes(e.cls) && tiene('conectar_libre');
  poner('#mo-evid', `${evidenciaHtml(e)}
    <div class="fila" style="gap:8px;margin-top:8px">${reintentar ? `<button class="bt chico" type="button" data-a="mo-reintentar" data-id="${esc(d.id)}" data-k="mo-reintentar">Reintentar ahora</button>` : ''}
      ${ruta ? `<a class="ch" href="#rutas" data-a="mo-a-matriz">Ver en la matriz</a>` : `<a class="ch" href="#fuentes" data-a="mo-a-fuente">Ver la fuente</a>`}</div>`);

  // audio: los niveles de la fuente; en una ruta, además, qué pistas salen
  medidores($('#mo-audio'), f);

  // métricas
  const cifra = (k, v, nota) => `<div class="dato"><b>${k}</b><span>${v}</span>${nota ? `<small class="gr nota">${nota}</small>` : ''}</div>`;
  let cifras, notas = [];
  if (!ruta) {
    const v = f.pistas?.video, desde = f.con_senal ? (ef.evidencia || []).find(x => x.desde)?.desde : null, na = f.pistas?.audio?.length;
    cifras = [cifra('Bitrate recibido', f.con_senal ? num(f.kbps) : '—'),
      cifra('Vídeo', v?.ancho ? `${v.ancho}×${v.alto}` : '—', v?.fps ? `${String(Math.round(v.fps * 100) / 100).replace('.', ',')} fps` : ''),
      cifra('Pistas de audio', f.pistas ? String(na || 0) : '—'),
      cifra('Con señal desde', desde ? hora(desde, false) : '—', desde ? dur(ahora() - desde) : ''),
      cifra('Última señal', f.con_senal ? 'ahora' : hace(f.ultima_senal)),
      cifra('Rutas que alimenta', String(f.rutas?.length || 0))];
  } else {
    const x = ruta.metricas || {}, plan = ruta.plan || {};
    const convierte = [plan.video?.accion === 'convierte' && 'Vídeo', (plan.audio || []).some(a => a.accion === 'convierte') && 'Audio'].filter(Boolean);
    cifras = [cifra('Bitrate enviado', x.kbps != null ? num(x.kbps) : '—', x.kbps == null && e.cls === 'enviando' ? 'MediaMTX no lo mide en un reenvío' : ''),
      d.modo === 'entrega' ? cifra('Receptores', String((x.receptores || []).length)) : cifra('Reconexiones', String(x.reconexiones || 0)),
      d.modo === 'entrega' ? cifra('Perfil', esc(nombrePerfilDe(m, ruta.perfil))) : cifra('Intentos sin éxito', String(x.intentos || 0),
        x.intentos && e.cls === 'enviando' ? 'A cero tras 1 min enviando' : ''),     // el supervisor no da un intento por bueno hasta entonces
      cifra('Enviando desde', x.enviando_desde ? hora(x.enviando_desde, false) : '—',
        x.enviando_desde ? dur(ahora() - x.enviando_desde) : e.cls === 'enviando' ? 'MediaMTX no dice desde cuándo' : ''),
      cifra('Habilitada desde', hora(ruta.desde, false), ruta.por ? esc(ruta.por) : ''),
      cifra('Conversión', convierte.length ? convierte.join(' y ') : plan.provisional ? '—' : 'Ninguna', conv?.compartida_con?.length ? 'Compartida' : '')];
    notas = [plan.resumen, d.modo !== 'entrega' && `Perfil: ${nombrePerfilDe(m, ruta.perfil)}.`,
      conv?.compartida_con?.length && `La conversión se comparte con ${conv.compartida_con.map(n => `«${n}»`).join(', ')}.`,
      conv?.reinicios ? `La conversión se ha reiniciado ${plural(conv.reinicios, 'vez', 'veces')}.` : '',
      ...(x.receptores || []).map(r => `Recibe ${r.tipo || r.protocolo}${r.remoto ? ` (${r.remoto})` : ''}${r.desde ? ` desde las ${hora(r.desde, false)}` : ''}.`)].filter(Boolean);
  }
  poner('#mo-metricas', `<div class="cifras">${cifras.join('')}</div>${notas.map(n => `<p class="ayuda">${esc(n)}</p>`).join('')}`);
  poner('#mo-bitrate', barrasBitrate(ruta ? 'r:' + d.id : 'f:' + f.id));
}
const nombrePerfilDe = (m, id) => m.perfiles.find(p => p.id === id)?.nombre || id;
ACC['mo-a-matriz'] = () => { const d = H.m.destinos.find(x => x.id === MO.sel?.id); if (d?.ruta) R.sel = {f: d.ruta.fuente, d: d.id}; location.hash = 'rutas'; };
ACC['mo-a-fuente'] = () => { F.sel = MO.sel?.id || F.sel; location.hash = 'fuentes'; };
ACC['mo-reintentar'] = async el => {
  await pide('POST', `/api/rutas/${el.dataset.id}/reintentar`);
  decir('El hub vuelve a intentarlo ahora.', 'ok');
  await leerMatriz();
};

// El bitrate del último minuto, en doce barras de cinco segundos (la media de lo leído en cada tramo).
function barrasBitrate(k) {
  const h = H.hist[k] || [], t1 = ahora(), tramos = Array.from({length: 12}, () => []);
  for (const [t, v] of h) { const i = 11 - Math.floor((t1 - t) / 5); if (i >= 0 && i < 12) tramos[i].push(v); }
  const medias = tramos.map(c => c.length ? suma(c) / c.length : null), conDato = medias.filter(v => v != null);
  const tope = Math.max(0, ...conDato), escala = [500, 1000, 2000, 5000, 10000, 20000, 50000, 100000].find(x => x >= tope) || tope;
  const ultimo = h.length ? h[h.length - 1][1] : null;
  const barras = medias.map((v, i) => {
    const tramo = `De hace ${60 - i * 5} s a hace ${55 - i * 5} s`;
    return v == null ? `<div class="nada" title="${tramo}: sin dato"></div>`
      : `<div${v <= 0 ? ' class="cero"' : ` style="height:${Math.max(3, v / escala * 100).toFixed(1)}%"`} title="${tramo}: ${num(v)}"></div>`;
  }).join('');
  return `<div class="graf-rot"><span>${num(escala)}</span><span>Ahora: ${ultimo != null ? num(ultimo) : '—'}</span></div>
    <div class="graf" role="img" aria-label="Bitrate del último minuto, de ${conDato.length ? num(Math.min(...conDato)) : '—'} a ${num(tope)}">${barras}</div>
    <div class="graf-rot"><span>hace 60 s</span><span>0 en la base · una barra cada 5 s</span><span>ahora</span></div>`;
}

// ---------------------------------------------------------------- la carga de la estación
function pintarCargaEstacion(m) {
  const c = H.carga;
  if (!c) return poner('#mo-carga', '<h2>Carga de la estación</h2><div class="vacio">Midiendo…</div>');
  const mem = c.memoria, usada = mem ? mem.total_mb - mem.disponible_mb : null, gpu = (c.gpu || [])[0];
  const tareas = Object.values(c.tareas || {}), motor = tareas.find(t => t.tipo === 'motor'), ff = tareas.filter(t => t.tipo !== 'motor');
  const conv = ff.filter(t => t.tipo === 'conversion').length;
  const pct = v => v == null ? '—' : `${Math.round(v)} %`;
  const tarjeta = (rot, valor, barra, sub) => `<div class="dato"><b>${rot}</b><span>${valor}</span>
    ${barra != null ? `<div class="barra"><span style="width:${Math.max(0, Math.min(100, barra)).toFixed(0)}%"${barra >= 90 ? ' class="tope"' : barra >= 70 ? ' class="alto"' : ''}></span></div>` : ''}
    ${sub ? `<small class="gr nota">${sub}</small>` : ''}</div>`;
  const filas = MO.tareas ? [...tareas].sort((a, b) => (b.cpu_pct || 0) - (a.cpu_pct || 0)).map(t => `<div class="linea"><div class="corta" title="${esc(t.texto)}">${esc(t.texto)}</div>
    <div class="n">${t.cpu_pct == null ? '—' : String(t.cpu_pct.toFixed(1)).replace('.', ',') + ' %'}</div><div class="n">${t.memoria_mb == null ? '—' : Math.round(t.memoria_mb) + ' MB'}</div></div>`).join('') : '';
  poner('#mo-carga', `<div class="cab"><h2>Carga de ${esc(c.equipo?.nombre || 'la estación')}</h2>
      <small class="gr">Se mide cada 4 s mientras esta página está a la vista${c.equipo?.cpu ? ` · ${esc(c.equipo.cpu)}` : ''}${c.equipo?.sistema ? ` · ${esc(c.equipo.sistema)}` : ''}</small></div>
    <div class="carga">
      ${tarjeta(`CPU · ${c.nucleos} hilos`, pct(c.cpu_pct), c.cpu_pct, c.carga ? `Carga media de 1, 5 y 15 min: ${c.carga.map(x => x.toFixed(2).replace('.', ',')).join(' · ')}` : '')}
      ${gpu ? tarjeta(`GPU · ${esc(gpu.nombre)}`, pct(gpu.uso_pct), gpu.uso_pct, [gpu.sesiones_codificador != null && plural(Math.round(gpu.sesiones_codificador), 'codificador NVENC', 'codificadores NVENC'),
          gpu.codificador_pct != null && `codificador al ${Math.round(gpu.codificador_pct)} %`, gpu.memoria_mb != null && `${gigas(gpu.memoria_mb)} de ${gigas(gpu.memoria_total_mb)} GB`].filter(Boolean).join(' · '))
        : tarjeta('GPU', '—', null, 'Esta estación no tiene una GPU NVIDIA a la vista: las conversiones de vídeo van por CPU')}
      ${mem ? tarjeta(`Memoria · ${gigas(usada)} de ${gigas(mem.total_mb)} GB`, pct(usada / mem.total_mb * 100), usada / mem.total_mb * 100) : ''}
      ${tarjeta('Señal que entra', num(suma(m.fuentes.map(f => f.con_senal ? f.kbps : 0))), null, 'La suma de las fuentes con señal')}
      ${tarjeta('Señal que sale', num(suma(m.destinos.map(d => d.ruta?.metricas?.kbps))), null, 'La suma de las rutas que el hub mide')}
      ${tarjeta('MediaMTX', m.sistema.mediamtx ? 'En marcha' : 'No responde', null, motor ? `${motor.cpu_pct == null ? '—' : String(motor.cpu_pct.toFixed(1)).replace('.', ',') + ' %'} de un núcleo · ${Math.round(motor.memoria_mb || 0)} MB` : '')}
      ${tarjeta('Procesos FFmpeg', `${ff.length} · ${conv} con conversión`, null, 'Entradas que el hub va a buscar, conversiones y envíos')}
    </div>
    <p style="margin:12px 0 0"><button class="bt chico" type="button" data-a="mo-tareas" data-k="mo-tareas" aria-expanded="${MO.tareas}">${MO.tareas ? 'Ocultar' : 'Ver'} el consumo de cada proceso</button></p>
    ${MO.tareas ? `<div class="rejilla" data-desliza="mo-t" style="--cols:minmax(240px,1fr) 150px 110px;--ancho:520px;margin-top:10px"><div>
      <div class="cabeza"><span>Proceso del hub</span><span>CPU, de un núcleo</span><span>Memoria</span></div>${filas || '<div class="vacio">El hub no tiene procesos en marcha.</div>'}</div></div>` : ''}`);
}
ACC['mo-tareas'] = () => { MO.tareas = !MO.tareas; pintarCargaEstacion(H.m); };
