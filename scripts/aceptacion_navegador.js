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
//   node aceptacion_navegador.js paginas.json        (lo mismo en un archivo: así la clave no va en la línea de órdenes)
//
// Necesita Playwright (playwright o playwright-core) con su Chromium; CHROMIUM=/ruta usa otro
// navegador. Termina al cerrarle la entrada o con SIGTERM.
// Si por la entrada le llega una línea «recargar», vuelve a cargar las páginas (como quien pulsa F5).
//
// Una página con «publicar» ({"whip": "http://…/whip", "token": "…", "codec": "H264", "audio_wav": "/ruta.wav"})
// no reproduce: publica por WHIP la cámara y el micrófono de mentira de Chromium, como haría un navegador
// de verdad, y lo que se apunta es lo que envía (fotogramas codificados y bytes). Con «audio_wav», el
// micrófono de mentira suena como ese archivo en vez de con el pitido de Chromium.

const fs = require('fs');
let chromium;
try { ({ chromium } = require('playwright')); } catch (e) { ({ chromium } = require('playwright-core')); }   // el de las estaciones

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

async function publicar(cfg) {
  // Un cliente WHIP mínimo: ofrece la cámara y el micrófono y manda la oferta con el token.
  // el audio, tal cual: el navegador trataría un tono continuo como ruido y lo dejaría casi en silencio
  const medios = await navigator.mediaDevices.getUserMedia({
    video: { width: cfg.ancho || 1280, height: cfg.alto || 720, frameRate: cfg.fps || 30 },
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  const pc = new RTCPeerConnection();
  for (const pista of medios.getTracks()) {
    const t = pc.addTransceiver(pista, { direction: 'sendonly' });
    if (pista.kind === 'video' && cfg.codec) {
      const quiere = ('video/' + cfg.codec).toLowerCase();
      const elegidos = RTCRtpSender.getCapabilities('video').codecs.filter((c) => c.mimeType.toLowerCase() === quiere);
      if (!elegidos.length) throw new Error('este navegador no codifica ' + cfg.codec);
      t.setCodecPreferences(elegidos);
    }
  }
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((listo) => {
    if (pc.iceGatheringState === 'complete') return listo();
    pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') listo(); });
    setTimeout(listo, 3000);
  });
  const cabeceras = { 'Content-Type': 'application/sdp' };
  if (cfg.token) cabeceras.Authorization = 'Bearer ' + cfg.token;
  const r = await fetch(cfg.whip, { method: 'POST', headers: cabeceras, body: pc.localDescription.sdp });
  if (!r.ok) throw new Error('el servidor WHIP respondió ' + r.status);
  await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
  return true;
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
      const codec = stats.get(s.codecId) || {};
      if (s.type === 'inbound-rtp') {
        r.rtc[s.kind] = { paquetes: s.packetsReceived, perdidos: s.packetsLost, bytes: s.bytesReceived, codec: codec.mimeType || '',
                          fotogramas: s.framesDecoded, ancho: s.frameWidth, alto: s.frameHeight,
                          energia: s.totalAudioEnergy, muestras: s.totalSamplesReceived };
      } else if (s.type === 'outbound-rtp') {             // una página que publica: lo que envía
        r.rtc.envia = r.rtc.envia || {};
        r.rtc.envia[s.kind] = { paquetes: s.packetsSent, bytes: s.bytesSent, codec: codec.mimeType || '',
                                fotogramas: s.framesEncoded, ancho: s.frameWidth, alto: s.frameHeight };
      }
    });
  }
  return r;
}

(async () => {
  const dicho = (process.argv[2] || '{}').trim();
  const config = JSON.parse(dicho.startsWith('{') ? dicho : fs.readFileSync(dicho, 'utf8'));
  const opciones = { args: ['--autoplay-policy=no-user-gesture-required'] };
  const publican = (config.paginas || []).filter((p) => p.publicar);
  if (publican.length) {                              // cámara y micrófono de mentira, sin preguntar
    opciones.args.push('--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream');
    const wav = publican.map((p) => p.publicar.audio_wav).find(Boolean);
    if (wav) opciones.args.push('--use-file-for-fake-audio-capture=' + wav);    // el micrófono «oye» ese archivo, en bucle
    // la cámara solo se abre en https o en el propio equipo: si el hub se entrega por http con su nombre, se le deja
    const origenes = [...new Set(publican.map((p) => new URL(p.url).origin).filter((o) => o.startsWith('http://')))];
    if (origenes.length) opciones.args.push('--unsafely-treat-insecure-origin-as-secure=' + origenes.join(','));
  }
  if (process.env.CHROMIUM) opciones.executablePath = process.env.CHROMIUM;     // otro navegador (p. ej. Google Chrome)
  const navegador = await chromium.launch(opciones);
  const paginas = [];
  for (const p of config.paginas || []) {
    const contexto = await navegador.newContext(p.usuario ? { httpCredentials: { username: p.usuario, password: p.clave || '' } } : {});
    await contexto.addInitScript(vigilarConexiones);
    const pagina = await contexto.newPage();
    const errores = [];
    pagina.on('pageerror', (e) => errores.push(String(e.message || e).slice(0, 200)));
    const publica = async () => {
      if (!p.publicar) return;
      for (let intento = 1; intento <= 3; intento++) {  // como un codificador, que insiste si el servidor aún no atiende
        try {
          await pagina.evaluate(publicar, p.publicar);
          return;
        } catch (e) {
          errores.push(`no publica (intento ${intento}): ` + String(e.message || e).slice(0, 200));
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    };
    if (p.publicar) {                                   // una página en blanco con el mismo origen que el servidor WHIP
      await contexto.grantPermissions(['camera', 'microphone']).catch(() => {});
      await pagina.route(p.url, (r) => r.fulfill({ contentType: 'text/html', body: '<!doctype html><title>cámara de pruebas</title>' }));
    }
    try {
      await pagina.goto(p.url, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await publica();
    } catch (e) {
      errores.push('no abre: ' + String(e.message || e).slice(0, 200));
    }
    paginas.push({ nombre: p.nombre, pagina, errores, publica });
  }
  let seguir = true;
  const parar = () => { seguir = false; };
  process.on('SIGTERM', parar);
  process.on('SIGINT', parar);
  process.stdin.on('end', parar);                     // quien lo lanzó se fue (o cerró la entrada): se acaba
  let recargar = false;
  process.stdin.on('data', (d) => { if (String(d).includes('recargar')) recargar = true; });
  while (seguir) {
    const linea = { t: Date.now() / 1000 };
    if (recargar) {
      recargar = false;
      linea.recargadas = true;
      for (const p of paginas) {
        await p.pagina.reload({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch((e) => p.errores.push('no recarga: ' + String(e.message || e).slice(0, 200)));
        await p.publica();
      }
    }
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
