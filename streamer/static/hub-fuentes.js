// Media Syntaxis Streamer - hub de señales: Fuentes. La lista, el detalle de la elegida (vista
// previa, señal recibida con sus medidores y cómo llega) y el formulario de una fuente.

const F = {sel: null, form: null};

// Quién inicia la conexión, dicho para quien da de alta la fuente: [título, explicación]
const QUIEN_FUENTE = {
  srt: {origen: ['El codificador llama al hub', 'El hub escucha (listener). Para equipos que salen a Internet y conocen la dirección del hub.'],
    hub: ['El hub llama al codificador', 'El hub llama (caller). Para orígenes con dirección fija que esperan la llamada.']},
  rtmp: {origen: ['El codificador publica en el hub', 'El hub acepta la publicación. Es lo habitual con un codificador de estudio.'],
    hub: ['El hub lee un origen remoto', 'El hub se conecta a un servidor RTMP o RTMPS y toma la señal.']},
  hls: {hub: ['El hub lee una dirección', 'El hub descarga la lista .m3u8 y sus segmentos. HLS no se puede recibir de otra forma.']},
  webrtc: {origen: ['El navegador publica en el hub', 'Publicación WHIP por HTTPS, desde un navegador o un codificador compatible.']},
  ndi: {hub: ['El hub toma una fuente de la red local', 'El hub se suscribe a una fuente NDI de la LAN por su nombre.']},
  zixi: {origen: ['El equipo Zixi llama al hub', 'El componente de Zixi de la estación recibe la señal (push).'],
    hub: ['El hub llama al equipo Zixi', 'El componente de Zixi de la estación pide la señal a un equipo remoto (pull).']},
};
const ORIGEN_DE = {srt: 'Codificador remoto', rtmp: 'Codificador o servidor RTMP', hls: 'Servidor HLS', webrtc: 'Navegador o codificador',
  ndi: 'Fuente NDI de la LAN', zixi: 'Equipo Zixi remoto'};
const COLS_FUENTES = '14px minmax(160px,1.4fr) minmax(200px,1.5fr) 142px minmax(180px,1.3fr) minmax(170px,1.2fr) 84px 100px 150px';

SEC.fuentes = {
  pinta() {
    const s = $('#s-fuentes');
    if (!s._montada) {
      s._montada = true;
      s.innerHTML = `<div id="fu-vista" class="vista">
          <div class="caja" id="fu-lista"></div>
          <div id="fu-det" class="vista" hidden>
            <div class="titulo" id="fu-titulo"></div>
            <div id="fu-prueba"></div>
            <div class="cols">
              <div class="caja" style="flex:1 1 400px"><h2>Vista previa</h2>${previaHtml('fu-previa')}<div class="pie-previa" id="fu-pie"></div></div>
              <div class="caja" style="flex:1 1 400px"><h2>Señal recibida</h2><div id="fu-cifras"></div>
                <h3 class="tit">Pistas de audio</h3><div id="fu-audio"></div></div>
              <div class="caja" style="flex:1 1 340px" id="fu-como"></div>
            </div>
          </div>
        </div>
        <div id="fu-form" hidden></div>`;
    }
    $('#fu-vista').hidden = !!F.form;
    $('#fu-form').hidden = !F.form;
    if (F.form) { previa(null); medidores(null); return; }     // el formulario se pinta al abrirlo y al cambiar su estructura
    const m = H.m;
    if (!m) { poner('#fu-lista', `<div class="vacio">${esc(H.fallo || 'Cargando…')}</div>`); return; }
    if (!m.fuentes.some(f => f.id === F.sel)) F.sel = m.fuentes[0]?.id || null;
    listaFuentes(m);
    detalleFuente(m.fuentes.find(f => f.id === F.sel));
  },
};

