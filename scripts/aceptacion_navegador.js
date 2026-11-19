#!/usr/bin/env node
// Media Syntaxis Streamer - prueba de aceptación: el navegador.
//
// Lo lanza scripts/aceptacion.py. Abre en un Chromium sin ventana las páginas que el hub entrega
// (la del reproductor HLS y la del monitor WebRTC, las mismas que abriría una persona) y escribe
// cada segundo, en una línea JSON, lo que el navegador está reproduciendo de verdad: el tiempo del
// vídeo, su tamaño, los bytes de audio y de vídeo decodificados y, en WebRTC, los fotogramas
// decodificados y la energía del audio recibido. Así la prueba no se fía de que «la página abrió».
//
//   node aceptacion_navegador.js '{"paginas": [{"nombre": "hls", "url": "http://…", "usuario": "", "clave": ""}]}'
//
// Necesita Playwright (npm i playwright) con su Chromium. Termina al cerrarle la entrada o con SIGTERM.

const { chromium } = require('playwright');

function vigilarConexiones() {
  // La página de MediaMTX no deja a la vista su RTCPeerConnection: se guarda al crearla.
  const Original = window.RTCPeerConnection;
  if (!Original) return;
  window.__conexiones = [];
  function Vigilada(...args) {
    const pc = new Original(...args);
    window.__conexiones.push(pc);
    return pc;
  }
  Vigilada.prototype = Original.prototype;
  Object.setPrototypeOf(Vigilada, Original);
  window.RTCPeerConnection = Vigilada;
}

async function medir() {
  const v = document.querySelector('video');
  const r = { video: null, rtc: null, texto: (document.body.innerText || '').trim().slice(0, 200) };
  if (v) {
    if (v.muted) { v.muted = false; }                 // con el sonido apagado el navegador puede no decodificar el audio
    if (v.paused) { v.play().catch(() => {}); }
    r.video = { tiempo: v.currentTime, ancho: v.videoWidth, alto: v.videoHeight, pausado: v.paused, listo: v.readyState,
                bytes_video: v.webkitVideoDecodedByteCount || 0, bytes_audio: v.webkitAudioDecodedByteCount || 0,
                error: v.error ? v.error.message : '' };
  }
  const pc = (window.__conexiones || []).slice(-1)[0];
  if (pc) {
    r.rtc = { estado: pc.connectionState };
    const stats = await pc.getStats();
    stats.forEach((s) => {
      if (s.type !== 'inbound-rtp') return;
      const codec = stats.get(s.codecId) || {};
      r.rtc[s.kind] = { paquetes: s.packetsReceived, perdidos: s.packetsLost, bytes: s.bytesReceived, codec: codec.mimeType || '',
                        fotogramas: s.framesDecoded, ancho: s.frameWidth, alto: s.frameHeight,
                        energia: s.totalAudioEnergy, muestras: s.totalSamplesReceived };
    });
  }
  return r;
}

(async () => {
  const config = JSON.parse(process.argv[2] || '{}');
  const navegador = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const paginas = [];
  for (const p of config.paginas || []) {
    const contexto = await navegador.newContext(p.usuario ? { httpCredentials: { username: p.usuario, password: p.clave || '' } } : {});
    await contexto.addInitScript(vigilarConexiones);
    const pagina = await contexto.newPage();
    const errores = [];
    pagina.on('pageerror', (e) => errores.push(String(e.message || e).slice(0, 200)));
    try {
      await pagina.goto(p.url, { waitUntil: 'domcontentloaded', timeout: 20000 });
    } catch (e) {
      errores.push('no abre: ' + String(e.message || e).slice(0, 200));
    }
    paginas.push({ nombre: p.nombre, pagina, errores });
  }
  let seguir = true;
  const parar = () => { seguir = false; };
  process.on('SIGTERM', parar);
  process.on('SIGINT', parar);
  process.stdin.on('end', parar);                     // quien lo lanzó se fue (o cerró la entrada): se acaba
  process.stdin.on('data', () => {});
  while (seguir) {
    const linea = { t: Date.now() / 1000 };
    for (const p of paginas) {
      try {
        linea[p.nombre] = await p.pagina.evaluate(medir);
      } catch (e) {
        linea[p.nombre] = { error: String(e.message || e).slice(0, 200) };
      }
      if (p.errores.length) linea[p.nombre].errores = p.errores.splice(0);
    }
    process.stdout.write(JSON.stringify(linea) + '\n');
    await new Promise((r) => setTimeout(r, config.cada_ms || 1000));
  }
  await navegador.close().catch(() => {});
  process.exit(0);
})().catch((e) => {
  process.stdout.write(JSON.stringify({ fatal: String(e.message || e) }) + '\n');
  process.exit(1);
});
