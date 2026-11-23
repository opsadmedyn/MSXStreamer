// Media Syntaxis Streamer - hub de señales: Destinos. Dos listas que no se confunden (destinos a los
// que el hub se conecta y direcciones que el hub entrega para que otro equipo las abra) y la ficha
// del elegido, con su conexión, la señal que sale y su reconexión.

const D = {sel: null, form: null, nuevo: null};      // nuevo: el modo elegido en «Nuevo destino», antes de continuar
const COLS_DESTINOS = '14px minmax(170px,1.3fr) 76px minmax(240px,2fr) minmax(150px,1.1fr) 150px 64px';
const QUIEN_DESTINO = {
  hub: {srt: 'El hub llama al receptor (caller)', rtmp: 'El hub publica en el servidor del destino', webrtc: 'El hub publica en el destino (WHIP)', zixi: 'El hub llama al receptor'},
  receptor: {srt: 'El receptor llama al hub (listener)', rtmp: 'Un cliente lee del hub', hls: 'El reproductor pide la lista al hub', webrtc: 'El navegador pide la señal al hub (WHEP)',
    ndi: 'Los receptores NDI de la LAN abren la salida', zixi: 'El receptor llama al hub'},
};
const MODOS_DESTINO = {
  conecta: ['El hub se conecta al destino', 'Tienes la dirección y la clave de una plataforma o de un receptor. El hub inicia la conexión y envía.', 'hub'],
  entrega: ['El hub entrega una dirección', 'Otro equipo abrirá una dirección del hub para recibir la señal. El hub la prepara y te la da para compartirla.', 'receptor'],
};
const protocolosDe = inicia => Object.entries(H.m.protocolos).filter(([, x]) => x.salidas.includes(inicia));

SEC.destinos = {
  pinta() {
    const s = $('#s-destinos');
    if (!s._montada) {
      s._montada = true;
      s.innerHTML = `<div class="cols">
        <div class="vista" style="flex:999 1 720px"><div class="caja" id="de-conecta"></div><div class="caja" id="de-entrega"></div></div>
        <aside class="vista" style="flex:1 1 400px">
          <div class="caja" id="de-ficha" hidden><div id="de-cab"></div><div id="de-dir"></div><div id="de-form"></div><div id="de-prueba" style="margin-top:12px"></div></div>
          <div class="caja" id="de-nuevo" hidden></div>
        </aside></div>`;
    }
    const m = H.m;
    if (!m) { poner('#de-conecta', `<div class="vacio">${esc(H.fallo || 'Cargando…')}</div>`); return; }
    if (!D.nuevo && !(D.form && !D.form.id) && !m.destinos.some(d => d.id === D.sel)) {
      if (m.destinos.length) abrirFichaDestino(m.destinos[0].id);
      else { D.sel = null; D.form = null; }          // un hub recién instalado: sin destinos no hay ficha que abrir
    }
    listaDestinos(m, 'conecta');
    listaDestinos(m, 'entrega');
    $('#de-nuevo').hidden = !D.nuevo;
    $('#de-ficha').hidden = !!D.nuevo || !D.form;
    if (D.nuevo) pintarNuevoDestino(); else if (D.form) fichaDestino();
  },
};

