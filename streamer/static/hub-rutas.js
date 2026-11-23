// Media Syntaxis Streamer - hub de señales: Matriz de rutas. Fuentes en filas y destinos en columnas.
// Un destino recibe una sola fuente; una fuente alimenta a todos los destinos que haga falta. Antes
// de activar un cruce el hub dice qué copia, qué convierte y por qué, y qué fuente se quita si el
// destino ya tenía una.

const R = {
  sel: null,           // {f, d}: el cruce elegido
  perfil: {},          // «fuente:destino» → perfil elegido para ese cruce, aún sin aplicar
  audio: {},           // lo mismo, con el audio
  plan: null,          // {clave, t, pidiendo, datos, error}: el plan del cruce elegido
  confirma: null,      // cruce cuya desconexión se está confirmando
  ocupado: false,      // hay un cambio de ruta en curso
};
const LEYENDA = [['enviando', 'Enviando'], ['esperando_receptor', 'Esperando receptor'], ['esperando_fuente', 'Esperando fuente'],
  ['reconectando', 'Reconectando'], ['error', 'Error'], ['adaptador', 'Adaptador pendiente']];
const ACCION_PISTA = {copia: ['bien', 'Se copia'], convierte: ['regular', 'Se convierte'], omite: ['apagado', 'No se envía'], imposible: ['mal', 'No se puede']};
const variasPistas = protocolo => protocolo === 'srt' || protocolo === 'hls';

SEC.rutas = {
  pinta() {
    const s = $('#s-rutas'), m = H.m;
    if (!s._montada) {
      s._montada = true;
      s.innerHTML = '<div class="vista"><div class="caja" id="ru-matriz"></div><div class="caja" id="ru-cruce"></div></div>';
    }
    if (!m) { poner('#ru-matriz', `<div class="vacio">${esc(H.fallo || 'Cargando…')}</div>`); return; }
    if (!m.fuentes.length || !m.destinos.length) {
      $('#ru-cruce').hidden = true;
      return poner('#ru-matriz', `<h2>Matriz de rutas</h2><div class="vacio">Para trazar una ruta hacen falta al menos una fuente y un destino.
        ${m.fuentes.length ? '' : '<a href="#fuentes">Añadir una fuente</a>'} ${m.destinos.length ? '' : '<a href="#destinos">Añadir un destino</a>'}</div>`);
    }
    $('#ru-cruce').hidden = false;
    if (!cruceActual()) {            // de entrada, la primera ruta que haya
      const con = m.destinos.find(d => d.ruta);
      R.sel = con ? {f: con.ruta.fuente, d: con.id} : {f: m.fuentes[0].id, d: m.destinos[0].id};
    }
    pintarMatriz(m);
    pintarCruce();
  },
};

// El cruce elegido, con lo que la matriz sabe de él. `ruta` solo si este cruce es la ruta del destino.
function cruceActual() {
  const m = H.m, f = m?.fuentes.find(x => x.id === R.sel?.f), d = m?.destinos.find(x => x.id === R.sel?.d);
  if (!f || !d) return null;
  const k = f.id + ':' + d.id, ruta = d.ruta?.fuente === f.id ? d.ruta : null;
  const perfil = R.perfil[k] ?? ruta?.perfil ?? d.perfil, audio = String(R.audio[k] ?? ruta?.audio ?? d.audio);
  return {f, d, k, ruta, perfil, audio, otra: ruta ? null : d.ruta,
    cambiado: !!ruta && (perfil !== ruta.perfil || audio !== String(ruta.audio)),
    falta: [f, d].filter(o => o.adaptador && !o.adaptador.listo),
    permiso: d.ruta?.al_aire ? 'modificar_al_aire' : 'conectar_libre'};
}
const nombrePerfil = id => H.m.perfiles.find(p => p.id === id)?.nombre || id;
const textoAudio = (audio, protocolo) => audio === 'todas' ? (variasPistas(protocolo) ? 'todas las pistas' : 'la primera pista') : `la pista ${audio}`;

