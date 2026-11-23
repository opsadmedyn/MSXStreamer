#!/usr/bin/env node
// Media Syntaxis Streamer - la prueba principal de aceptación, hecha desde el panel.
//
// scripts/aceptacion.py hace la prueba por la API y lo mide todo (también la carga del equipo).
// Esta hace la primera mitad pulsando: abre el panel en un Chromium sin ventana, con una sesión
// de administrador, y hace lo que haría un operador:
//
//   1. en Fuentes, da de alta una fuente RTMP y toma la dirección que el hub da para el codificador;
//   2. publica ahí una señal de pruebas (ffmpeg: vídeo H.264 y un tono en AAC);
//   3. en Destinos, da de alta tres: SRT (el hub llama a un receptor), HLS y WebRTC;
//   4. en la Matriz de rutas, conecta la fuente a los tres y apunta lo que el hub dice que copiará
//      y convertirá antes de activar cada ruta;
//   5. comprueba audio y vídeo en los tres: lo que guarda el receptor SRT y lo que reproducen las
//      páginas HLS y WebRTC que entrega el hub, abiertas en un navegador;
//   6. apaga el receptor SRT, comprueba que HLS y WebRTC siguen reproduciendo y que Monitoreo lo
//      dice, lo vuelve a encender y comprueba que la ruta SRT se recupera sola;
//   7. borra lo que creó.
//
// Lo que crea lleva un nombre que empieza por «Aceptación del panel · ». Si una pasada anterior se
// quedó a medias (un corte de luz, un cierre a destiempo), al empezar borra lo que dejó: lo reconoce
// por ese nombre y no toca nada más.
//
// La cámara y los receptores son programas: lo que pasa aquí vale como «verificado en el banco de
// pruebas», nunca como verificado con equipos reales (docs/validacion.md).
//
//   MSXS_SESION=<cookie msr_sesion de un administrador> node scripts/aceptacion_panel.js \
//       [--hub http://127.0.0.1:8095] [--receptor 127.0.0.1] [--corte 60] [--capturas carpeta]
//
// --receptor es la dirección de esta máquina vista desde el hub. Necesita ffmpeg y ffprobe con
// libsrt y Playwright con su Chromium (CHROMIUM=/ruta usa otro navegador). Sale con 0 si todo
// pasa y con 1 si algo falla.

const fs = require('fs');
const os = require('os');
const path = require('path');
const dgram = require('dgram');
const { spawn, spawnSync } = require('child_process');
let chromium;
try { ({ chromium } = require('playwright')); } catch (e) { ({ chromium } = require('playwright-core')); }   // el de las estaciones

const arg = (nombre, defecto) => { const i = process.argv.indexOf('--' + nombre); return i > 0 ? process.argv[i + 1] : defecto; };
const HUB = arg('hub', 'http://127.0.0.1:8095').replace(/\/+$/, '');
const RECEPTOR = arg('receptor', '127.0.0.1');
const CORTE = Number(arg('corte', '60'));
const CAPTURAS = arg('capturas', '');
const SESION = process.env.MSXS_SESION || '';
const FFMPEG = process.env.FFMPEG || 'ffmpeg', FFPROBE = process.env.FFPROBE || 'ffprobe';
const PREFIJO = 'Aceptación del panel · ';      // lo que crea la prueba se llama así, y solo eso borra
const PARADA_MAX = 4;                            // s seguidos sin que avance un reproductor

const esperar = ms => new Promise(r => setTimeout(r, ms));
const hora = () => new Date().toLocaleTimeString('es', { hour12: false });
const decir = texto => console.log(`${hora()}  ${texto}`);
const fallos = [];
function comprobar(que, ok, detalle = '') {
  decir(`   ${ok ? 'bien' : 'FALLA'}: ${que}${detalle ? ` (${detalle})` : ''}`);
  if (!ok) fallos.push(que);
  return ok;
}
async function hasta(condicion, segundos, cada = 500) {         // espera a que algo se cumpla; devuelve lo que dio
  const fin = Date.now() + segundos * 1000;
  for (;;) {
    const v = await condicion();
    if (v || Date.now() > fin) return v;
    await esperar(cada);
  }
}
const puertoLibre = () => new Promise((ok, mal) => {            // un puerto UDP que nadie usa, para el receptor SRT
  const s = dgram.createSocket('udp4');
  s.once('error', mal);
  s.bind(0, () => { const p = s.address().port; s.close(() => ok(p)); });
});

