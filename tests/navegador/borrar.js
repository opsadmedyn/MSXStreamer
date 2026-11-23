#!/usr/bin/env node
// «Flujos y composición» en un navegador de verdad: borrar una salida y borrar un flujo, pulsando.
//
// El botón «Borrar» pide un segundo clic («¿Seguro? Borrar») y el panel no repinta mientras hay una
// confirmación a medias. Tras confirmar, el botón se quedaba en «¿Seguro?»: lo borrado seguía en
// pantalla y la sección dejaba de ponerse al día hasta recargar la página. Aquí se pulsa como lo
// haría un administrador y se mira lo que queda en pantalla y lo que queda en la base.
//
//   node tests/navegador/borrar.js http://127.0.0.1:<puerto del panel>
//
// El panel tiene que aceptar la cookie msr_sesion=admin (tests/banco/recorder_falso.py). No hace
// falta MediaMTX ni supervisor. Lo lanza tests/test_panel_navegador.py. Sale con 0 si todo pasa,
// con 1 si algo falla y con 77 si no hay navegador con el que probar.

let chromium;
try { ({ chromium } = require('playwright')); } catch (e) {
  try { ({ chromium } = require('playwright-core')); } catch (e2) { console.log('sin Playwright'); process.exit(77); }
}

const BASE = (process.argv[2] || 'http://127.0.0.1:8095').replace(/\/+$/, '');
const NOMBRE = 'Prueba de borrado';
const fallos = [];
const mira = (bien, que, visto) => {
  console.log(`${bien ? 'ok   ' : 'FALLA'} ${que}${bien || visto === undefined ? '' : ` (visto: ${JSON.stringify(visto)})`}`);
  if (!bien) fallos.push(que);
};