// ---------------------------------------------------------------- la matriz
function pintarMatriz(m) {
  const cols = m.destinos, sel = R.sel || {};
  const cabeza = cols.map(d => `<div class="mz-d"><span class="nombre corta" title="${esc(d.nombre)}">${esc(d.nombre)}</span>
    <span>${etq('', d.protocolo_nombre)}</span><span class="xs gr">${d.modo === 'entrega' ? 'El hub entrega' : 'El hub se conecta'}</span></div>`).join('');
  const filas = m.fuentes.map(f => {
    const ef = estadoFuente(f), faltaF = f.adaptador && !f.adaptador.listo;
    return `<div class="mz-f">${bola(ef)}<div><span class="nombre corta" title="${esc(f.nombre)}">${esc(f.nombre)}</span>
        <div>${etq('', f.protocolo_nombre)} <span class="xs gr">${esc(ef.nombre)}${ef.cls === 'recibiendo' && f.kbps ? ' · ' + num(f.kbps) : ''}</span></div></div></div>`
      + cols.map(d => {
        const r = d.ruta?.fuente === f.id ? d.ruta : null, e = r ? estadoDestino(d) : null, falta = faltaF || (d.adaptador && !d.adaptador.listo);
        const que = e ? e.nombre : falta ? 'falta un adaptador' : 'sin ruta';
        return `<button class="mz-c${r ? ' ruta' : ''}${falta && !r ? ' falta' : ''}" type="button" data-a="ru-ver" data-f="${esc(f.id)}" data-d="${esc(d.id)}" data-k="mz:${esc(f.id)}:${esc(d.id)}"
          aria-pressed="${sel.f === f.id && sel.d === d.id}" aria-label="${esc(f.nombre)} hacia ${esc(d.nombre)}: ${esc(que)}" title="${esc(f.nombre)} hacia ${esc(d.nombre)}: ${esc(que)}">
          ${r ? `${etq(e.cls, e.nombre)}<span>${e.cls === 'adaptador' ? 'Ruta guardada' : esc(nombrePerfil(r.perfil))}</span>` : ''}</button>`;
      }).join('');
  }).join('');
  poner('#ru-matriz', `<div class="cab"><h2>Matriz de rutas</h2>
      <span class="leyenda">${LEYENDA.map(([c, t]) => etq(c, t)).join('')}</span><span class="hueco"></span>
      <span class="xs gr">${tiene('modificar_al_aire') ? 'Tu rol puede modificar rutas al aire' : tiene('conectar_libre') ? 'Tu rol no puede modificar rutas al aire' : 'Tu rol solo puede ver las rutas'}</span></div>
    <div class="desliza" data-desliza="matriz"><div class="mz" style="--n:${cols.length}">
      <div class="mz-esq"><span>Destinos en columnas</span><span>Fuentes en filas</span></div>${cabeza}${filas}</div></div>`);
}
ACC['ru-ver'] = el => {
  R.sel = {f: el.dataset.f, d: el.dataset.d}; R.confirma = null;
  pintarMatriz(H.m); pintarCruce(true);
};