// ---------------------------------------------------------------- lista
function listaFuentes(m) {
  const cuenta = {};
  const filas = m.fuentes.map(f => {
    const e = estadoFuente(f), [video, audio] = pistasTexto(f.pistas);
    cuenta[e.cls] = (cuenta[e.cls] || 0) + 1;
    const rutas = f.rutas?.length || 0;
    return `<div class="linea${f.id === F.sel ? ' elegida' : ''}">
      ${bola(e)}
      <div><button class="nombre corta" type="button" data-a="fu-ver" data-id="${esc(f.id)}" data-k="fu-n:${esc(f.id)}" title="Ver el detalle">${esc(f.nombre)}</button>
        <div class="sub">${rutas ? plural(rutas, 'ruta', 'rutas') : 'Sin rutas'}${f.heredada ? ' · flujo del Streamer' : ''}</div></div>
      <div>${etq('', f.protocolo_nombre)} <span class="ch">${esc(f.quien_inicia)}</span></div>
      <div>${etqEstado(e)}</div>
      <div class="ch corta" title="${esc(video)}">${esc(video)}</div>
      <div class="ch corta" title="${esc(audio)}">${esc(audio)}</div>
      <div class="ch n">${f.con_senal && f.kbps ? num(f.kbps) : '—'}</div>
      <div class="ch gr">${f.con_senal ? 'ahora' : hace(f.ultima_senal)}</div>
      <div class="acc">
        <button class="bt chico" type="button" data-a="fu-probar" data-id="${esc(f.id)}" data-k="fu-p:${esc(f.id)}">Probar</button>
        <button class="bt chico" type="button" data-a="fu-ver" data-id="${esc(f.id)}" data-k="fu-v:${esc(f.id)}">Ver</button>
        <button class="bt chico" type="button" data-a="fu-editar" data-id="${esc(f.id)}" data-k="fu-e:${esc(f.id)}"${sinPermiso('editar', 'editar fuentes')}>Editar</button>
      </div></div>`;
  });
  const resumen = [['recibiendo', 'con señal'], ['esperando_fuente', 'esperando'], ['reconectando', 'reconectando'], ['error', 'con error'],
    ['adaptador', 'con adaptador pendiente'], ['desactivada', cuenta.desactivada === 1 ? 'desactivada' : 'desactivadas']]
    .filter(([k]) => cuenta[k]).map(([k, t]) => `${cuenta[k]} ${t}`).join(' · ');
  const ndi = adaptadorListo('ndi');
  poner('#fu-lista', `<div class="cab"><h2>Fuentes</h2><small class="gr">${resumen}</small><span class="hueco"></span>
      ${m.protocolos.ndi && !ndi ? '<button class="bt" type="button" disabled title="Requiere el adaptador NDI">Buscar fuentes NDI</button>' : ''}
      <button class="bt pri" type="button" data-a="fu-nueva" data-k="fu-nueva"${sinPermiso('editar', 'crear fuentes')}>Nueva fuente</button></div>
    ${filas.length ? `<div class="rejilla" data-desliza="fu" style="--cols:${COLS_FUENTES};--ancho:1280px"><div>
      <div class="cabeza"><span></span><span>Fuente</span><span>Protocolo y conexión</span><span>Estado</span><span>Vídeo</span><span>Audio</span><span>Bitrate</span><span>Última señal</span><span></span></div>
      ${filas.join('')}</div></div>`
      : `<div class="vacio">Todavía no hay fuentes. ${tiene('editar') ? '«Nueva fuente» da de alta la primera.' : 'Las da de alta un manager o un administrador.'}</div>`}`);
}