// ---------------------------------------------------------------- listas
function listaDestinos(m, modo) {
  const conecta = modo === 'conecta', perfiles = Object.fromEntries(m.perfiles.map(p => [p.id, p.nombre]));
  const filas = m.destinos.filter(d => d.modo === modo).map(d => {
    const e = estadoDestino(d), id = esc(d.id), r = d.remoto, dir = d.direccion;
    const guardadas = r && [r.tiene_clave && 'clave', r.tiene_passphrase && 'passphrase', r.tiene_streamid && 'stream ID'].filter(Boolean);
    const donde = conecta ? [r?.url || opcionesTexto(d), guardadas?.length ? `Guardada: ${guardadas.join(', ')}` : 'Sin clave guardada']
      : [dir?.url || opcionesTexto(d), !dir ? (d.adaptador ? 'Por su adaptador' : '') : dir.protegida ? 'Protegida con clave' : 'Abierta: sin clave'];
    return `<div class="linea${d.id === D.form?.id && !D.nuevo ? ' elegida' : ''}">
      ${bola(e)}
      <div><button class="nombre corta" type="button" data-a="de-ver" data-id="${id}" data-k="de-n:${id}" title="Abrir su ficha">${esc(d.nombre)}</button></div>
      <div>${etq('', d.protocolo_nombre)}</div>
      <div><div class="ch corta" title="${esc(donde[0])}">${esc(donde[0])}</div><div class="sub">${esc(donde[1])}</div></div>
      <div><div class="ch corta">${esc(perfiles[d.ruta?.perfil || d.perfil] || d.perfil)}</div><div class="sub">${esc(audioTexto(d.ruta?.audio || d.audio, d.protocolo))}</div></div>
      <div>${etqEstado(e)}<div class="sub corta">${d.ruta ? esc(d.ruta.fuente_nombre) : 'Sin fuente'}</div></div>
      <div class="acc">${conecta || !dir ? `<button class="bt chico" type="button" data-a="de-probar" data-id="${id}" data-k="de-p:${id}">Probar</button>`
        : `<button class="bt chico" type="button" data-a="de-copiar" data-id="${id}" data-k="de-c:${id}" title="Copia la dirección${dir.protegida ? ' con su clave, si tu rol puede verla' : ''}">Copiar</button>`}</div></div>`;
  });
  const [titulo, , ] = MODOS_DESTINO[modo];
  poner(conecta ? '#de-conecta' : '#de-entrega', `<div class="cab"><h2>${titulo}</h2><span class="hueco"></span>
      ${conecta ? `<button class="bt pri" type="button" data-a="de-nuevo" data-k="de-nuevo"${sinPermiso('editar', 'crear destinos')}>Nuevo destino</button>` : ''}</div>
    <p class="ch gr" style="margin:0 0 10px;max-width:70ch">${conecta ? 'El hub inicia la conexión y envía la señal. Necesita la dirección y las credenciales de la plataforma o del receptor.'
      : 'El hub espera. Otro equipo abre la dirección para recibir la señal: un reproductor, un navegador, un receptor SRT o un mezclador con NDI.'}</p>
    ${filas.length ? `<div class="rejilla" data-desliza="de-${modo}" style="--cols:${COLS_DESTINOS};--ancho:930px"><div>
      <div class="cabeza"><span></span><span>Destino</span><span>Protocolo</span><span>${conecta ? 'El hub se conecta a' : 'Dirección que se entrega'}</span><span>Señal que sale</span><span>Estado y fuente</span><span></span></div>
      ${filas.join('')}</div></div>` : `<div class="vacio">${conecta ? 'Ningún destino al que el hub se conecte.' : 'Ninguna dirección entregada.'}</div>`}`);
}
const opcionesTexto = d => Object.values(d.opciones || {}).filter(v => typeof v === 'string').join(' · ') || '—';
function audioTexto(audio, protocolo) {
  const varias = protocolo === 'srt' || protocolo === 'hls';
  return String(audio) === 'todas' ? (varias ? 'Todas las pistas de audio' : 'La primera pista de audio') : `Pista de audio ${audio}`;
}