// ---------------------------------------------------------------- el plan
const clavePlan = c => [c.f.id, c.d.id, c.perfil, c.audio].join('|');
const pidePlan = c => pide('POST', '/api/rutas/plan', {fuente: c.f.id, destino: c.d.id, perfil: c.perfil, audio: c.audio});
async function seguirPlan(c) {        // el plan del cruce elegido, al día mientras se mira
  const clave = clavePlan(c), antes = R.plan?.clave === clave ? R.plan : null;
  if (antes && (antes.pidiendo || Date.now() - antes.t < 8000)) return;
  const p = R.plan = {clave, t: Date.now(), pidiendo: true, datos: antes?.datos || null, error: ''};
  try { p.datos = await pidePlan(c); } catch (e) { p.error = e.message; p.datos = null; }
  p.pidiendo = false; p.t = Date.now();
  if (R.plan === p && H.sec === 'rutas') pintarCruce();
}
// Qué hace el hub con cada pista. En el aviso de activar va también el porqué.
function filasPlan(plan, conMotivo) {
  const filas = [...(plan.video ? [['Vídeo', plan.video]] : []), ...(plan.audio || []).map(a => [a.nombre || `Pista ${a.pista}`, a])];
  return filas.map(([pista, x]) => {
    const [cls, txt] = ACCION_PISTA[x.accion] || ['', x.accion];
    const detalle = x.accion === 'convierte' ? `${x.de} pasa a ${x.a}` : x.accion === 'omite' ? `${x.de}, no sale por este destino` : x.accion === 'imposible' ? x.de : `${x.de}, sin cambios`;
    return `<div class="pl"><span class="pl-a">${etq(cls, txt)}</span><span class="pl-t"><b>${esc(pista)}</b> <span class="gr">· ${esc(detalle)}</span>
      ${conMotivo && x.motivo ? `<small>${esc(x.motivo)}</small>` : ''}</span></div>`;
  }).join('');
}
// lo que el plan dice además de las pistas: carga, avisos y si esta combinación está validada
function notasPlan(p, enAviso) {
  const plan = p.plan, v = p.validacion, avisos = (plan.avisos || []).filter(a => a !== plan.resumen);
  return `${plan.carga ? `<p class="ch" style="margin:10px 0 0"><b>Carga en la estación.</b> ${esc(plan.carga)}</p>` : ''}
    ${avisos.map(a => `<p class="ch gr" style="margin:6px 0 0">${esc(a)}</p>`).join('')}
    ${v ? `<p class="ch" style="margin:10px 0 0"><span class="gr">Validación de esta forma de conexión:</span> ${etq(v.nivel, v.nombre, v.nota)}${enAviso ? '' : ' <a href="#adaptadores">Ver detalle</a>'}</p>` : ''}`;
}