// ---------------------------------------------------------------- detalle
function detalleFuente(f) {
  $('#fu-det').hidden = !f;
  if (!f) { previa(null); medidores(null); return; }
  const e = estadoFuente(f), id = esc(f.id), editar = tiene('editar');
  const botones = f.heredada
    ? `<a class="bt" href="#${encodeURIComponent(f.heredada.flujo)}">Abrir su flujo</a>`
    : `<button class="bt" type="button" data-a="fu-editar" data-id="${id}" data-k="fu-d-editar"${sinPermiso('editar', 'editar fuentes')}>Editar</button>
       ${editar ? `<button class="bt" type="button" data-a="fu-onoff" data-id="${id}" data-k="fu-d-onoff">${f.activo ? 'Desactivar' : 'Activar'}</button>
       <button class="bt peligro" type="button" data-a="fu-borrar" data-id="${id}" data-k="fu-d-borrar">Borrar</button>` : ''}`;
  poner('#fu-titulo', `<h2>${esc(f.nombre)}</h2>${etq('', f.protocolo_nombre)}${etqEstado(e)}
    ${f.heredada ? etq('', 'Flujo', 'Es un flujo del Streamer: se cambia en «Flujos y composición»') : ''}<span class="hueco"></span>
    <button class="bt" type="button" data-a="fu-probar" data-id="${id}" data-k="fu-d-probar">Probar fuente</button>${botones}`);
  poner('#fu-prueba', pruebaHtml(H.pruebas[f.id]));

  // vista previa: solo con señal
  const caja = $('#fu-previa'), ver = f.con_senal && e.cls === 'recibiendo' && f.vista?.hls;
  textoPrevia(caja, e.cls, e.nombre, !ver ? e.detalle : inactivo() ? 'En pausa por inactividad: mueve el ratón para seguir viéndola' : 'Abriendo la vista previa…');
  previa(caja, ver ? f.vista.hls : null);
  poner('#fu-pie', `<span>Vista previa por HLS, unos segundos por detrás del directo</span><span>Última señal: ${f.con_senal ? 'ahora' : hace(f.ultima_senal)}</span>`);

  // señal recibida
  const v = f.pistas?.video, desde = f.con_senal ? (e.evidencia || []).find(x => x.desde)?.desde : null;
  const cifra = (k, val) => `<div class="dato"><b>${k}</b><span>${val}</span></div>`;
  const notas = [
    !f.pistas && (f.con_senal ? 'El hub está leyendo las pistas de esta señal.' : 'El hub lee las pistas cuando llega la señal.'),
    f.pistas?.sin_confirmar && 'Son las pistas de la última vez: el hub las está confirmando.',
    f.pistas?.parcial && 'La inspección no pudo leer todas las pistas.',
    f.inspeccion?.error && `La última inspección falló: ${f.inspeccion.error}`,
    v && v.pasa === false && 'El hub no puede copiar este vídeo tal como llega: lo convierte en cada ruta.',
  ].filter(Boolean);
  poner('#fu-cifras', `<div class="cifras">
      ${cifra('Resolución', v?.ancho ? `${v.ancho}×${v.alto}${v.entrelazado ? 'i' : 'p'}` : '—')}
      ${cifra('FPS', v?.fps ? String(Math.round(v.fps * 100) / 100).replace('.', ',') : '—')}
      ${cifra('Códec de vídeo', v ? esc(v.texto.split(' · ')[0] + (v.perfil ? ' ' + v.perfil : '')) : '—')}
      ${cifra('Bitrate recibido', f.con_senal ? num(f.kbps) : '—')}
      ${cifra('Última señal', f.con_senal ? 'ahora' : hace(f.ultima_senal))}
      ${cifra('Con señal desde', desde ? `${hora(desde, false)} <small class="gr">(${dur(ahora() - desde)})</small>` : '—')}
    </div>${notas.map(n => `<p class="ayuda">${esc(n)}</p>`).join('')}`);
  medidores($('#fu-audio'), f);

  // cómo llega
  const opciones = Object.entries(f.opciones || {}).map(([k, val]) => `<div class="par"><span>${esc(NOMBRE_OPCION[k] || k)}</span><span>${esc(val)}</span></div>`).join('');
  const rutas = (f.rutas || []).map(d => H.m.destinos.find(x => x.id === d)?.nombre).filter(Boolean);
  poner('#fu-como', `<h2>Cómo llega la señal</h2>
    <div class="cifras" style="--n:2">
      <div class="dato txt"><b>Quién inicia la conexión</b><span>${esc(f.quien_inicia)}</span></div>
      <div class="dato txt"><b>Dirección de la señal</b><span>${esc(f.sentido)}</span></div>
    </div>
    ${direccionHtml('fuente', f)}
    <div style="margin-top:10px">
      ${f.protocolo === 'srt' && !f.direccion ? `<div class="par"><span>Latencia SRT</span><span>${f.latencia_ms} ms</span></div>` : ''}${opciones}
      <div class="par"><span>Rutas</span><span>${rutas.length ? esc(rutas.join(', ')) : 'Ninguna'} · <a href="#rutas">Matriz</a></span></div>
      <div class="par"><span>Creada</span><span>${hora(f.creado, false)}</span></div>
    </div>
    ${e.cls === 'adaptador' ? `<p class="morado" style="margin:12px 0 0">Esta fuente no puede recibir señal hasta que su adaptador esté habilitado. <a href="#adaptadores">Ver adaptadores</a></p>` : ''}
    ${f.heredada ? `<p class="ayuda">Es el flujo «${esc(f.nombre)}» del Streamer: su entrada se cambia en <a href="#${encodeURIComponent(f.heredada.flujo)}">su flujo</a>.</p>` : ''}
    ${!f.activo ? '<p class="ayuda">Está desactivada: el hub no la escucha ni la llama, y sus rutas esperan.</p>' : ''}`);
}
const NOMBRE_OPCION = {nombre: 'Nombre NDI', equipo: 'Equipo', grupo: 'Grupo', calidad: 'Calidad', modo: 'Modo', host: 'Equipo remoto', puerto: 'Puerto', canal: 'Canal', variante: 'Variante de la lista'};