// ---------------------------------------------------------------- ficha
// La parte viva (estado, dirección, prueba) se repinta con cada lectura; el formulario, solo al
// abrirlo o al cambiar su estructura.
function fichaDestino() {
  const fm = D.form, d = fm.id ? H.m.destinos.find(x => x.id === fm.id) : null, f = fm.d;
  const nombreProto = H.m.protocolos[f.protocolo]?.nombre || f.protocolo;
  const e = d ? estadoDestino(d) : null;
  poner('#de-cab', `<div class="cab"><h2 style="font-size:15px;text-transform:none;letter-spacing:0;color:var(--texto)">${d ? esc(d.nombre) : 'Nuevo destino'}</h2>
      ${etq('', nombreProto)}${e ? etqEstado(e) : ''}</div>
    <div class="cifras" style="--n:2">
      <div class="dato txt"><b>Quién inicia la conexión</b><span>${esc(d ? d.quien_inicia : QUIEN_DESTINO[f.inicia][f.protocolo] || '')}</span></div>
      <div class="dato txt"><b>Dirección de la señal</b><span>Desde el hub</span></div>
    </div>
    ${d ? `<p class="ch" style="margin:10px 0 0">${d.ruta ? `Recibe «${esc(d.ruta.fuente_nombre)}» desde las ${hora(d.ruta.desde, false)}.` : 'Sin ruta: ninguna fuente conectada.'} <a href="#rutas">Matriz de rutas</a></p>
      ${e.cls === 'adaptador' ? `<p class="morado" style="margin:8px 0 0">Este destino no puede enviar hasta que su adaptador esté habilitado. <a href="#adaptadores">Ver adaptadores</a></p>` : ''}` : ''}`);
  // la dirección que el hub entrega, con sus claves; la del otro equipo ya está en el formulario
  poner('#de-dir', d?.direccion ? direccionHtml('destino', d)
    : !d && f.inicia === 'receptor' && !H.m.protocolos[f.protocolo].adaptador ? '<p class="ayuda" style="margin-top:10px">El hub prepara la dirección al guardar el destino y la enseña aquí para compartirla.</p>' : '');
  poner('#de-prueba', pruebaHtml(H.pruebas[fm.id || 'de-form']));
}

ACC['de-ver'] = async el => { if (await dejarForm()) abrirFichaDestino(el.dataset.id); };
ACC['de-probar'] = async el => {
  const id = el.dataset.id;
  if (id !== D.sel && !await dejarForm()) return;
  if (id !== D.sel) abrirFichaDestino(id);
  return probar(id, () => pide('POST', `/api/destinos/${id}/probar`));
};
ACC['de-copiar'] = async el => {     // la dirección tal como hay que dársela al otro equipo: con su clave, si quien mira puede verla
  const d = H.m.destinos.find(x => x.id === el.dataset.id);
  if (!d?.direccion) return;
  let url = d.direccion.url, nota = 'Dirección copiada.';
  if (d.direccion.protegida) {
    if (tiene('ver_claves')) { url = (await pide('GET', `/api/destinos/${d.id}/claves`)).url; nota = 'Dirección copiada con su clave. La consulta queda en el registro de cambios.'; }
    else nota = `Dirección copiada sin su clave: tu rol (${yo.rol}) no puede verla.`;
  }
  await copiar(url, el);
  decir(nota, 'ok');
};
ACC['de-borrar'] = async el => {
  const d = H.m.destinos.find(x => x.id === el.dataset.id);
  if (!d) return;
  if (!await preguntar({titulo: `Borrar «${d.nombre}»`, aceptar: 'Borrar el destino', clase: 'peligro',
    html: `<p>Se borra el destino${d.ruta ? ` y su ruta desde «${esc(d.ruta.fuente_nombre)}»` : ''}. No se puede deshacer.</p>${avisoAire(alAire({destino: d.id}))}`})) return;
  await pide('DELETE', `/api/destinos/${d.id}`);
  D.form = null; D.sel = null;
  decir(`Destino «${d.nombre}» borrado.`, 'ok');
  await leerMatriz();
};
async function dejarForm() {         // antes de cambiar de ficha: lo escrito y sin guardar se pierde
  if (!D.form?.sucio) return true;
  return preguntar({titulo: 'Hay cambios sin guardar', html: '<p>Lo escrito en esta ficha se pierde si sales de ella.</p>', aceptar: 'Salir sin guardar', clase: 'peligro', cancelar: 'Seguir editando'});
}