// ---------------------------------------------------------------- el cruce elegido
function pintarCruce(forzar) {
  const c = cruceActual(), m = H.m;
  if (!c) return;
  seguirPlan(c);
  const {f, d, ruta} = c, e = ruta ? estadoDestino(d) : null, p = R.plan?.clave === clavePlan(c) ? R.plan : null, plan = p?.datos?.plan;
  const puede = tiene(c.permiso), alAire = !!d.ruta?.al_aire;
  // izquierda: qué pasa en este cruce y con qué evidencia
  const izq = `<div class="cruce-tit"><b>${esc(f.nombre)}</b><span class="hacia" aria-label="hacia">→</span><b>${esc(d.nombre)}</b></div>
    <div class="fila" style="gap:8px;margin:8px 0">${e ? etqEstado(e) : etq('apagado', 'Sin ruta')}
      ${ruta ? `<span class="xs gr">Activada a las ${hora(ruta.desde, false)}${ruta.por ? ` por ${esc(ruta.por)}` : ''}</span>` : ''}</div>
    ${ruta ? `<div class="rotulo">Evidencia</div>${evidenciaHtml(e)}`
      : `<p class="ch" style="margin:0">${c.falta.length ? 'No se puede enviar todavía: falta un adaptador en la fuente o en el destino.' : 'No hay ruta entre esta fuente y este destino.'}</p>
        ${c.falta.map(o => `<p class="ch morado" style="margin:6px 0 0">${esc(o.adaptador.falta)}</p>`).join('')}`}
    ${c.otra ? `<div class="alaire">${c.otra.al_aire ? `«${esc(d.nombre)}» está al aire con «${esc(c.otra.fuente_nombre)}». Conectar este cruce cambia su fuente.`
      : `«${esc(d.nombre)}» ya tiene conectada «${esc(c.otra.fuente_nombre)}», que ahora no está al aire (${esc(c.otra.estado.nombre.toLowerCase())}). Conectar este cruce la sustituye.`}</div>` : ''}`;
  // centro: el perfil y el audio del cruce, y lo que el hub hará con cada pista
  const opc = (accion, v, t, actual) => `<button class="opc fina" type="button" data-a="${accion}" data-v="${esc(v)}" data-k="${accion}:${esc(v)}" aria-pressed="${String(v) === String(actual)}">${esc(t)}</button>`;
  const pistas = (f.pistas?.audio || []).map(x => [String(x.pista), nombrePista(x)]);
  if (c.audio !== 'todas' && !pistas.some(([n]) => n === c.audio)) pistas.push([c.audio, `Pista ${c.audio}`]);
  const centro = `<div class="rotulo">Perfil de este cruce</div>
    <div class="opcs">${m.perfiles.map(x => opc('ru-perfil', x.id, x.nombre, c.perfil)).join('')}</div>
    <div class="rotulo" style="margin-top:10px">Audio que se envía</div>
    <div class="opcs">${opc('ru-audio', 'todas', variasPistas(d.protocolo) ? 'Todas las pistas' : 'La primera', c.audio)}${pistas.map(([n, t]) => opc('ru-audio', n, t, c.audio)).join('')}</div>
    <div class="rotulo" style="margin-top:12px">Qué hace el hub con cada pista</div>
    ${p?.error ? `<p class="ch" style="margin:0;color:#ff8a9a">${esc(p.error)}</p>`
      : !plan ? '<p class="ch gr" style="margin:0">Calculando el plan…</p>'
      : !plan.posible ? `<p class="ch" style="margin:0;color:#ff8a9a">No se puede activar esta ruta. ${esc(plan.resumen || '')}</p>${filasPlan(plan, true)}`
      : plan.video || plan.audio?.length ? filasPlan(plan, false)
      : `<p class="ch gr" style="margin:0">${esc(plan.adaptador ? plan.resumen : c.falta.length ? 'Las pistas se inspeccionarán cuando el adaptador esté habilitado y la fuente tenga señal.' : plan.resumen)}</p>`}
    ${plan ? notasPlan(p.datos) : ''}`;
  // derecha: lo que quien mira puede hacer aquí
  const noPuede = `<p class="ch" style="margin:0">${alAire ? `Tu rol (${esc(yo.rol)}) no puede modificar rutas al aire. Pide el cambio a quien tenga ese permiso.` : `Tu rol (${esc(yo.rol)}) solo puede ver las rutas.`}</p>`;
  const ocupado = R.ocupado ? ' disabled' : '';
  let der;
  if (ruta) {
    der = `${e.cls === 'adaptador' ? '<p class="ch morado" style="margin:0 0 10px">Ruta guardada, pendiente de adaptador. <a href="#adaptadores">Ver qué falta</a></p>' : ''}
      ${!puede ? noPuede : R.confirma === c.k
        ? `<p class="ch" style="margin:0 0 8px">${alAire ? 'Esta ruta está al aire.' : 'Esta ruta está habilitada.'} ¿Cortar la señal hacia «${esc(d.nombre)}»?</p>
          <div class="fila" style="gap:8px"><button class="bt peligro" type="button" data-a="ru-cortar" data-k="ru-cortar"${ocupado}>Sí, desconectar</button><button class="bt" type="button" data-a="ru-mantener" data-k="ru-mantener">Mantener</button></div>`
        : `${c.cambiado ? `<button class="bt pri" type="button" data-a="ru-conectar" data-k="ru-conectar"${ocupado}>Aplicar el cambio</button>
            <button class="bt" type="button" data-a="ru-descartar">Descartar</button>
            <p class="ayuda" style="margin:6px 0 12px">Has elegido otro perfil u otro audio para esta ruta${alAire ? ', que está al aire: al aplicarlo esta salida se reinicia' : ''}.</p>` : ''}
          <button class="bt" type="button" data-a="ru-desconectar" data-k="ru-desconectar"${ocupado}>Desconectar</button>`}
      ${['error', 'reconectando'].includes(e.cls) && tiene('conectar_libre') ? `<p style="margin:10px 0 0"><button class="bt" type="button" data-a="ru-reintentar" data-k="ru-reintentar"${ocupado}>Reintentar ahora</button></p>` : ''}`;
  } else if (!puede) der = noPuede;
  else if (plan && !plan.posible) der = '<p class="ch gr" style="margin:0">Este cruce no se puede conectar: mira qué pista lo impide.</p>';
  else if (c.falta.length) {
    der = `<p class="ch" style="margin:0 0 8px">Este cruce necesita un adaptador que aún no está habilitado. <a href="#adaptadores">Ver adaptadores</a></p>
      <button class="bt" type="button" data-a="ru-conectar" data-k="ru-conectar"${ocupado}>Guardar la ruta</button>
      <p class="ayuda">La ruta queda guardada y empieza a enviar cuando el adaptador esté habilitado.</p>`;
  } else {
    der = `<button class="bt pri" type="button" data-a="ru-conectar" data-k="ru-conectar"${ocupado}>${c.otra ? 'Cambiar la fuente' : 'Conectar'}</button>
      <p class="ayuda">Antes de activar verás qué pistas se copian y cuáles se convierten.</p>`;
  }
  poner('#ru-cruce', `<h2>Cruce seleccionado</h2><div class="cols cruce">
    <div style="flex:2 1 380px;min-width:0">${izq}</div>
    <div style="flex:2 1 380px;min-width:0">${centro}</div>
    <div style="flex:1 1 240px;min-width:0">${der}${ruta ? `<p class="ch" style="margin:14px 0 0"><a href="#monitoreo" data-a="ru-monitoreo">Ver en Monitoreo</a></p>` : ''}</div></div>`, forzar);
}
ACC['ru-perfil'] = el => { const c = cruceActual(); if (c) { R.perfil[c.k] = el.dataset.v; pintarCruce(true); } };
ACC['ru-audio'] = el => { const c = cruceActual(); if (c) { R.audio[c.k] = el.dataset.v; pintarCruce(true); } };
ACC['ru-descartar'] = () => { const c = cruceActual(); if (c) { delete R.perfil[c.k]; delete R.audio[c.k]; pintarCruce(true); } };
ACC['ru-desconectar'] = () => { R.confirma = cruceActual()?.k || null; pintarCruce(true); };
ACC['ru-mantener'] = () => { R.confirma = null; pintarCruce(true); };
ACC['ru-monitoreo'] = () => { const c = cruceActual(); if (c) H.foco = {ruta: c.d.id}; location.hash = 'monitoreo'; };

