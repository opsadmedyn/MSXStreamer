// Media Syntaxis Streamer - hub de señales: Ajustes. WebRTC fuera de la red local, los puertos del
// hub, qué se restaura al arrancar, los perfiles de conversión, qué puede hacer cada rol y el
// registro de cambios. Verlo lo puede quien ve el hub; cambiarlo, quien tiene el permiso de ajustes.

const AJ = {datos: null, perfiles: null, cambios: null, limite: 12, t: 0, fallo: ''};

async function leerAjustes() {
  try {
    const [a, p, c] = await Promise.all([pide('GET', '/api/ajustes'), pide('GET', '/api/perfiles'), pide('GET', `/api/cambios?limite=${AJ.limite}`)]);
    AJ.datos = a; AJ.perfiles = p; AJ.cambios = c; AJ.fallo = '';
  } catch (e) { AJ.fallo = e.message; }
  if (H.sec === 'ajustes') SEC.ajustes.pinta();
}
const soloLectura = que => tiene('ajustes') ? '' : ` disabled title="Tu rol (${esc(yo.rol)}) no puede ${que}"`;

SEC.ajustes = {
  entra() { AJ.t = Date.now(); leerAjustes(); },
  pinta() {
    const s = $('#s-ajustes');
    if (!s._montada) {
      s._montada = true;
      s.innerHTML = `<div class="vista"><div id="aj-avisos" hidden></div><div class="cols">
        <div class="vista" style="flex:1 1 560px"><div class="caja" id="aj-webrtc"></div><div class="caja" id="aj-puertos"></div><div class="caja" id="aj-arranque"></div></div>
        <div class="vista" style="flex:1 1 560px"><div class="caja" id="aj-perfiles"></div><div class="caja" id="aj-permisos"></div><div class="caja" id="aj-cambios"></div></div></div></div>`;
    }
    if (Date.now() - AJ.t > 10000) { AJ.t = Date.now(); leerAjustes(); }
    const a = AJ.datos;
    if (!a) { poner('#aj-webrtc', `<div class="vacio">${esc(AJ.fallo || 'Cargando…')}</div>`); return; }
    const problemas = Object.values(a.problemas || {});
    $('#aj-avisos').hidden = !problemas.length && tiene('ajustes');
    poner('#aj-avisos', `${problemas.length ? `<div class="alaire" style="margin:0"><b>El motor avisa:</b><ul>${problemas.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
      ${tiene('ajustes') ? '' : `<p class="ch gr" style="margin:${problemas.length ? '10px' : '0'} 0 0">Tu rol (${esc(yo.rol)}) puede ver los ajustes, pero no cambiarlos.</p>`}`);
    pintarWebrtc(a); pintarPuertos(a); pintarArranque(a); pintarPerfiles(); pintarPermisos(a); pintarCambios();
  },
};
const parAj = (k, v) => `<div class="par"><span>${k}</span><span>${v}</span></div>`;

// ---------------------------------------------------------------- WebRTC fuera de la LAN
function pintarWebrtc(a) {
  const red = a.red, w = a.webrtc, udp = a.puertos.find(p => p.clave === 'webrtc_udp');
  const ice = w.ice.map(s => `<div class="par"><span>${etq('', s.url.split(':')[0].toUpperCase())}</span>
    <span><code class="ch">${esc(s.url)}</code>${s.usuario || s.tiene_clave ? `<br><span class="gr">${s.usuario ? `usuario ${esc(s.usuario)}` : 'sin usuario'} · ${s.tiene_clave ? 'credencial guardada' : 'sin credencial'}</span>` : ''}</span></div>`).join('');
  poner('#aj-webrtc', `<div class="cab"><h2>WebRTC fuera de la LAN</h2><span class="hueco"></span>
      <button class="bt chico" type="button" data-a="aj-red" data-k="aj-red"${soloLectura('cambiar las direcciones')}>Direcciones</button>
      <button class="bt chico" type="button" data-a="aj-ice" data-k="aj-ice"${soloLectura('cambiar los servidores ICE')}>Servidores ICE</button></div>
    <p class="ch gr" style="margin:0 0 8px;max-width:75ch">Un navegador de fuera de la red solo publica o reproduce si el hub tiene HTTPS y una forma de atravesar su router. Dentro de la LAN basta con la primera fila.</p>
    ${parAj('HTTPS', `<code class="ch">${esc(red.webrtc_url)}</code> ${red.https ? etq('bien', 'Correcto') : etq('regular', 'Sin HTTPS', 'Fuera de la red local, los navegadores solo publican y reproducen WebRTC por https://')}`)}
    ${red.https ? '' : '<p class="ayuda" style="margin:0 0 6px">Para usarlo fuera de la red local hace falta un proxy con HTTPS delante del hub y escribir aquí, en «Direcciones», la dirección https:// con que se llega a él.</p>'}
    ${parAj('HLS se entrega en', `<code class="ch">${esc(red.hls_url)}</code>`)}
    ${parAj('Nombre o IP del hub en las direcciones SRT y RTMP', `<code class="ch">${esc(red.host_publico || '—')}</code>`)}
    ${parAj('Dirección pública que anuncia', `${w.hosts.length ? `<code class="ch">${esc(w.hosts.join(', '))}</code>` : 'Solo las de la red local'}${udp ? ` · UDP ${udp.puerto}` : ''}`)}
    <h3 class="sub">Servidores ICE</h3>
    ${ice || '<p class="ch gr" style="margin:0">Ninguno. Sin un servidor STUN o TURN, un navegador de fuera de la red local puede no llegar al hub.</p>'}
    <p class="ayuda" style="margin-top:10px">El hub no puede verse a sí mismo desde fuera: para comprobarlo, abre la página de un destino WebRTC desde una red externa, por ejemplo con los datos de un móvil.</p>`);
}
ACC['aj-red'] = async () => {
  const red = AJ.datos.red;
  const r = await dialogoAjuste({titulo: 'Direcciones con que se llega al hub', leer: d => Object.fromEntries(['host_publico', 'hls_url', 'webrtc_url'].map(k => [k, d.querySelector(`[data-c="${k}"]`).value.trim()])),
    enviar: (v, corta) => pide('PUT', '/api/ajustes/red', {...v, ...(corta ? {corta} : {})}),
    html: `<p class="ch gr">Son las que el hub escribe en las direcciones que entrega. Cambiarlas no corta nada, pero las direcciones ya repartidas dejan de coincidir con las nuevas.</p>
      <div class="campos">${campo('host_publico', 'Nombre o IP con que los equipos llegan al hub', red.host_publico, {ancho: true, pon: 'hub.ejemplo.com', ayuda: 'Sale en las direcciones SRT y RTMP. Sin http://.'})}
        ${campo('hls_url', 'Dirección pública de HLS', red.hls_url_propia ? red.hls_url : '', {ancho: true, pon: red.hls_url_propia ? '' : `Automática: ${red.hls_url}`, ayuda: 'Con https:// si hay un proxy delante. Vacía: la del propio hub.'})}
        ${campo('webrtc_url', 'Dirección pública de WebRTC', red.webrtc_url_propia ? red.webrtc_url : '', {ancho: true, pon: red.webrtc_url_propia ? '' : `Automática: ${red.webrtc_url}`, ayuda: 'Fuera de la red local tiene que ser https://. Vacía: la del propio hub.'})}</div>`});
  if (r) { AJ.datos = r; decir('Direcciones guardadas.', 'ok'); await leerMatriz(); SEC.ajustes.pinta(); }
};
// los servidores ICE se editan en filas dentro del diálogo; «Añadir» y «Quitar» tocan esas filas
const filaIce = (s = {}) => `<div class="ice-fila">
  <input type="text" data-c="url" value="${esc(s.url || '')}" placeholder="stun:stun.ejemplo.com:3478" spellcheck="false" autocomplete="off" aria-label="Servidor">
  <input type="text" data-c="usuario" value="${esc(s.usuario || '')}" placeholder="Usuario (TURN)" spellcheck="false" autocomplete="off" aria-label="Usuario">
  <input type="password" data-c="clave" value="" placeholder="${s.tiene_clave ? 'Guardada (vacío = conservar)' : 'Credencial (TURN)'}" autocomplete="new-password" aria-label="Credencial">
  <button class="bt chico" type="button" data-a="aj-ice-quitar">Quitar</button></div>`;
ACC['aj-ice-mas'] = el => { el.closest('dialog').querySelector('.ice-filas').insertAdjacentHTML('beforeend', filaIce()); };
ACC['aj-ice-quitar'] = el => el.closest('.ice-fila').remove();
ACC['aj-ice'] = async () => {
  const w = AJ.datos.webrtc;
  const r = await dialogoAjuste({titulo: 'WebRTC fuera de la red local', ancho: true,
    leer: d => ({hosts: d.querySelector('[data-c="hosts"]').value.split(/[\s,]+/).filter(Boolean),
      ice: [...d.querySelectorAll('.ice-fila')].map(f => { const v = k => f.querySelector(`[data-c="${k}"]`).value.trim(), clave = f.querySelector('[data-c="clave"]').value;
        return {url: v('url'), usuario: v('usuario'), ...(clave ? {clave} : {})}; }).filter(s => s.url)}),
    enviar: (v, corta) => pide('PUT', '/api/ajustes/webrtc', {...v, ...(corta ? {corta} : {})}),
    html: `<div class="campos">${campo('hosts', 'Direcciones públicas que el hub anuncia', w.hosts.join(', '), {ancho: true, pon: '203.0.113.80', ayuda: 'La IP pública o el nombre por los que un navegador de fuera alcanza al hub. Hasta 8, separadas por comas.'})}</div>
      <h3 class="sub">Servidores ICE</h3>
      <div class="ice-filas">${w.ice.map(filaIce).join('')}</div>
      <p style="margin:8px 0 0"><button class="bt chico" type="button" data-a="aj-ice-mas">Añadir servidor</button></p>
      <p class="ayuda">Un servidor se escribe stun:servidor:3478 o turn:servidor:3478 (con ?transport=tcp si va por TCP). Las credenciales se guardan en la estación y no se vuelven a enseñar.</p>
      <div class="alaire">Guardar un cambio reinicia el servidor WebRTC del hub: quien miraba se queda sin imagen unos 20 segundos y quien publicaba tiene que volver a publicar.</div>`});
  if (r) { AJ.datos = r; decir('Ajustes de WebRTC guardados.', 'ok'); await leerMatriz(); SEC.ajustes.pinta(); }
};

// ---------------------------------------------------------------- puertos
function pintarPuertos(a) {
  const filas = a.puertos.map(p => {
    const e = p.estado, chip = e.ok === true ? etq('bien', 'Abierto', e.detalle) : e.ok === false ? etq('mal', 'No disponible', e.detalle) : etq('apagado', 'Apagado', e.detalle);
    return `<div class="linea"><div>${esc(p.servicio)}</div><div class="n">${p.puerto}/${p.tipo}</div>
      <div>${chip}${e.ok === true ? '' : `<div class="sub" style="overflow-wrap:anywhere">${esc(e.detalle || '')}</div>`}</div>
      <div class="acc">${p.clave ? `<button class="bt chico" type="button" data-a="aj-puerto" data-c="${p.clave}" data-k="aj-p:${p.clave}"${soloLectura('cambiar los puertos')}>Cambiar</button>` : ''}</div></div>`;
  }).join('');
  poner('#aj-puertos', `<h2>Puertos del hub</h2>
    <p class="ch gr" style="margin:0 0 8px;max-width:75ch">Si otro servicio de la estación ocupa un puerto, el hub lo señala aquí y permite cambiarlo sin tocar el resto.</p>
    <div class="rejilla" data-desliza="aj-p" style="--cols:minmax(160px,1.4fr) 110px minmax(120px,1fr) 70px;--ancho:480px"><div>
      <div class="cabeza"><span>Servicio</span><span>Puerto</span><span>Estado</span><span></span></div>${filas}</div></div>
    <p class="ayuda" style="margin-top:8px">Los puertos de SRT, HLS y RTMPS se fijan en config.json. El panel de operación va por el Caddy del Recorder, solo dentro de la red privada.</p>`);
}
ACC['aj-puerto'] = async el => {
  const p = AJ.datos.puertos.find(x => x.clave === el.dataset.c);
  if (!p) return;
  const r = await dialogoAjuste({titulo: `Cambiar el puerto de ${p.servicio}`, aceptar: 'Cambiar el puerto',
    leer: d => { const v = entero(d.querySelector('[data-c="puerto"]').value); if (v == null || v < 1024 || v > 65535) throw new Error('El puerto debe estar entre 1024 y 65535'); return v; },
    enviar: (v, corta) => pide('PUT', '/api/ajustes/red', {[p.clave]: v, ...(corta ? {corta} : {})}),
    html: `<div class="campos">${campo('puerto', `Puerto nuevo (${p.tipo}), ahora el ${p.puerto}`, p.puerto, {tipo: 'number', min: 1024, max: 65535})}</div>
      <div class="alaire">Cambiar un puerto reconecta a quien esté conectado por ese protocolo y cambia las direcciones que el hub entrega: hay que volver a darlas a quien las tenga.</div>`});
  if (r) { AJ.datos = r; decir(`Puerto de ${p.servicio} cambiado.`, 'ok'); await leerMatriz(); SEC.ajustes.pinta(); }
};

// ---------------------------------------------------------------- arranque
function pintarArranque(a) {
  const x = a.arranque, fijo = (t, sub) => `<div class="fijo"><span>${etq('bien', 'Siempre')}</span><div><div>${t}</div><div class="ch gr">${sub}</div></div></div>`;
  poner('#aj-arranque', `<h2>Arranque y restauración</h2>
    ${fijo('Restaurar las rutas habilitadas al arrancar la estación', 'Tras un reinicio, el hub vuelve a activar las fuentes y las rutas que estaban habilitadas. Están guardadas en la base de datos de la estación.')}
    ${fijo('Comprobar que cada ruta sale una sola vez', 'Antes de lanzar un proceso, el hub mira si ya hay uno en marcha para esa ruta y lo conserva en vez de lanzar otro. Así no duplica una emisión.')}
    ${parAj('Último arranque del hub', x ? `${hora(x.t, false)} · ${plural(x.rutas, 'ruta habilitada', 'rutas habilitadas')} · ${plural(x.siguen, 'proceso seguía en marcha y se conservó', 'procesos seguían en marcha y se conservaron')} · ${plural(x.lanzados, 'lanzado', 'lanzados')}${x.reposo ? ' · el servicio estaba apagado' : ''}` : 'Sin datos todavía')}`);
}

// ---------------------------------------------------------------- perfiles de conversión
// sin vídeo o sin audio en el perfil, esa parte se copia, y se convierte solo si el destino no admite su formato
const SE_COPIA = 'Se copia si el destino lo admite';
const textoVideo = p => p.video ? `H.264 · hasta ${p.video.alto}p · ${num(p.video.kbps)}` : SE_COPIA;
function pintarPerfiles() {
  const filas = (AJ.perfiles || []).map(p => `<div class="linea"><div><b>${esc(p.nombre)}</b></div><div class="ch">${textoVideo(p)}</div>
    <div class="ch">${p.audio ? `${p.audio.kbps} kb/s, en AAC u Opus según el destino` : SE_COPIA}</div>
    <div class="ch" title="${esc(p.en_uso.join(', '))}">${p.en_uso.length ? plural(p.en_uso.length, 'destino', 'destinos') : 'Ninguno'}</div>
    <div class="acc">${p.fijo ? '<span class="xs gr">Fijo</span>' : `<button class="bt chico" type="button" data-a="aj-perfil" data-id="${esc(p.id)}" data-k="aj-pf:${esc(p.id)}"${soloLectura('cambiar perfiles')}>Editar</button>
      <button class="bt chico peligro" type="button" data-a="aj-perfil-borrar" data-id="${esc(p.id)}" data-k="aj-pb:${esc(p.id)}"${soloLectura('borrar perfiles')}>Borrar</button>`}</div></div>`).join('');
  poner('#aj-perfiles', `<div class="cab"><h2>Perfiles de conversión</h2><span class="hueco"></span>
      <button class="bt chico pri" type="button" data-a="aj-perfil" data-k="aj-perfil-nuevo"${soloLectura('crear perfiles')}>Nuevo perfil</button></div>
    <p class="ch gr" style="margin:0 0 8px;max-width:75ch">Si varias rutas de la misma fuente piden el mismo perfil, el hub abre una sola conversión y la reparte. El vídeo nunca se agranda: una fuente más pequeña que el perfil conserva su tamaño.</p>
    <div class="rejilla" data-desliza="aj-pf" style="--cols:minmax(120px,1fr) minmax(150px,1.3fr) minmax(110px,1.1fr) 84px 120px;--ancho:620px"><div>
      <div class="cabeza"><span>Perfil</span><span>Vídeo</span><span>Audio</span><span>En uso</span><span></span></div>${filas}</div></div>`);
}
ACC['aj-perfil'] = async el => {
  const p = AJ.perfiles.find(x => x.id === el.dataset.id), nuevo = !p;
  const marca = (c, t, si) => `<label class="interruptor ancho"><input type="checkbox" data-c="${c}"${si ? ' checked' : ''}><span>${t}</span></label>`;
  const aire = p ? H.m.destinos.filter(d => d.ruta?.al_aire && d.ruta.perfil === p.id).map(d => d.nombre) : [];
  const conVideo = nuevo || !!p.video, conAudio = nuevo || !!p.audio;
  const ok = await preguntar({titulo: nuevo ? 'Nuevo perfil de conversión' : `Editar «${p.nombre}»`, aceptar: nuevo ? 'Crear el perfil' : 'Guardar', clase: aire.length ? 'peligro' : 'pri',
    html: `<div class="campos" data-form="perfil">${campo('nombre', 'Nombre', p?.nombre || '', {ancho: true, largo: 60, pon: '540p · 1,5 Mb/s'})}
        ${marca('con-video', 'Convertir el vídeo a H.264 con este tamaño y este bitrate', conVideo)}
        ${campo('alto', 'Alto máximo (líneas)', p?.video?.alto ?? 720, {tipo: 'number', min: 144, max: 2160, ayuda: 'Par, de 144 a 2160. Una fuente más pequeña no se agranda.', fijo: !conVideo})}
        ${campo('vkbps', 'Bitrate de vídeo (kb/s)', p?.video?.kbps ?? 3000, {tipo: 'number', min: 200, max: 60000, fijo: !conVideo})}
        ${marca('con-audio', 'Fijar el bitrate del audio', conAudio)}
        ${campo('akbps', 'Bitrate de audio (kb/s)', p?.audio?.kbps ?? 128, {tipo: 'number', min: 32, max: 512, fijo: !conAudio,
          ayuda: 'Sin marcar, el audio se copia y solo se convierte si el destino no admite su formato.'})}</div>
      ${aire.length ? `<div class="alaire">Este perfil está al aire en ${aire.map(n => `«${esc(n)}»`).join(', ')}. Al guardarlo, sus conversiones se reinician y esas salidas se cortan unos segundos.</div>` : ''}`,
    antes: async d => {
      const v = k => d.querySelector(`[data-c="${k}"]`), n = k => entero(v(k).value);
      const cuerpo = {nombre: v('nombre').value.trim(), video: v('con-video').checked ? {alto: n('alto'), kbps: n('vkbps')} : null, audio: v('con-audio').checked ? {kbps: n('akbps')} : null};
      if (!cuerpo.nombre) throw new Error('Falta el nombre del perfil');
      await (nuevo ? pide('POST', '/api/perfiles', cuerpo) : pide('PUT', `/api/perfiles/${p.id}`, cuerpo));
    }});
  if (!ok) return;
  decir(nuevo ? 'Perfil creado.' : 'Perfil guardado.', 'ok');
  await Promise.all([leerAjustes(), leerMatriz()]);
};
CAMBIA.perfil = (c, v, el) => {                  // lo que no se va a convertir no se rellena
  const campos = {'con-video': ['alto', 'vkbps'], 'con-audio': ['akbps']}[c];
  for (const k of campos || []) el.closest('[data-form]').querySelector(`[data-c="${k}"]`).disabled = !v;
};
ACC['aj-perfil-borrar'] = async el => {
  const p = AJ.perfiles.find(x => x.id === el.dataset.id);
  if (!p) return;
  if (p.en_uso.length) return decir(`El perfil lo usan ${p.en_uso.map(n => `«${n}»`).join(', ')}: cámbialo ahí antes de borrarlo.`);
  if (!await preguntar({titulo: `Borrar el perfil «${p.nombre}»`, html: '<p>Ningún destino lo usa. No se puede deshacer.</p>', aceptar: 'Borrar el perfil', clase: 'peligro'})) return;
  await pide('DELETE', `/api/perfiles/${p.id}`);
  decir('Perfil borrado.', 'ok');
  await Promise.all([leerAjustes(), leerMatriz()]);
};

// ---------------------------------------------------------------- permisos por rol
function pintarPermisos(a) {
  const p = a.permisos, puede = tiene('ajustes');
  const filas = Object.entries(p.nombres).map(([k, texto]) => `<div class="pm-a">${esc(texto)}</div>` + p.roles.map(rol => {
    const si = p.tabla[rol].includes(k), fijo = rol === 'admin' || !puede;
    return `<button class="pm ${si ? 'si' : 'no'}" type="button" data-a="aj-permiso" data-rol="${rol}" data-p="${k}" data-k="pm:${rol}:${k}" aria-pressed="${si}"${fijo ? ' disabled' : ''}
      aria-label="${esc(rol)}: ${esc(texto)} (${si ? 'permitido' : 'no permitido'})">${si ? 'Sí' : 'No'}</button>`;
  }).join('')).join('');
  poner('#aj-permisos', `<h2>Permisos por rol</h2>
    <p class="ch gr" style="margin:0 0 10px;max-width:75ch">Los usuarios y sus roles son los del Recorder. Aquí se decide qué puede hacer cada rol en el Streamer. El administrador siempre puede todo.</p>
    <div class="desliza" data-desliza="aj-pm"><div class="pm-mz"><div class="pm-h izq">Acción</div>${p.roles.map(r => `<div class="pm-h">${esc(r[0].toUpperCase() + r.slice(1))}</div>`).join('')}${filas}</div></div>`);
}
ACC['aj-permiso'] = async el => {
  const p = AJ.datos.permisos, {rol, p: permiso} = el.dataset, tenia = p.tabla[rol].includes(permiso);
  if (permiso === 'ver' && tenia && !await preguntar({titulo: `Quitar a «${rol}» el permiso de ver`, clase: 'peligro', aceptar: 'Quitar el permiso',
    html: `<p>Sin este permiso, quien tenga el rol ${esc(rol)} no verá nada del hub de señales: ni fuentes, ni destinos, ni monitoreo.</p>`})) return;
  const tabla = {operador: [...p.tabla.operador], manager: [...p.tabla.manager]};
  tabla[rol] = tenia ? tabla[rol].filter(x => x !== permiso) : [...tabla[rol], permiso];
  AJ.datos.permisos = await pide('PUT', '/api/ajustes/permisos', tabla);
  decir(`${rol[0].toUpperCase() + rol.slice(1)} ${tenia ? 'ya no puede' : 'ya puede'} ${p.nombres[permiso][0].toLowerCase() + p.nombres[permiso].slice(1)}.`, 'ok');
  pintarPermisos(AJ.datos);
  leerAjustes();
};

// ---------------------------------------------------------------- registro de cambios
function pintarCambios() {
  const c = AJ.cambios || [];
  const filas = c.map(x => `<div class="cambio"><span class="n ch gr">${hora(x.t)}</span>
    <div><div><b>${esc(x.accion)}</b>${x.objeto ? ` <span class="gr">· ${esc(x.objeto)}</span>` : ''}</div>
      <div class="ch gr">${esc(x.quien || 'El hub')}${x.rol ? ` (${esc(x.rol)})` : ''}${x.detalle ? ` · ${esc(x.detalle)}` : ''}</div></div></div>`).join('');
  poner('#aj-cambios', `<h2>Registro de cambios</h2>
    <p class="ch gr" style="margin:0 0 8px;max-width:75ch">Quién cambió qué y cuándo. Las claves, las passphrases y los tokens nunca se escriben en el registro.</p>
    ${filas || '<div class="vacio">Todavía no hay cambios anotados.</div>'}
    ${c.length >= AJ.limite && AJ.limite < 1000 ? `<p style="margin:10px 0 0"><button class="bt chico" type="button" data-a="aj-mas" data-k="aj-mas">Ver más antiguos</button></p>` : ''}`);
}
ACC['aj-mas'] = () => { AJ.limite = Math.min(1000, AJ.limite * 4); return leerAjustes(); };