// ---------------------------------------------------------------- nuevo destino
ACC['de-nuevo'] = async () => { if (await dejarForm()) { D.nuevo = {modo: 'conecta'}; D.form = null; SEC.destinos.pinta(); $('#de-nuevo').scrollIntoView({behavior: 'smooth', block: 'nearest'}); } };
ACC['de-modo'] = el => { D.nuevo.modo = el.dataset.v; pintarNuevoDestino(true); };
ACC['de-nuevo-no'] = () => { D.nuevo = null; abrirFichaDestino(D.sel || H.m.destinos[0]?.id || null); };
ACC['de-nuevo-si'] = () => {
  const inicia = MODOS_DESTINO[D.nuevo.modo][2];
  D.nuevo = null;
  abrirFichaDestino(null, inicia);
};
function pintarNuevoDestino(forzar) {
  poner('#de-nuevo', `<h2>Nuevo destino</h2><p style="margin:0 0 10px">¿Cómo recibirá la señal este destino?</p>
    <div class="vista" style="gap:8px">${Object.entries(MODOS_DESTINO).map(([k, [t, txt, inicia]]) => `<button class="tarjeta" type="button" data-a="de-modo" data-v="${k}" data-k="de-m:${k}" aria-pressed="${D.nuevo.modo === k}">
      <b>${t}</b><span>${txt}</span><span class="xs">${protocolosDe(inicia).map(([p, x]) => x.nombre + (p === 'webrtc' ? (inicia === 'hub' ? ' (WHIP)' : ' (WHEP)') : p === 'rtmp' && inicia === 'hub' ? ' · RTMPS' : '')).join(' · ')}</span></button>`).join('')}</div>
    <div class="pie-form"><button class="bt" type="button" data-a="de-nuevo-no">Cancelar</button><button class="bt pri" type="button" data-a="de-nuevo-si" data-k="de-nuevo-si">Continuar</button></div>
    <p class="ayuda">El paso siguiente pide el protocolo y sus parámetros, con el mismo formulario que ves al elegir un destino de la lista.</p>`, forzar);
}

// ---------------------------------------------------------------- formulario
function abrirFichaDestino(id, inicia) {
  const m = H.m, d = id ? m.destinos.find(x => x.id === id) : null, o = d?.opciones || {};
  D.sel = d?.id || (id === null && inicia ? D.sel : null);
  if (!d && !inicia) { D.form = null; return SEC.destinos.pinta(); }
  const ini = d?.inicia || inicia, proto = d?.protocolo || protocolosDe(ini)[0][0];
  D.form = {id: d?.id || null, antes: d, sucio: false, error: '', ocupado: false,
    d: {nombre: d?.nombre || '', protocolo: proto, inicia: ini, url: d?.remoto?.url || '', streamid: '', passphrase: '', clave: '',
      latencia_ms: d ? d.latencia_ms : '', puerto: o.puerto || '', perfil: d?.perfil || 'original', audio: String(d?.audio ?? 'todas'),
      cada_s: d ? d.reconexion.cada_s : 2, max_intentos: d ? d.reconexion.max_intentos : 0, abierta: d ? !d.direccion?.protegida : true,
      opciones: {nombre: o.nombre || '', equipo: o.equipo || '', grupo: o.grupo || '', calidad: o.calidad || '', modo: o.modo || '', host: o.host || '', puerto: o.puerto || '', canal: o.canal || ''}}};
  delete H.pruebas['de-form'];
  SEC.destinos.pinta();
  pintarFormDestino();
  if (!d) { $('#de-ficha').scrollIntoView({behavior: 'smooth', block: 'nearest'}); $('#de-form [data-c="nombre"]')?.focus(); }
}
CAMBIA.destino = (c, v, el) => {
  const fm = D.form, d = fm.d;
  fm.sucio = true;
  if (c.startsWith('op.')) d.opciones[c.slice(3)] = v; else d[c] = v;
  if (c === 'abierta') pintarFormDestino();
};
ACC['df-proto'] = el => {
  const d = D.form.d;
  if (D.form.id || d.protocolo === el.dataset.v) return;
  d.protocolo = el.dataset.v; D.form.sucio = true;
  delete H.pruebas['de-form'];
  fichaDestino(); pintarFormDestino();
};
ACC['df-perfil'] = el => { D.form.d.perfil = el.dataset.v; D.form.sucio = true; pintarFormDestino(); };
ACC['df-audio'] = el => { D.form.d.audio = el.dataset.v; D.form.sucio = true; pintarFormDestino(); };
ACC['df-generar'] = () => {
  D.form.d.passphrase = fraseNueva(); D.form.sucio = true;
  pintarFormDestino();
  const i = $('#de-form [data-c="passphrase"]');
  if (i) { i.type = 'text'; i.select(); }
};
ACC['df-cancelar'] = () => { D.form.sucio = false; abrirFichaDestino(D.sel || H.m.destinos[0]?.id || null); };