// ---------------------------------------------------------------- acciones de la lista y del detalle
const fuenteDe = id => H.m?.fuentes.find(f => f.id === id);
ACC['fu-ver'] = el => {
  F.sel = el.dataset.id;
  SEC.fuentes.pinta();
  if (el.classList.contains('chico')) $('#fu-det').scrollIntoView({behavior: 'smooth', block: 'start'});
};
ACC['fu-probar'] = el => {
  const id = el.dataset.id;
  F.sel = id;
  return probar(id, () => pide('POST', `/api/fuentes/${id}/probar`));
};
ACC['fu-onoff'] = async el => {
  const f = fuenteDe(el.dataset.id);
  if (!f) return;
  if (f.activo) {
    const aire = alAire({fuente: f.id});
    if (!await preguntar({titulo: `Desactivar «${f.nombre}»`, aceptar: 'Desactivar', clase: aire.length ? 'peligro' : 'pri',
      html: `<p>El hub deja de escucharla o de llamarla. Sus rutas se conservan y esperan a que vuelva a activarse.</p>${avisoAire(aire)}`})) return;
  }
  await pide('POST', `/api/fuentes/${f.id}/${f.activo ? 'desactivar' : 'activar'}`);
  await leerMatriz();
};
ACC['fu-borrar'] = async el => {
  const f = fuenteDe(el.dataset.id);
  if (!f) return;
  const rutas = (f.rutas || []).map(d => H.m.destinos.find(x => x.id === d)?.nombre).filter(Boolean);
  if (!await preguntar({titulo: `Borrar «${f.nombre}»`, aceptar: 'Borrar la fuente', clase: 'peligro',
    html: `<p>Se borra la fuente${rutas.length ? ` y ${rutas.length === 1 ? 'su ruta' : 'sus rutas'} hacia ${esc(rutas.join(', '))}` : ''}. Los destinos se conservan. No se puede deshacer.</p>${avisoAire(alAire({fuente: f.id}))}`})) return;
  await pide('DELETE', `/api/fuentes/${f.id}`);
  delete H.pruebas[f.id];
  decir(`Fuente «${f.nombre}» borrada.`, 'ok');
  await leerMatriz();
};

// ---------------------------------------------------------------- formulario
// d: lo escrito. Al editar, las claves empiezan vacías (vacío = conservar la guardada) y ni el
// protocolo ni quién inicia la conexión se pueden cambiar.
ACC['fu-nueva'] = () => abrirFormFuente(null);
ACC['fu-editar'] = el => {
  const f = fuenteDe(el.dataset.id);
  if (!f) return;
  if (f.heredada) { location.hash = '#' + encodeURIComponent(f.heredada.flujo); return; }
  abrirFormFuente(f);
};
function abrirFormFuente(f) {
  const o = f?.opciones || {};
  F.form = {id: f?.id || null, antes: f || null, error: '', ocupado: false, variantes: null,
    d: {nombre: f?.nombre || '', protocolo: f?.protocolo || 'srt', inicia: f?.inicia || 'origen', url: f?.remoto?.url || '', streamid: '', passphrase: '',
      clave: '', latencia_ms: f ? f.latencia_ms : '', puerto: o.puerto || '', variante: o.variante ?? 0,
      opciones: {nombre: o.nombre || '', equipo: o.equipo || '', grupo: o.grupo || '', calidad: o.calidad || '', modo: o.modo || '', host: o.host || '', puerto: o.puerto || '', canal: o.canal || ''}}};
  delete H.pruebas['fu-form'];
  SEC.fuentes.pinta();
  pintarFormFuente();
  window.scrollTo(0, 0);
  $('#fu-form [data-c="nombre"]')?.focus();
}
function cerrarFormFuente() { F.form = null; SEC.fuentes.pinta(); window.scrollTo(0, 0); }
ACC['ff-cancelar'] = cerrarFormFuente;
ACC['ff-proto'] = el => {
  const d = F.form.d, p = el.dataset.v;
  if (F.form.id || d.protocolo === p) return;
  d.protocolo = p;
  d.inicia = H.m.protocolos[p].entradas[0];
  F.form.variantes = null; delete H.pruebas['fu-form'];
  pintarFormFuente();
};
ACC['ff-inicia'] = el => {
  if (F.form.id || F.form.d.inicia === el.dataset.v) return;
  F.form.d.inicia = el.dataset.v;
  delete H.pruebas['fu-form'];
  pintarFormFuente();
};
ACC['ff-variante'] = el => { F.form.d.variante = Number(el.dataset.v); pintarFormFuente(); };
ACC['ff-generar'] = el => {
  F.form.d.passphrase = fraseNueva();
  pintarFormFuente();
  const i = $('#fu-form [data-c="passphrase"]');
  if (i) { i.type = 'text'; i.select(); }
};
CAMBIA.fuente = (c, v) => {
  const d = F.form.d;
  if (c.startsWith('op.')) d.opciones[c.slice(3)] = v; else d[c] = v;
};

