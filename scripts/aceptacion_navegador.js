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
// Una página con «sin_verificar» se abre aunque su certificado https no se pueda comprobar (el del banco).
// Una página con «solo_rele» solo deja a WebRTC salir por un servidor TURN, como un navegador en una red
// que no deja pasar otra cosa: si el servidor no le da uno que funcione, no conecta.
//
// Necesita Playwright (playwright o playwright-core) con su Chromium; CHROMIUM=/ruta usa otro
// navegador. Termina al cerrarle la entrada o con SIGTERM.
//   node aceptacion_navegador.js --capacidades   solo dice si este navegador puede reproducir lo que entrega el hub
// Si por la entrada le llega una línea «recargar», vuelve a cargar las páginas (como quien pulsa F5).
//
// Una página con «publicar» ({"whip": "http://…/whip", "token": "…", "codec": "H264", "audio_wav": "/ruta.wav"})
// no reproduce: publica por WHIP la cámara y el micrófono de mentira de Chromium, como haría un navegador
// de verdad, y lo que se apunta es lo que envía (fotogramas codificados y bytes). Con «audio_wav», el
// micrófono de mentira suena como ese archivo en vez de con el pitido de Chromium. Con «ice_del_servidor»
// pregunta antes al servidor WHIP por sus servidores STUN y TURN (OPTIONS), como la página de MediaMTX.

const fs = require('fs');
let chromium;
try { ({ chromium } = require('playwright')); } catch (e) { ({ chromium } = require('playwright-core')); }   // el de las estaciones

function vigilarConexiones(opciones) {
  // La página de MediaMTX no deja a la vista su RTCPeerConnection: se guarda al crearla.
  const Original = window.RTCPeerConnection;
  if (!Original) return;
  window.__conexiones = [];
  function Vigilada(config, ...resto) {
    const pc = new Original(opciones.solo_rele ? { ...(config || {}), iceTransportPolicy: 'relay' } : config, ...resto);
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
  const cabeceras = { 'Content-Type': 'application/sdp' };
  if (cfg.token) cabeceras.Authorization = 'Bearer ' + cfg.token;
  const servidores = [];
  if (cfg.ice_del_servidor) {                         // Link: <turn:…>; rel="ice-server"; username="…"; credential="…"
    const r = await fetch(cfg.whip, { method: 'OPTIONS', headers: cfg.token ? { Authorization: cabeceras.Authorization } : {} });
    for (const trozo of (r.headers.get('Link') || '').split(/,\s*(?=<)/)) {
      const m = trozo.match(/^<([^>]+)>;\s*rel="ice-server"(.*)$/i);
      if (!m) continue;
      const dato = (nombre) => { const x = m[2].match(new RegExp(nombre + '="((?:[^"\\\\]|\\\\.)*)"', 'i')); return x ? JSON.parse('"' + x[1] + '"') : undefined; };
      servidores.push({ urls: [m[1]], username: dato('username'), credential: dato('credential') });
    }
  }
  const pc = new RTCPeerConnection({ iceServers: servidores });
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
    // por dónde van los medios: el par de candidatos elegido y lo que se ofreció. Nunca las credenciales
    const candidato = (s) => (s ? { tipo: s.candidateType, donde: `${s.address || '?'}:${s.port}/${s.protocol}`,
                                    ...(s.url ? { servidor: s.url } : {}) } : null);
    const transporte = Array.from(stats.values()).find((s) => s.type === 'transport') || {};
    const par = stats.get(transporte.selectedCandidatePairId);
    // lo que el servidor ofrece en su respuesta: «a=candidate:1 1 udp 2130706431 192.0.2.10 8189 typ host»
    const anunciados = ((pc.remoteDescription || {}).sdp || '').split(/\r?\n/).filter((l) => l.startsWith('a=candidate:'))
      .map((l) => l.split(' ')).map((x) => ({ tipo: x[7], donde: `${x[4]}:${x[5]}/${String(x[2]).toLowerCase()}` }));
    r.rtc.ice = {
      servidores: (pc.getConfiguration().iceServers || []).map((s) => ({
        url: [].concat(s.urls || []).join(' '), con_credencial: Boolean(s.credential),
        usuario_temporal: /^\d{9,}:/.test(s.username || '') })),
      solo_rele: pc.getConfiguration().iceTransportPolicy === 'relay',
      elegido: par ? { propio: candidato(stats.get(par.localCandidateId)), remoto: candidato(stats.get(par.remoteCandidateId)) } : null,
      anunciados };
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

// --capacidades: no abre ninguna página; dice en una línea JSON si este navegador puede reproducir lo
// que entrega el hub (H.264 y AAC en la página HLS, H.264 y Opus por WebRTC) y termina.
async function capacidades() {
  const opciones = {};
  if (process.env.CHROMIUM) opciones.executablePath = process.env.CHROMIUM;
  const navegador = await chromium.launch(opciones);
  try {
    const pagina = await navegador.newPage();
    console.log(JSON.stringify(await pagina.evaluate(() => {
      const recibe = (tipo, codec) => Boolean(window.RTCRtpReceiver
        && ((RTCRtpReceiver.getCapabilities(tipo) || {}).codecs || []).some((c) => c.mimeType.toLowerCase() === codec));
      const puede = (tipo) => Boolean(window.MediaSource && MediaSource.isTypeSupported(tipo));
      return {
        navegador: (navigator.userAgent.match(/(?:Headless)?Chrome\/[0-9.]+/) || [''])[0],
        mse_h264: puede('video/mp4; codecs="avc1.64001f"'),
        mse_aac: puede('audio/mp4; codecs="mp4a.40.2"'),
        rtc_h264: recibe('video', 'video/h264'),
        rtc_opus: recibe('audio', 'audio/opus'),
      };
    })));
  } finally {
    await navegador.close();
  }
}

(async () => {
  if (process.argv[2] === '--capacidades') return capacidades();
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
    const contexto = await navegador.newContext({
      ...(p.usuario ? { httpCredentials: { username: p.usuario, password: p.clave || '' } } : {}),
      ...(p.sin_verificar ? { ignoreHTTPSErrors: true } : {}) });    // un https con certificado de pruebas, firmado por nadie
    await contexto.addInitScript(vigilarConexiones, { solo_rele: Boolean(p.solo_rele) });
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