function conexionDestino(d, antes) {           // los campos de «Conexión», según el protocolo y quién llama
  const p = d.protocolo, conecta = d.inicia === 'hub', r = antes?.remoto, guardada = t => `${t} (vacío = conservar)`, ojo = [['ojo', 'Mostrar']];
  const lat = campo('latencia_ms', 'Latencia SRT (ms)', d.latencia_ms, {tipo: 'number', min: 20, max: 8000, pon: 'La del hub'});
  if (p === 'ndi') return campo('op.nombre', 'Nombre con el que se publica en NDI', d.opciones.nombre, {ancho: true, pon: 'MSX-HUB (Programa)'}) + campo('op.grupo', 'Grupo NDI (opcional)', d.opciones.grupo, {pon: 'public'});
  if (p === 'zixi') return (conecta ? campo('op.host', 'Equipo Zixi remoto', d.opciones.host, {pon: 'zixi.ejemplo.com'}) + campo('op.puerto', 'Puerto', d.opciones.puerto, {tipo: 'number', min: 1, max: 65535, pon: '2088'}) : '')
    + campo('op.canal', 'Canal (stream ID de Zixi)', d.opciones.canal) + campo('clave', 'Clave del canal (opcional)', d.clave, {tipo: 'password', botones: ojo, pon: antes?.adaptador?.tiene_clave ? guardada('Guardada') : ''});
  if (conecta) {
    const que = {srt: ['Dirección del receptor', 'srt://203.0.113.20:9000'], rtmp: ['Servidor', 'rtmp://a.rtmp.youtube.com/live2'], webrtc: ['Dirección WHIP del destino', 'https://ejemplo.com/whip/sala']}[p];
    let h = campo('url', que[0], d.url, {ancho: true, pon: que[1], ayuda: p === 'rtmp' ? 'Con rtmps:// la conexión va cifrada.' : ''});
    if (p === 'srt') h += campo('streamid', 'Stream ID (opcional)', d.streamid, {pon: r?.tiene_streamid ? guardada('Guardado') : ''})
      + lat + campo('passphrase', 'Passphrase (opcional)', d.passphrase, {tipo: 'password', ancho: true, botones: ojo, pon: r?.tiene_passphrase ? guardada('Guardada') : 'De 10 a 79 caracteres'});
    else h += campo('clave', p === 'rtmp' ? 'Clave de emisión' : 'Token (Bearer), si lo pide', d.clave, {tipo: 'password', ancho: true, botones: ojo, pon: r?.tiene_clave ? guardada('Guardada') : ''});
    return h;
  }
  // el hub entrega la dirección: la clave la pone el hub, salvo en HLS, que puede ir abierto
  if (p === 'hls') return `<label class="interruptor ancho"><input type="checkbox" data-c="abierta" data-k="c:abierta"${d.abierta ? ' checked' : ''}>
      <span>Abierta: cualquier reproductor que llegue a la dirección puede verla.<small class="ayuda">Sin marcar, el hub pide usuario y contraseña, que da junto con la dirección. Muchos reproductores de televisión no saben mandarlos.</small></span></label>`;
  let h = campo('clave', 'Clave propia (opcional)', d.clave, {pon: antes ? guardada('La actual') : 'La genera el hub', largo: 64, ancho: true,
    ayuda: 'De 8 a 64 letras, cifras o guiones. Vacía, ' + (antes ? 'se conserva la que tiene.' : 'el hub genera una.')});
  if (p === 'srt') h = campo('passphrase', 'Passphrase (opcional)', d.passphrase, {tipo: 'password', ancho: true, botones: [['df-generar', 'Generar'], ...ojo],
      pon: antes?.direccion?.campos?.some(c => /passphrase/i.test(c.nombre)) ? guardada('Guardada') : 'De 10 a 79 caracteres', ayuda: 'Cifra la conexión. El receptor debe llevar la misma.'}) + h
    + campo('puerto', 'Puerto propio (opcional)', d.puerto, {tipo: 'number', min: 1024, max: 65535, pon: 'El del hub', ayuda: 'Para receptores que no admiten stream ID. Con puerto propio no hay clave: protege la passphrase.'}) + lat;
  return h;
}