function parametrosFuente(d, antes) {          // los campos del paso 4, según el protocolo y quién llama
  const p = d.protocolo, llama = d.inicia === 'hub', r = antes?.remoto, guardada = t => `${t} (vacío = conservar)`;
  const lat = campo('latencia_ms', 'Latencia SRT (ms)', d.latencia_ms, {tipo: 'number', min: 20, max: 8000, pon: 'La del hub', ayuda: 'Entre 20 y 8000 ms. Más latencia aguanta más pérdidas de red.'});
  if (p === 'ndi') return campo('op.nombre', 'Nombre de la fuente NDI', d.opciones.nombre, {ancho: true, pon: 'ESTUDIO-B (Programa)', ayuda: 'Tal como la anuncia el equipo en la red: EQUIPO (Fuente).'})
    + campo('op.grupo', 'Grupo NDI (opcional)', d.opciones.grupo, {pon: 'public'})
    + seleccion('op.calidad', 'Calidad', d.opciones.calidad, [['', 'La del adaptador'], ['alta', 'Alta (ancho de banda completo)'], ['baja', 'Baja (proxy)']]);
  if (p === 'zixi') return (llama ? campo('op.host', 'Equipo Zixi remoto', d.opciones.host, {pon: 'zixi.ejemplo.com'}) + campo('op.puerto', 'Puerto', d.opciones.puerto, {tipo: 'number', min: 1, max: 65535, pon: '2088'}) : '')
    + campo('op.canal', 'Canal (stream ID de Zixi)', d.opciones.canal, {pon: 'afiliada-01'})
    + campo('clave', 'Clave del canal (opcional)', d.clave, {tipo: 'password', botones: [['ojo', 'Mostrar']], pon: antes?.adaptador?.tiene_clave ? guardada('Guardada') : ''});
  if (llama) {
    const ejemplo = {srt: 'srt://203.0.113.20:9000', rtmp: 'rtmp://origen.ejemplo.com/live/senal', hls: 'https://ejemplo.com/senal/index.m3u8'}[p];
    let h = campo('url', {srt: 'Dirección del origen', rtmp: 'Dirección del servidor de origen', hls: 'Dirección de la lista (.m3u8)'}[p], d.url, {ancho: true, pon: ejemplo,
      ayuda: p === 'hls' ? 'Con «Probar fuente» el hub lee la lista y deja elegir la variante que entra.' : 'El hub llama a esta dirección y reintenta si se cae.'});
    if (p === 'srt') h += campo('streamid', 'Stream ID (opcional)', d.streamid, {pon: r?.tiene_streamid ? guardada('Guardado') : ''})
      + campo('passphrase', 'Passphrase (opcional)', d.passphrase, {tipo: 'password', botones: [['ojo', 'Mostrar']], pon: r?.tiene_passphrase ? guardada('Guardada') : 'De 10 a 79 caracteres'}) + lat;
    if (p === 'rtmp') h += campo('clave', 'Clave de lectura (opcional)', d.clave, {tipo: 'password', botones: [['ojo', 'Mostrar']], pon: r?.tiene_clave ? guardada('Guardada') : '', ayuda: 'Si el servidor la pide aparte de la dirección.'});
    return h;
  }
  // el otro equipo llama al hub: la dirección y la clave las pone el hub
  let h = campo('clave', 'Clave de publicación propia (opcional)', d.clave, {pon: antes ? guardada('La actual') : 'La genera el hub', largo: 64,
    ayuda: 'De 8 a 64 letras, cifras o guiones. Vacía, ' + (antes ? 'se conserva la que tiene.' : 'el hub genera una y la enseña al guardar.')});
  if (p === 'srt') h += campo('passphrase', 'Passphrase (opcional)', d.passphrase, {tipo: 'password', botones: [['ff-generar', 'Generar'], ['ojo', 'Mostrar']],
      pon: antes?.direccion?.campos?.some(c => /passphrase/i.test(c.nombre)) ? guardada('Guardada') : 'De 10 a 79 caracteres', ayuda: 'Cifra la conexión. El codificador debe llevar la misma.'})
    + campo('puerto', 'Puerto propio (opcional)', d.puerto, {tipo: 'number', min: 1024, max: 65535, pon: 'El del hub',
      ayuda: 'Para codificadores que no admiten stream ID. Con puerto propio no hay clave: protege la passphrase.'}) + lat;
  return h;
}