(async () => {
  if (!SESION) { console.error('Falta MSXS_SESION: la cookie msr_sesion de un administrador del panel.'); process.exit(2); }
  const carpeta = fs.mkdtempSync(path.join(os.tmpdir(), 'msxs-panel-'));
  const procesos = [];
  const lanzar = (orden, opciones = {}) => { const p = spawn(orden[0], orden.slice(1), { stdio: ['ignore', 'ignore', 'ignore'], ...opciones }); procesos.push(p); return p; };
  const navegador = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  const ctx = await navegador.newContext({ viewport: { width: 1500, height: 1100 } });
  await ctx.addCookies([{ name: 'msr_sesion', value: SESION, url: HUB }]);
  const pag = await ctx.newPage();
  const errores = [];
  pag.on('pageerror', e => errores.push(String(e.message || e).slice(0, 200)));
  const api = async (metodo, ruta) => (await ctx.request.fetch(HUB + ruta, { method: metodo })).ok();
  const ir = async seccion => { await pag.goto(`${HUB}/#${seccion}`); await pag.waitForSelector(`#s-${seccion}:not([hidden])`, { timeout: 15000 }); await esperar(700); };
  const foto = async nombre => { if (CAPTURAS) { fs.mkdirSync(CAPTURAS, { recursive: true }); await pag.screenshot({ path: path.join(CAPTURAS, nombre + '.png'), fullPage: true }); } };
  const idDe = (lista, accion, nombre) => pag.locator(`${lista} [data-a="${accion}"]`).filter({ hasText: nombre }).first().getAttribute('data-id');
  const creado = { fuente: null, destinos: {} };
  let paginas = null, acabando = false;

  let limpieza = null, interrumpida = false;
  const limpiar = () => limpieza || (limpieza = (async () => {      // una sola vez, la pida quien la pida
    acabando = true;
    for (const p of procesos) { try { p.kill('SIGKILL'); } catch (e) { /* ya no está */ } }
    for (const id of Object.values(creado.destinos)) { await api('DELETE', `/api/rutas/${id}`).catch(() => {}); await api('DELETE', `/api/destinos/${id}`).catch(() => {}); }
    if (creado.fuente) await api('DELETE', `/api/fuentes/${creado.fuente}`).catch(() => {});
    await navegador.close().catch(() => {});
    fs.rmSync(carpeta, { recursive: true, force: true });
  })());
  for (const aviso of ['SIGINT', 'SIGTERM', 'SIGHUP']) {             // si la cierran a medias, no deja nada en el hub
    process.once(aviso, () => { interrumpida = true; decir('Interrumpida: se borra lo que creó la prueba'); limpiar().then(() => process.exit(130)); });
  }
  async function restos() {                                          // lo que dejó una pasada anterior que no llegó a borrar
    const r = await ctx.request.get(HUB + '/api/matriz');
    if (!r.ok()) throw new Error(`el panel no acepta esa sesión (respondió ${r.status()}): MSXS_SESION es la cookie msr_sesion de un administrador`);
    const m = await r.json(), suyo = x => String(x.nombre || '').startsWith(PREFIJO);
    const destinos = (m.destinos || []).filter(suyo), fuentes = (m.fuentes || []).filter(suyo);
    for (const d of destinos) { await api('DELETE', `/api/rutas/${d.id}`); await api('DELETE', `/api/destinos/${d.id}`); }
    for (const f of fuentes) await api('DELETE', `/api/fuentes/${f.id}`);
    if (destinos.length + fuentes.length) decir(`   había restos de una pasada anterior y se borraron: ${fuentes.length} ${fuentes.length === 1 ? 'fuente' : 'fuentes'} y ${destinos.length} ${destinos.length === 1 ? 'destino' : 'destinos'}`);
  }

  try {
    // ---------------------------------------------------------------- 1 · la fuente
    decir('1 · Fuentes: alta de una fuente RTMP');
    await restos();
    await ir('fuentes');
    await pag.click('[data-a="fu-nueva"]');
    await pag.fill('#fu-form [data-c="nombre"]', PREFIJO + 'cámara RTMP');
    await pag.click('[data-k="ff-p:rtmp"]');
    await pag.click('[data-k="ff-i:origen"]');
    await pag.click('[data-k="ff-guardar"]');
    await pag.waitForSelector(`#fu-titulo:has-text("${PREFIJO}cámara RTMP")`, { timeout: 15000 });
    creado.fuente = await idDe('#fu-lista', 'fu-ver', PREFIJO + 'cámara RTMP');
    // al crearla, el panel enseña la dirección con su clave durante un minuto a quien puede ver claves
    if (await pag.locator('#fu-det [data-a="claves"]').count()) await pag.click('#fu-det [data-a="claves"]');
    const publicar = await hasta(async () => { const t = (await pag.locator('#fu-det .dir code').first().innerText()).trim(); return t.includes('***') ? '' : t; }, 10);
    if (!comprobar('El panel da la dirección para el codificador, con su clave', /^rtmp:\/\//.test(publicar || ''), publicar ? publicar.replace(/pass=[^&]+/, 'pass=…') : 'la sesión no puede ver claves')) throw new Error('sin dirección para publicar');

    decir('2 · Una cámara de pruebas publica en esa dirección');
    const camara = () => {                           // como un codificador de verdad: si el hub aún no atiende o se corta, vuelve a publicar
      if (acabando) return;
      lanzar([FFMPEG, '-hide_banner', '-nostdin', '-loglevel', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000', '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-pix_fmt', 'yuv420p',
        '-b:v', '2500k', '-maxrate', '2500k', '-bufsize', '5000k', '-g', '60', '-bf', '0', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2', '-f', 'flv', publicar])
        .on('exit', () => setTimeout(camara, 1500));
    };
    camara();
    const conSenal = await hasta(async () => /recibiendo medios/i.test(await pag.locator('#fu-titulo').innerText()), 40);
    comprobar('Fuentes enseña la fuente «Recibiendo medios»', conSenal, (await pag.locator('#fu-titulo').innerText()).replace(/\s+/g, ' ').slice(0, 120));
    await foto('1-fuente');

    // ---------------------------------------------------------------- 3 · los destinos
    decir('3 · Destinos: alta de un receptor SRT, un reproductor HLS y un navegador WebRTC');
    const puerto = await puertoLibre();
    async function altaDestino(clave, nombre, modo, protocolo, campos = {}) {
      await ir('destinos');
      await pag.click('[data-a="de-nuevo"]');
      await pag.click(`[data-k="de-m:${modo}"]`);
      await pag.click('[data-k="de-nuevo-si"]');
      await pag.fill('#de-form [data-c="nombre"]', PREFIJO + nombre);
      await pag.click(`[data-k="df-p:${protocolo}"]`);
      for (const [c, v] of Object.entries(campos)) await pag.fill(`#de-form input[data-c="${c}"]`, v);
      await pag.click('[data-k="df-guardar"]');
      const fila = pag.locator('#s-destinos [data-a="de-ver"]').filter({ hasText: PREFIJO + nombre }).first();
      await fila.waitFor({ timeout: 15000 });
      creado.destinos[clave] = await fila.getAttribute('data-id');
      await fila.click();
      await esperar(600);
    }
    await altaDestino('srt', 'receptor SRT', 'conecta', 'srt', { url: `srt://${RECEPTOR}:${puerto}`, latencia_ms: '300' });
    await altaDestino('hls', 'reproductor HLS', 'entrega', 'hls');
    const dirHls = (await pag.locator('#de-dir .dir code').first().innerText()).trim();
    await altaDestino('webrtc', 'navegador WebRTC', 'entrega', 'webrtc');
    if (await pag.locator('#de-dir [data-a="claves"]').count()) await pag.click('#de-dir [data-a="claves"]');
    const web = await hasta(() => pag.evaluate(() => {
      const caja = document.querySelector('#de-dir'), dirs = [...caja.querySelectorAll('.dir code')].map(c => c.textContent.trim());
      const token = [...caja.querySelectorAll('.par')].map(p => p.lastElementChild.textContent.trim()).find(t => /^[\w-]+:.+/.test(t)) || '';
      return token && !token.includes('***') ? { pagina: dirs.find(d => /^https?:\/\/.*\/$/.test(d)) || '', token } : null;
    }), 10);
    comprobar('El panel da las direcciones de los tres destinos', Object.keys(creado.destinos).length === 3 && /\.m3u8$/.test(dirHls) && !!(web && web.pagina),
      `HLS ${dirHls} · WebRTC ${web ? web.pagina : 'sin página'}`);

    // ---------------------------------------------------------------- 4 · las rutas
    decir('4 · Matriz de rutas: la fuente, a los tres destinos');
    const receptor = { n: 0, p: null, archivo: '' };
    const abrirReceptor = () => {                     // un ffmpeg a la escucha que guarda lo que llega; cada vez, una captura nueva
      receptor.archivo = path.join(carpeta, `receptor-${++receptor.n}.ts`);
      receptor.p = lanzar([FFMPEG, '-hide_banner', '-nostdin', '-loglevel', 'error', '-scan_all_pmts', '0', '-i', `srt://0.0.0.0:${puerto}?mode=listener&latency=300000`,
        '-map', '0', '-c', 'copy', '-f', 'mpegts', '-flush_packets', '1', '-y', receptor.archivo]);
    };
    const recibido = () => { try { return fs.statSync(receptor.archivo).size; } catch (e) { return 0; } };
    abrirReceptor();
    await ir('rutas');
    for (const [clave, id] of Object.entries(creado.destinos)) {
      await pag.click(`[data-k="mz:${creado.fuente}:${id}"]`);
      // como un operador: espera a que el hub termine de inspeccionar la fuente y diga qué hará con cada pista
      await hasta(async () => { const t = await pag.locator('#ru-cruce').innerText(); return /se copia|se convierte/i.test(t) && !/falta saber|no se ha inspeccionado/i.test(t); }, 45);
      await pag.click('[data-k="ru-conectar"]');
      await pag.waitForSelector('#h-dlg[open]', { timeout: 10000 });
      const plan = (await pag.locator('#h-dlg .cuerpo').innerText()).replace(/\s+/g, ' ').trim();
      decir(`   ${clave.toUpperCase()}, antes de activar: ${plan.slice(0, 260)}`);
      await pag.click('#h-dlg [data-dlg="si"]');
      await pag.waitForSelector('#h-dlg[open]', { state: 'detached', timeout: 15000 });      // se cierra al activarse la ruta
      comprobar(`${clave.toUpperCase()}: el panel dice qué copia y qué convierte antes de activar`, /se copia|se convierte/i.test(plan));
    }
    const conRuta = await hasta(async () => (await pag.locator(`.mz-c.ruta[data-f="${creado.fuente}"]`).count()) === 3, 15);
    comprobar('La matriz enseña las tres rutas activas a la vez', conRuta);
    await foto('2-matriz');

    // ---------------------------------------------------------------- 5 · audio y vídeo en los tres
    decir('5 · Audio y vídeo en las tres salidas');
    await ir('monitoreo');
    const estadoDe = id => pag.evaluate(i => { const b = document.querySelector(`[data-k="mo:ruta:${i}"]`); return b ? b.closest('.linea').innerText.replace(/\s+/g, ' ').trim() : ''; }, id);
    for (const k of ['hls', 'webrtc']) {              // como un operador: no da la dirección hasta que el hub dice que está lista
      const lista = await hasta(async () => { const t = await estadoDe(creado.destinos[k]); return /^(esperando receptor|enviando)/i.test(t) ? t : ''; }, 45) || await estadoDe(creado.destinos[k]);
      comprobar(`Monitoreo dice cuándo está lista la dirección ${k === 'hls' ? 'HLS' : 'WebRTC'} («Esperando receptor»)`, /^(esperando receptor|enviando)/i.test(lista), lista.slice(0, 150));
    }
    const config = path.join(carpeta, 'paginas.json');   // lleva la clave de lectura: solo para quien corre la prueba
    const [usuario, clave] = web ? [web.token.slice(0, web.token.indexOf(':')), web.token.slice(web.token.indexOf(':') + 1)] : ['', ''];
    fs.writeFileSync(config, JSON.stringify({ paginas: [{ nombre: 'hls', url: dirHls.replace(/[^/]+$/, '') },
      { nombre: 'webrtc', url: web ? web.pagina : '', usuario, clave }] }), { mode: 0o600 });
    const muestras = [];
    paginas = spawn(process.execPath, [path.join(__dirname, 'aceptacion_navegador.js'), config], { stdio: ['pipe', 'pipe', 'ignore'] });
    procesos.push(paginas);
    let resto = '';
    paginas.stdout.on('data', d => { const lineas = (resto + d).split('\n'); resto = lineas.pop(); for (const l of lineas) { try { muestras.push(JSON.parse(l)); } catch (e) { /* una línea a medias */ } } });
    const ultima = () => muestras[muestras.length - 1] || {};
    const avanceHls = () => {                         // lo que ha avanzado el reproductor HLS desde que tuvo imagen, en segundos
      const con = muestras.map(m => (m.hls || {}).video || {}).filter(v => v.ancho > 0);
      return con.length ? con[con.length - 1].tiempo - con[0].tiempo : 0;
    };
    const hlsVe = m => { const v = (m.hls || {}).video; return !!v && v.ancho > 0 && v.bytes_video > 0 && v.bytes_audio > 0 && avanceHls() >= 2; };
    const rtcVe = m => { const r = (m.webrtc || {}).rtc || {}; return (r.video || {}).fotogramas > 30 && (r.audio || {}).paquetes > 30 && (r.audio || {}).energia > 0; };
    await hasta(() => recibido() > 1e6 && hlsVe(ultima()) && rtcVe(ultima()), 60);
    const sonda = archivo => {                         // qué pistas trae de verdad lo que guardó el receptor
      const r = spawnSync(FFPROBE, ['-v', 'error', '-count_packets', '-show_entries', 'stream=codec_type,codec_name,nb_read_packets', '-of', 'json', archivo], { encoding: 'utf8', timeout: 60000 });
      try { return JSON.parse(r.stdout).streams || []; } catch (e) { return []; }
    };
    const conPistas = pistas => ['video', 'audio'].every(t => pistas.some(s => s.codec_type === t && Number(s.nb_read_packets) > 20));
    const texto = pistas => pistas.map(s => `${s.codec_type} ${s.codec_name}, ${s.nb_read_packets} paquetes`).join(' · ') || 'no llegó nada';
    let pistas = sonda(receptor.archivo);
    comprobar('SRT: el receptor recibe vídeo y audio', conPistas(pistas), texto(pistas));
    const v = (ultima().hls || {}).video || {}, r = (ultima().webrtc || {}).rtc || {};
    comprobar('HLS: el reproductor del navegador decodifica vídeo y audio', hlsVe(ultima()), `${v.ancho || 0}×${v.alto || 0}, ${avanceHls().toFixed(1)} s reproducidos desde que tuvo imagen`);
    comprobar('WebRTC: el navegador recibe vídeo y audio', rtcVe(ultima()),
      `${(r.video || {}).ancho || 0}×${(r.video || {}).alto || 0}, ${(r.video || {}).fotogramas || 0} fotogramas, audio ${(r.audio || {}).codec || 'sin audio'}`
      + (rtcVe(ultima()) ? '' : ` · conexión ${r.estado || 'sin abrir'} · ${muestras.length} lecturas del navegador · ${JSON.stringify((ultima().webrtc || {}).texto || '').slice(0, 120)}`));

    for (const [k, id] of Object.entries(creado.destinos)) {
      const linea = await hasta(async () => { const t = await estadoDe(id); return /^enviando/i.test(t) ? t : ''; }, 20) || await estadoDe(id);
      comprobar(`Monitoreo enseña ${k.toUpperCase()} «Enviando», con su evidencia`, /^enviando/i.test(linea), linea.slice(0, 170));
    }
    await foto('3-monitoreo');

    // ---------------------------------------------------------------- 6 · el corte
    decir(`6 · El receptor SRT se apaga ${CORTE} s`);
    const t0 = Date.now(), desde = muestras.length;
    receptor.p.kill('SIGKILL');
    await esperar(Math.min(CORTE, 20) * 1000);
    const cortada = await hasta(async () => { const t = await estadoDe(creado.destinos.srt); return /^(reconectando|error)/i.test(t) ? t : ''; }, 15) || await estadoDe(creado.destinos.srt);
    comprobar('Monitoreo enseña la ruta SRT «Reconectando» y dice por qué', /^reconectando/i.test(cortada), cortada.slice(0, 200));
    for (const k of ['hls', 'webrtc']) comprobar(`Monitoreo sigue enseñando ${k.toUpperCase()} «Enviando» durante el corte`, /^enviando/i.test(await estadoDe(creado.destinos[k])));
    await foto('4-corte');
    await esperar(Math.max(0, CORTE * 1000 - (Date.now() - t0)));
    const parada = serie => {                          // el tramo más largo sin que la serie avance, en segundos
      let peor = 0, ultimo = serie[0];
      for (const [t, x] of serie) { if (x > ultimo[1]) ultimo = [t, x]; else peor = Math.max(peor, t - ultimo[0]); }
      return Math.round(peor * 10) / 10;
    };
    const tramo = muestras.slice(desde);
    const paradaHls = parada(tramo.map(m => [m.t, ((m.hls || {}).video || {}).tiempo || 0]));
    const paradaRtc = parada(tramo.map(m => [m.t, (((m.webrtc || {}).rtc || {}).video || {}).fotogramas || 0]));
    comprobar('HLS sigue reproduciendo durante el corte', tramo.length > CORTE / 2 && paradaHls <= PARADA_MAX, `parada más larga ${paradaHls} s`);
    comprobar('WebRTC sigue reproduciendo durante el corte', tramo.length > CORTE / 2 && paradaRtc <= PARADA_MAX, `parada más larga ${paradaRtc} s`);

    decir('   el receptor SRT vuelve');
    abrirReceptor();
    const t1 = Date.now();
    const vuelve = await hasta(() => recibido() > 2e5, 90, 250);
    comprobar('La ruta SRT se recupera sola al volver el receptor', !!vuelve, vuelve ? `a los ${((Date.now() - t1) / 1000).toFixed(1)} s` : 'no llegó nada en 90 s');
    await esperar(8000);
    pistas = sonda(receptor.archivo);
    comprobar('SRT: tras recuperarse, el receptor vuelve a recibir vídeo y audio', conPistas(pistas), texto(pistas));
    const recuperada = await hasta(async () => { const t = await estadoDe(creado.destinos.srt); return /^enviando/i.test(t) ? t : ''; }, 30) || await estadoDe(creado.destinos.srt);
    comprobar('Monitoreo vuelve a enseñar la ruta SRT «Enviando»', /^enviando/i.test(recuperada), recuperada.slice(0, 170));
    await foto('5-recuperada');
    comprobar('El panel no dio ningún error de programa', errores.length === 0, errores.slice(0, 3).join(' · '));
  } catch (e) {
    if (!interrumpida) {                              // al interrumpirla se cierra el navegador: eso no es un fallo de la prueba
      comprobar('La prueba llegó hasta el final', false, String(e.message || e).split('\n')[0]);
      await foto('fallo').catch(() => {});
    }
  } finally {
    if (!interrumpida) decir('7 · Se borra lo que creó la prueba');
    await limpiar();
  }
  if (interrumpida) return;                          // el aviso de cierre ya borra y termina
  decir(fallos.length ? `FALLA: ${fallos.length} ${fallos.length === 1 ? 'comprobación' : 'comprobaciones'} (${fallos.join('; ')})` : 'PASA: todas las comprobaciones');
  process.exit(fallos.length ? 1 : 0);
})();