function pintarFormDestino() {
  const fm = D.form, d = fm.d, m = H.m, edita = !!fm.id, conecta = d.inicia === 'hub', puede = tiene('editar');
  const perfil = m.perfiles.find(p => p.id === d.perfil) || m.perfiles[0], varias = d.protocolo === 'srt' || d.protocolo === 'hls';
  // las pistas que se pueden elegir: las de la fuente conectada, o cuatro si aún no hay ninguna
  const fuente = fm.antes?.ruta ? m.fuentes.find(f => f.id === fm.antes.ruta.fuente) : null, pistas = fuente?.pistas?.audio || [];
  const numeros = pistas.length ? pistas.map(p => [String(p.pista), nombrePista(p)]) : [1, 2, 3, 4].map(n => [String(n), `Pista ${n}`]);
  if (d.audio !== 'todas' && !numeros.some(([n]) => n === d.audio)) numeros.push([d.audio, `Pista ${d.audio}`]);
  const opc = (accion, v, t, actual) => `<button class="opc fina" type="button" data-a="${accion}" data-v="${esc(v)}" data-k="${accion}:${esc(v)}" aria-pressed="${String(v) === String(actual)}"${puede ? '' : ' disabled'}>${esc(t)}</button>`;
  poner('#de-form', `<form data-form="destino" data-envia="df-guardar" novalidate><fieldset${puede ? '' : ' disabled'} style="border:0;padding:0;margin:0;min-width:0">
    <h3 class="sub">Conexión</h3>
    <div class="campos">${campo('nombre', 'Nombre', d.nombre, {largo: 80, ancho: true, pon: conecta ? 'YouTube · Canal principal' : 'Web · HLS'})}
      ${edita ? '' : `<div class="ancho"><div class="rotulo">Protocolo</div><div class="opcs">${protocolosDe(d.inicia).map(([p, x]) => `<button class="opc fina" type="button" data-a="df-proto" data-v="${p}" data-k="df-p:${p}" aria-pressed="${p === d.protocolo}">${esc(x.nombre)}${adaptadorListo(p) ? '' : '<span class="pt mo"></span>'}</button>`).join('')}</div>
        ${adaptadorListo(d.protocolo) ? '' : `<p class="morado" style="margin:8px 0 0">${esc(m.protocolos[d.protocolo].nombre)} necesita su adaptador, que no está habilitado: el destino se guarda y espera. <a href="#adaptadores">Ver adaptadores</a></p>`}</div>`}
      ${conexionDestino(d, fm.antes)}</div>
    <p class="ayuda">${conecta ? 'Las claves del otro equipo se guardan solo en la base de datos de la estación, no aparecen en los registros y no se vuelven a enseñar.'
      : 'Solo quien tiene permiso para ver claves puede ver las de esta dirección, y cada consulta queda anotada en el registro de cambios.'}</p>

    <h3 class="sub">Señal que sale</h3>
    <div class="rotulo">Perfil de salida</div>
    <div class="opcs">${m.perfiles.map(p => opc('df-perfil', p.id, p.nombre, d.perfil)).join('')}</div>
    <div class="cifras" style="margin-top:10px">
      <div class="dato txt"><b>Vídeo</b><span>${perfil.video ? `Hasta ${perfil.video.alto}p` : 'Como llega'}</span></div>
      <div class="dato txt"><b>Bitrate de vídeo</b><span>${perfil.video ? num(perfil.video.kbps) : 'El de la fuente'}</span></div>
      <div class="dato txt"><b>Audio</b><span>${perfil.audio ? `${perfil.audio.kbps} kb/s` : 'Como llega'}</span></div>
    </div>
    <p class="ayuda">${perfil.video ? 'Con este perfil el hub convierte el vídeo: es la ruta que más carga la estación. La conversión se comparte entre los destinos con el mismo perfil.'
      : 'El hub copia lo que el destino acepta y convierte solo lo que no. La matriz dice qué hará antes de activar la ruta.'}</p>
    <div class="rotulo" style="margin-top:10px">Pistas de audio que se envían</div>
    <div class="opcs">${opc('df-audio', 'todas', varias ? 'Todas' : 'La primera', d.audio)}${numeros.map(([n, t]) => opc('df-audio', n, t, d.audio)).join('')}</div>
    <p class="ayuda">${varias ? `${esc(m.protocolos[d.protocolo].nombre)} puede llevar varias pistas de audio a la vez.` : `${esc(m.protocolos[d.protocolo]?.nombre || '')} lleva una sola pista de audio: elige cuál.`}
      ${pistas.length ? ` Son las pistas de «${esc(fuente.nombre)}», la fuente conectada.` : ''}${edita && fm.antes.ruta ? ' La ruta activa conserva su perfil y su audio: se cambian en la Matriz de rutas.' : ''}</p>

    <h3 class="sub">Reconexión</h3>
    ${conecta ? `<div class="campos">${campo('cada_s', 'Reintentar cada (s)', d.cada_s, {tipo: 'number', min: 0, max: 600, ayuda: '0: con espera creciente.'})}
        ${campo('max_intentos', 'Intentos máximos', d.max_intentos, {tipo: 'number', min: 0, max: 100000, ayuda: '0: sin límite.'})}</div>
      <p class="ayuda">Si este destino cae, solo se reintenta esta salida. Las demás rutas siguen enviando.</p>`
      : '<p class="ayuda" style="margin-top:0">El hub mantiene la dirección disponible y espera a que el receptor vuelva a abrirla. No hay reintentos que programar.</p>'}
    <p class="aviso error" role="alert"${fm.error ? '' : ' hidden'}>${esc(fm.error)}</p>
    ${puede ? `<div class="pie-form">
      <button class="bt" type="button" data-a="df-probar" data-k="df-probar"${fm.ocupado ? ' disabled' : ''}>Probar conexión</button>
      <button class="bt pri" type="submit" data-k="df-guardar"${fm.ocupado ? ' disabled' : ''}>${edita ? 'Guardar cambios' : 'Crear destino'}</button>
      ${edita ? `<span class="hueco"></span><button class="bt peligro" type="button" data-a="de-borrar" data-id="${esc(fm.id)}">Borrar</button>` : '<button class="bt" type="button" data-a="df-cancelar">Cancelar</button>'}
    </div>` : ''}</fieldset>
    ${puede ? '' : `<div class="pie-form"><button class="bt" type="button" data-a="de-probar" data-id="${esc(fm.id || '')}" data-k="de-probar-ficha">Probar conexión</button>
      <span class="ch gr">Tu rol (${esc(yo.rol)}) puede ver los destinos y probarlos, pero no cambiarlos.</span></div>`}</form>`, true);
}