async function api(metodo, ruta, cuerpo) {
  const r = await fetch(BASE + ruta, {method: metodo, headers: {Cookie: 'msr_sesion=admin', 'Content-Type': 'application/json'},
    body: cuerpo ? JSON.stringify(cuerpo) : undefined});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${metodo} ${ruta}: ${r.status} ${JSON.stringify(d)}`);
  return d;
}
const flujo = async id => (await api('GET', '/api/flujos')).find(f => f.id === id);
const salidasDe = async id => ((await flujo(id)) || {salidas: []}).salidas.map(s => s.nombre).sort();
const nuevaSalida = (fid, nombre) => api('POST', `/api/flujos/${fid}/salidas`, {nombre, tipo: 'srt', url: 'srt://127.0.0.1:9101'});

(async () => {
  let nav;
  try { nav = await chromium.launch(process.env.CHROMIUM ? {executablePath: process.env.CHROMIUM} : {}); }
  catch (e) { console.log('sin navegador: ' + String(e.message).split('\n')[0]); process.exit(77); }
  const ctx = await nav.newContext({viewport: {width: 1400, height: 900}});
  await ctx.addCookies([{name: 'msr_sesion', value: 'admin', url: BASE}]);
  const pag = await ctx.newPage();
  const errores = [];
  pag.on('pageerror', e => errores.push(String(e).slice(0, 200)));

  for (const f of await api('GET', '/api/flujos')) if (f.nombre.startsWith(NOMBRE)) await api('DELETE', `/api/flujos/${f.id}`);
  const fid = (await api('POST', '/api/flujos', {nombre: `${NOMBRE} · uno`, entrada: 'pull', url: 'srt://127.0.0.1:9999'})).id;
  await nuevaSalida(fid, 'Salida A'); await nuevaSalida(fid, 'Salida B');

  // lo que hay en la tabla de salidas del flujo abierto, y los botones de una fila
  const enPantalla = () => pag.$$eval('#t-sal tr[data-id]', trs => trs.map(tr => tr.children[1].childNodes[0].textContent.trim()).sort());
  const fila = nombre => pag.locator('#t-sal tr[data-id]', {hasText: nombre});
  const borrar = nombre => fila(nombre).locator('[data-a=borrar]');
  const espera = async (cond, ms = 5000) => {       // hasta que se cumpla; false si no llega a cumplirse
    for (const fin = Date.now() + ms; Date.now() < fin;) { if (await cond()) return true; await pag.waitForTimeout(100); }
    return false;
  };
  const igual = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  await pag.goto(`${BASE}/#${fid}`);
  await pag.waitForSelector('#t-sal tr[data-id]');
  mira(igual(await enPantalla(), ['Salida A', 'Salida B']), 'el flujo abierto enseña sus dos salidas', await enPantalla());

  // ---- 1. el primer clic no borra, y la confirmación se deshace sola
  await borrar('Salida B').click();
  mira(await borrar('Salida B').textContent() === '¿Seguro? Borrar', 'el primer clic pide confirmación', await borrar('Salida B').textContent());
  mira(await espera(async () => await borrar('Salida B').textContent() === 'Borrar', 6000), 'sin segundo clic, el botón vuelve a «Borrar» solo');
  mira(igual(await salidasDe(fid), ['Salida A', 'Salida B']), 'y no se ha borrado nada', await salidasDe(fid));
  await nuevaSalida(fid, 'Salida C');
  mira(await espera(async () => (await enPantalla()).includes('Salida C')), 'después, la pantalla sigue poniéndose al día (aparece una salida nueva)', await enPantalla());

  // ---- 2. borrar una salida: confirmada, desaparece de la pantalla y de la base
  await borrar('Salida A').click();
  await borrar('Salida A').click();
  mira(await espera(async () => !(await enPantalla()).includes('Salida A'), 3000), 'la salida borrada desaparece de la pantalla sin recargar', await enPantalla());
  mira(igual(await salidasDe(fid), ['Salida B', 'Salida C']), 'y de la base, sin llevarse las demás', await salidasDe(fid));
  mira(await borrar('Salida B').textContent() === 'Borrar' && await pag.locator('.confirmar').count() === 0,
    'no queda ningún botón a medio confirmar');
  await nuevaSalida(fid, 'Salida D');
  mira(await espera(async () => (await enPantalla()).includes('Salida D')), 'después de borrar, la pantalla sigue poniéndose al día', await enPantalla());

  // ---- 3. una respuesta vieja que llega tarde no devuelve a la pantalla lo borrado
  // La primera lectura de los flujos que salga ahora (la periódica) se retiene con lo que el panel
  // contestó antes de borrar, y se entrega después de la que el panel pide al terminar de borrar.
  let vieja = null, soltada = false; const retenidas = [];
  await pag.route(/\/api\/flujos$/, async ruta => {
    if (ruta.request().method() !== 'GET') return ruta.continue();
    if (!vieja) { vieja = {ruta, respuesta: await ruta.fetch()}; return; }
    if (soltada) return retenidas.push(ruta);      // nada más pone la pantalla al día mientras se mira
    return ruta.continue();
  });
  mira(await espera(() => !!vieja, 4000), 'se retiene una lectura periódica de los flujos, de antes de borrar');
  await borrar('Salida B').click();
  await borrar('Salida B').click();
  mira(await espera(async () => !(await enPantalla()).includes('Salida B'), 3000), 'con esa lectura en el aire, la salida borrada desaparece igual', await enPantalla());
  soltada = true;
  if (vieja) await vieja.ruta.fulfill({response: vieja.respuesta});
  await pag.waitForTimeout(500);
  mira(!(await enPantalla()).includes('Salida B'), 'al llegar tarde la lectura vieja, la salida borrada no vuelve a la pantalla', await enPantalla());
  await pag.unroute(/\/api\/flujos$/);
  for (const ruta of retenidas) await ruta.continue().catch(() => {});
  mira(igual(await salidasDe(fid), ['Salida C', 'Salida D']), 'en la base quedan las otras dos', await salidasDe(fid));

  // ---- 4. borrar el flujo: vuelve a la lista, sin él, y la lista sigue poniéndose al día
  await pag.click('#b-borrar');
  await pag.click('#b-borrar');
  const enLista = () => pag.$$eval('#lista .flujo h3', hs => hs.map(h => h.textContent));
  mira(await espera(async () => !(await pag.isHidden('#v-flujos')) && !(await enLista()).includes(`${NOMBRE} · uno`), 3000),
    'el flujo borrado desaparece y se vuelve a la lista', await enLista());
  mira(!(await flujo(fid)), 'y ya no está en la base');
  await api('POST', '/api/flujos', {nombre: `${NOMBRE} · dos`, entrada: 'pull', url: 'srt://127.0.0.1:9998'});
  mira(await espera(async () => (await enLista()).includes(`${NOMBRE} · dos`)), 'después de borrar un flujo, la lista sigue poniéndose al día', await enLista());

  mira(errores.length === 0, 'la página no ha dado ningún error', errores);
  for (const f of await api('GET', '/api/flujos')) if (f.nombre.startsWith(NOMBRE)) await api('DELETE', `/api/flujos/${f.id}`);
  await nav.close();
  console.log(fallos.length ? `\n${fallos.length} cosas fallan` : '\ntodo bien');
  process.exit(fallos.length ? 1 : 0);
})().catch(e => { console.error('La prueba no pudo terminar:', e); process.exit(1); });