function pintarFormFuente() {
  const fm = F.form, d = fm.d, m = H.m, edita = !!fm.id, pr = m.protocolos;
  const protos = Object.entries(pr).filter(([, x]) => x.entradas.length);
  const listo = adaptadorListo(d.protocolo), quien = QUIEN_FUENTE[d.protocolo] || {};
  const llama = d.inicia === 'hub', prueba = H.pruebas['fu-form'];
  const direccion = edita && fm.antes.direccion ? `<div class="rotulo">Dirección para el codificador</div><div class="dir" style="margin:5px 0 12px"><code>${esc(fm.antes.direccion.url)}</code>
      <button class="bt chico" type="button" data-a="copiar" data-t="${esc(fm.antes.direccion.url)}">Copiar</button></div>`
    : !llama && !pr[d.protocolo].adaptador ? `<p class="ayuda" style="margin:0 0 12px">El hub da la dirección y la clave para el codificador al guardar la fuente.</p>` : '';
  // lo que dijo la prueba: en HLS, además, las variantes de la lista para elegir cuál entra
  const hls = prueba?.hls?.variantes;
  const variantes = d.protocolo === 'hls' && hls ? `<div class="rotulo" style="margin-top:12px">Variante que entra al hub</div><div class="opcs">
      ${hls.map(v => `<button class="opc fina" type="button" data-a="ff-variante" data-v="${v.indice}" aria-pressed="${v.indice === Number(d.variante)}">${esc(textoVariante(v))}</button>`).join('')}</div>
      <div class="rotulo" style="margin-top:12px">Pistas de audio que entran al hub</div>
      <div class="opcs">${(hls.find(v => v.indice === Number(d.variante))?.audios || []).map(a => `<span class="opc fina" style="cursor:default">${esc(a.nombre || a.idioma || 'Audio')}${a.idioma && a.nombre ? ` (${esc(a.idioma)})` : ''}</span>`).join('') || '<span class="ch gr">La lista no declara pistas de audio aparte: entra el audio de la variante.</span>'}</div>
      <p class="ayuda">Cada destino elige después cuáles de estas pistas envía.</p>` : '';
  poner('#fu-form', `<form class="cols" data-form="fuente" data-envia="ff-guardar" novalidate>
    <div class="caja" style="flex:999 1 620px">
      <div class="cab"><h2>${edita ? `Editar «${esc(fm.antes.nombre)}»` : 'Nueva fuente'}</h2><span class="hueco"></span><button class="bt" type="button" data-a="ff-cancelar">Volver a Fuentes</button></div>
      <div style="max-width:440px">${campo('nombre', 'Nombre', d.nombre, {largo: 80, pon: 'Cámara 1 · Estadio'})}</div>

      <h3 class="tit">1 · Protocolo de entrada</h3>
      <div class="opcs">${protos.map(([p, x]) => `<button class="opc" type="button" data-a="ff-proto" data-v="${p}" data-k="ff-p:${p}" aria-pressed="${p === d.protocolo}"${edita && p !== d.protocolo ? ' disabled' : ''}>${esc(x.nombre)}${adaptadorListo(p) ? '' : '<span class="pt mo"></span>'}</button>`).join('')}</div>
      ${listo ? '' : `<p class="morado" style="margin:8px 0 0">${esc(pr[d.protocolo].nombre)} necesita su adaptador, que no está habilitado en esta estación: la fuente se guarda y espera. <a href="#adaptadores">Ver adaptadores</a></p>`}
      ${edita ? '<p class="ayuda">El protocolo y quién inicia la conexión no se cambian: para otros, crea una fuente nueva.</p>' : ''}

      <h3 class="tit">2 · Quién inicia la conexión</h3>
      <div class="tarjetas">${pr[d.protocolo].entradas.map(i => `<button class="tarjeta" type="button" data-a="ff-inicia" data-v="${i}" data-k="ff-i:${i}" aria-pressed="${i === d.inicia}"${edita && i !== d.inicia ? ' disabled' : ''}>
        <b>${esc(quien[i]?.[0] || i)}</b><span>${esc(quien[i]?.[1] || '')}</span></button>`).join('')}</div>

      <h3 class="tit">3 · En qué dirección viaja la señal</h3>
      <div class="tarjetas">
        <div class="tarjeta puesta"><b>Hacia el hub</b><span>En una fuente la señal siempre entra al hub, inicie quien inicie la conexión.</span></div>
        <div class="tarjeta no"><b>Desde el hub</b><span>Eso es un destino, no una fuente. <a href="#destinos" data-a="ff-a-destino">Crear un destino</a></span></div>
      </div>
      ${esquemaHtml(['Origen', ORIGEN_DE[d.protocolo] || 'Origen'], ['Recibe', equipoHub()], !llama, llama ? 'El hub llama al origen' : 'El origen llama al hub', 'Vídeo y audio viajan del origen al hub')}

      <h3 class="tit">4 · Parámetros</h3>
      ${direccion}<div class="campos">${parametrosFuente(d, fm.antes)}</div>

      <p class="aviso error" id="ff-error" role="alert"${fm.error ? '' : ' hidden'}>${esc(fm.error)}</p>
      <div class="pie-form">
        <button class="bt" type="button" data-a="ff-probar" data-k="ff-probar"${fm.ocupado ? ' disabled' : ''}>Probar fuente</button>
        <button class="bt pri" type="submit" data-k="ff-guardar"${fm.ocupado ? ' disabled' : ''}>${edita ? 'Guardar los cambios' : 'Guardar fuente'}</button>
        <button class="bt" type="button" data-a="ff-cancelar">Cancelar</button>
      </div>
    </div>
    <aside class="vista" style="flex:1 1 380px">
      <div class="caja"><h2>Inspección de la fuente</h2>
        ${prueba ? pruebaHtml(prueba) + variantes : '<p class="ch gr" style="margin:0">Pulsa «Probar fuente» para que el hub compruebe la conexión con lo escrito, sin guardar nada. Con la fuente guardada y con señal, Fuentes enseña el vídeo y las pistas de audio que trae.</p>'}</div>
      <div class="caja"><h2>Al guardar</h2><ol class="pasos-lista">${edita ? `
        <li>Si solo cambia el nombre, no se corta nada.</li>
        <li>Si cambian los datos de la conexión, el hub vuelve a abrir la entrada: la señal y sus rutas se cortan unos segundos y se recuperan solas.</li>
        <li>Con rutas al aire, el hub dice cuáles se cortan y pide confirmarlo antes de guardar.</li>` : `
        <li>${llama ? 'El hub empieza a llamar al origen y reintenta hasta que responda.' : 'El hub abre la entrada y la deja en «Esperando fuente» hasta que llegue señal.'}</li>
        <li>Cuando llega, inspecciona el vídeo y el audio y los muestra en Fuentes.</li>
        <li>Prepara la vista previa para el operador.</li>
        <li>La fuente aparece como una fila nueva en la Matriz de rutas, sin ninguna ruta conectada.</li>`}</ol></div>
    </aside></form>`, true);
}
const textoVariante = v => [v.ancho ? `${v.ancho}×${v.alto}` : null, v.kbps ? num(v.kbps) : null, v.codecs || null].filter(Boolean).join(' · ') || `Variante ${v.indice + 1}`;
ACC['ff-a-destino'] = () => { cerrarFormFuente(); location.hash = '#destinos'; };