function cuerpoDestino(probando) {
  const {d, antes} = D.form, p = d.protocolo, conecta = d.inicia === 'hub', c = {};
  if (!antes) { c.protocolo = p; c.inicia = d.inicia; }
  if (!probando) c.nombre = d.nombre.trim();
  c.perfil = d.perfil; c.audio = d.audio;
  if (conecta) c.reconexion = {cada_s: Number(d.cada_s) || 0, max_intentos: Number(d.max_intentos) || 0};
  if (p === 'ndi' || p === 'zixi') {
    const claves = p === 'ndi' ? ['nombre', 'equipo', 'grupo', 'calidad'] : ['modo', 'host', 'puerto', 'canal'];
    c.opciones = Object.fromEntries(claves.map(k => [k, k === 'puerto' && entero(d.opciones[k]) != null ? entero(d.opciones[k]) : String(d.opciones[k] ?? '').trim()]).filter(([, v]) => v !== ''));
    if (d.clave) c.clave = d.clave;
    return c;
  }
  if (p === 'srt' && d.latencia_ms !== '' && d.latencia_ms != null) c.latencia_ms = Number(d.latencia_ms);
  if (conecta) {
    c.url = d.url.trim();
    if (p === 'srt') { if (d.streamid) c.streamid = d.streamid; if (d.passphrase) c.passphrase = d.passphrase; }
    else if (d.clave) c.clave = d.clave;
  } else if (p === 'hls') {
    const protegida = !!antes?.direccion?.protegida;       // abrirla quita la clave; protegerla pone una nueva
    if (d.abierta && protegida) c.clave = ''; else if (!d.abierta && !protegida) c.clave = fraseNueva(24);
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
ACC['df-probar'] = async () => {
  const fm = D.form, clave = fm.id || 'de-form';
  fm.error = '';
  await probar(clave, () => pide('POST', '/api/probar', {lado: 'destino', ...(fm.id ? {id: fm.id} : {}), ...cuerpoDestino(true)}));
};
ACC['df-guardar'] = async () => {
  const fm = D.form, cuerpo = cuerpoDestino(false), a = fm.antes;
  if (!cuerpo.nombre) { fm.error = 'Falta el nombre del destino'; return pintarFormDestino(); }
  if (fm.id) {            // cambiar la conexión de un destino al aire lo corta: se pregunta antes
    const toca = ['streamid', 'passphrase', 'clave', 'puerto'].some(k => k in cuerpo) || ('url' in cuerpo && cuerpo.url !== a.remoto?.url)
      || ('latencia_ms' in cuerpo && cuerpo.latencia_ms !== a.latencia_ms) || ('opciones' in cuerpo && JSON.stringify(cuerpo.opciones) !== JSON.stringify(a.opciones || {}));
    const aire = toca ? alAire({destino: fm.id}) : [];
    if (aire.length && !await preguntar({titulo: `Guardar los cambios de «${a.nombre}»`, aceptar: 'Guardar y reconectar', clase: 'peligro', html: avisoAire(aire)})) return;
  }
  fm.ocupado = true; fm.error = '';
  pintarFormDestino();
  try {
    const r = fm.id ? await pide('PUT', `/api/destinos/${fm.id}`, cuerpo) : await pide('POST', '/api/destinos', cuerpo);
    const id = fm.id || r.id;
    await leerMatriz();
    D.form.sucio = false;
    abrirFichaDestino(id);
    decir(fm.id ? 'Cambios guardados.' : `Destino «${cuerpo.nombre}» creado. ${cuerpo.inicia === 'receptor' ? 'Su dirección está en la ficha.' : 'Conéctale una fuente en la Matriz de rutas.'}`, 'ok');
  } catch (e) {
    fm.ocupado = false; fm.error = e.message;
    if (D.form === fm) pintarFormDestino();
  }
};