async function conRuta(hacer) {       // un cambio de ruta: uno cada vez, y la matriz al día al acabar
  if (R.ocupado) return;
  R.ocupado = true; pintarCruce(true);
  try { await hacer(); }
  finally { R.ocupado = false; R.plan = null; await leerMatriz(); if (H.sec === 'rutas') pintarCruce(true); }
}
ACC['ru-cortar'] = () => conRuta(async () => {
  const c = cruceActual();
  if (!c?.ruta) return;
  await pide('DELETE', `/api/rutas/${c.d.id}`);
  R.confirma = null; delete R.perfil[c.k]; delete R.audio[c.k];
  decir(`Ruta desconectada: «${c.d.nombre}» ya no recibe «${c.f.nombre}».`, 'ok');
});
ACC['ru-reintentar'] = () => conRuta(async () => {
  const c = cruceActual();
  if (!c?.ruta) return;
  await pide('POST', `/api/rutas/${c.d.id}/reintentar`);
  decir('El hub vuelve a intentarlo ahora.', 'ok');
});

// ---------------------------------------------------------------- activar
// Se pide el plan otra vez al abrir el aviso: lo que se confirma es lo que hay en ese momento. Si
// mientras tanto el destino cambió de fuente, el hub no acepta el cambio y se vuelve a preguntar.
function avisoActivar(c, p, nota) {
  const a = p.actual, plan = p.plan, desde = a ? hora(a.desde, false) : '';
  const cambio = !p.sustituye ? '' : a.al_aire
    ? `<div class="alaire"><b>Este destino ya está al aire</b><br>«${esc(c.d.nombre)}» emite ahora «${esc(a.fuente_nombre)}» desde las ${desde}. Si continúas, esa señal se corta y entra «${esc(c.f.nombre)}».</div>`
    : `<div class="alaire"><b>Este destino ya tiene una ruta</b><br>«${esc(c.d.nombre)}» está conectado a «${esc(a.fuente_nombre)}» desde las ${desde} (${esc(a.estado_nombre.toLowerCase())}). Si continúas, esa ruta se sustituye por «${esc(c.f.nombre)}».</div>`;
  const reinicia = c.ruta && a?.al_aire ? `<div class="alaire"><b>Esta ruta está al aire</b><br>Cambiar su perfil o su audio reinicia esta salida: «${esc(c.d.nombre)}» deja de recibir señal unos segundos.</div>` : '';
  const pistas = plan.video || plan.audio?.length;
  return `${nota ? `<div class="alaire" style="margin:0 0 12px">${esc(nota)}</div>` : ''}
    <div class="cruce-tit"><b>${esc(c.f.nombre)}</b><span class="hacia" aria-label="hacia">→</span><b>${esc(c.d.nombre)}</b></div>
    <p class="ch gr" style="margin:4px 0 0">Perfil: ${esc(nombrePerfil(p.perfil))} · Audio: ${esc(textoAudio(String(p.audio), c.d.protocolo))}</p>
    ${cambio}${reinicia}
    <p style="margin:14px 0 8px">${pistas ? 'El hub inspeccionó la fuente. Esto es lo que hará con cada pista:' : esc(plan.resumen || '')}</p>
    ${pistas ? filasPlan(plan, true) : ''}
    ${plan.provisional ? '<p class="ch gr" style="margin:8px 0 0">La ruta queda activada y espera: el hub decide qué copia y qué convierte cuando la fuente tenga señal, y lo enseña aquí y en Monitoreo.</p>' : ''}
    ${notasPlan(p, true)}`;
}
ACC['ru-conectar'] = () => conRuta(async () => {
  let nota = '';
  for (;;) {
    const c = cruceActual();
    if (!c) return;
    const p = await pidePlan(c);
    R.plan = {clave: clavePlan(c), t: Date.now(), pidiendo: false, datos: p, error: ''};
    pintarCruce(true);
    if (!p.puedes) return decir(p.permiso === 'modificar_al_aire' ? `Tu rol (${yo.rol}) no puede modificar rutas al aire, y «${c.d.nombre}» lo está.` : `Tu rol (${yo.rol}) no puede conectar rutas.`);
    if (!p.plan.posible) return decir(`No se puede activar esta ruta. ${p.plan.resumen || ''}`);
    const aire = !!p.actual?.al_aire;
    const boton = p.sustituye ? (aire ? 'Cambiar fuente al aire' : 'Cambiar la fuente') : c.ruta ? 'Aplicar el cambio' : c.falta.length ? 'Guardar la ruta' : 'Activar ruta';
    if (!await preguntar({titulo: c.ruta ? 'Cambiar una ruta activa' : 'Activar ruta', html: avisoActivar(c, p, nota), aceptar: boton, clase: aire ? 'peligro' : 'pri', ancho: true})) return;
    try {
      const r = await pide('PUT', `/api/rutas/${c.d.id}`, {fuente: c.f.id, perfil: p.perfil, audio: String(p.audio), ...(p.sustituye ? {sustituye: p.sustituye} : {})});
      delete R.perfil[c.k]; delete R.audio[c.k];
      return decir(r.sin_cambios ? 'La ruta ya estaba así.' : c.falta.length ? `Ruta guardada: «${c.f.nombre}» hacia «${c.d.nombre}», pendiente de adaptador.`
        : c.ruta ? `Cambio aplicado a «${c.d.nombre}».` : `Ruta activada: «${c.f.nombre}» hacia «${c.d.nombre}».`, 'ok');
    } catch (e) {
      if (e.estado !== 409 || !e.detalle?.sustituye) throw e;
      nota = 'El destino cambió mientras confirmabas. Esto es lo que tiene ahora.';
      await leerMatriz();
    }
  }
});