// Lo que se manda: al crear, todo lo escrito; al editar, solo lo que cambia (lo vacío se conserva).
function cuerpoFuente(probando) {
  const {d, antes} = F.form, p = d.protocolo, llama = d.inicia === 'hub', c = {};
  if (!antes) { c.protocolo = p; c.inicia = d.inicia; }
  if (!probando) c.nombre = d.nombre.trim();
  if (p === 'ndi' || p === 'zixi') {
    const claves = p === 'ndi' ? ['nombre', 'equipo', 'grupo', 'calidad'] : ['modo', 'host', 'puerto', 'canal'];
    c.opciones = Object.fromEntries(claves.map(k => [k, k === 'puerto' && entero(d.opciones[k]) != null ? entero(d.opciones[k]) : String(d.opciones[k] ?? '').trim()]).filter(([, v]) => v !== ''));
    if (d.clave) c.clave = d.clave;
    return c;
  }
  if (p === 'srt' && d.latencia_ms !== '' && d.latencia_ms != null) c.latencia_ms = Number(d.latencia_ms);
  if (llama) {
    c.url = d.url.trim();
    if (p === 'srt') { if (d.streamid) c.streamid = d.streamid; if (d.passphrase) c.passphrase = d.passphrase; }
    if (p === 'rtmp' && d.clave) c.clave = d.clave;
    if (p === 'hls') c.variante = Number(d.variante) || 0;
  } else {
    if (d.clave) c.clave = d.clave.trim();
    if (p === 'srt') {
      if (d.passphrase) c.passphrase = d.passphrase;
      const puerto = entero(d.puerto), tenia = antes?.opciones?.puerto;
      if (puerto != null && puerto !== tenia) c.puerto = puerto; else if (d.puerto === '' && tenia) c.puerto = 0;
    }
  }
  return c;
}
ACC['ff-probar'] = async () => {
  const fm = F.form;
  fm.error = '';
  H.pruebas['fu-form'] = {probando: true};
  pintarFormFuente();
  try { H.pruebas['fu-form'] = await pide('POST', '/api/probar', {lado: 'fuente', ...(fm.id ? {id: fm.id} : {}), ...cuerpoFuente(true)}); }
  catch (e) { H.pruebas['fu-form'] = {error: e.message}; }
  if (F.form === fm) pintarFormFuente();
};
ACC['ff-guardar'] = async () => {
  const fm = F.form, cuerpo = cuerpoFuente(false);
  if (!cuerpo.nombre) { fm.error = 'Falta el nombre de la fuente'; return pintarFormFuente(); }
  if (fm.id) {            // cambiar la conexión de una fuente al aire corta sus rutas: se pregunta antes
    const a = fm.antes, toca = ['url', 'streamid', 'passphrase', 'clave', 'opciones', 'puerto', 'variante'].some(k => k in cuerpo && !(k === 'url' && cuerpo.url === a.remoto?.url)
      && !(k === 'variante' && cuerpo.variante === (a.opciones?.variante ?? 0)) && !(k === 'opciones' && JSON.stringify(cuerpo.opciones) === JSON.stringify(a.opciones || {})))
      || ('latencia_ms' in cuerpo && cuerpo.latencia_ms !== a.latencia_ms);
    const aire = toca ? alAire({fuente: fm.id}) : [];
    if (aire.length && !await preguntar({titulo: `Guardar los cambios de «${a.nombre}»`, aceptar: 'Guardar y reconectar', clase: 'peligro', html: avisoAire(aire)})) return;
  }
  fm.ocupado = true; fm.error = '';
  pintarFormFuente();
  try {
    const r = fm.id ? await pide('PUT', `/api/fuentes/${fm.id}`, cuerpo) : await pide('POST', '/api/fuentes', cuerpo);
    const id = fm.id || r.id;
    await leerMatriz();
    F.sel = id;
    // recién creada y con dirección del hub: su clave, a la vista un minuto para configurar el codificador
    if (!fm.id && tiene('ver_claves') && fuenteDe(id)?.direccion?.protegida) await ACC.claves({dataset: {lado: 'fuente', id}}).catch(() => {});
    decir(fm.id ? 'Cambios guardados.' : `Fuente «${cuerpo.nombre}» creada.`, 'ok');
    cerrarFormFuente();
  } catch (e) {
    fm.ocupado = false; fm.error = e.message;
    if (F.form === fm) pintarFormFuente();
  }
};
