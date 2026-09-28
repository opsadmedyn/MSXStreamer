# Propuesta: entrar al Streamer (y a futuros módulos) con los usuarios del Recorder

**Estado:** pendiente de tu aprobación. No se ha tocado nada todavía.
**Fecha:** 27-09-2026. Revisada contra el código real de MSXRecorder y MSXStreamer desde tres ángulos: seguridad, robustez y compatibilidad.

## En pocas palabras

- **Una sola lista de usuarios:** la hoja "MSR Usuarios". Se añade una columna G "Módulos" donde pones `recorder`, `streamer` o ambos. Si está vacía, el usuario solo tiene el Recorder, como hoy, así que nadie gana ni pierde acceso al actualizar.
- **Un solo inicio de sesión:** el del Recorder (código por correo). El Streamer deja de tener clave propia para el día a día. Si no has entrado, te lleva al login del Recorder y después vuelves al Streamer.
- **Una sola dirección:** https://recorder2.mediasyntaxis.com/streamer/, con el mismo certificado.
- **Dos roles, los de la hoja:**
  - El admin crea, edita y borra entradas y destinos.
  - El operador ve el estado y el registro, arranca y para, y abre la vista previa.
- **Claves de destino ocultas:** las de Castr, YouTube, etc. no vuelven a mostrarse en el panel una vez guardadas, para nadie. Si hay que cambiarlas, se escriben de nuevo. Tampoco aparecen en los errores ni en el registro.
- **Si el Recorder o Google fallan:** las salidas siguen al aire porque no dependen del panel. Para operar en ese caso queda una clave de emergencia que solo funciona desde el propio Z8, por túnel SSH.
- **Registro con autor:** el registro del Streamer anota quién hizo cada cambio.
- **Sin cortes:** nada de esto corta la grabación ni las salidas. Se puede volver atrás en un minuto cambiando una línea de configuración.

## Decisiones que necesito de ti

1. **¿Desde dónde se entra al Streamer?**
   - **Recomendado: solo por recorder2 (Tailscale, HTTPS).** Consecuencia: en la oficina, el equipo que lo use necesita Tailscale. Hoy se entra por http://192.168.1.202:8095 sin él.
   - Alternativa: también por la red local (HTTP sin cifrar, como el Recorder en :8081). Es menos seguro, porque la sesión viaja sin cifrar y cualquier otro servicio del Z8 en otro puerto la recibe.
2. **¿Qué puede hacer un operador?**
   - **Recomendado: ver, arrancar y parar, y abrir la vista previa.**
   - Alternativa: solo ver.
3. **¿Quién ve las claves de destino ya guardadas?**
   - **Recomendado: nadie.** Se reescriben al cambiarlas, como hoy la contraseña SRT.
   - Alternativa: los admins las ven.
4. **El Z600 comparte la hoja.**
   - **Recomendado: actualizarlo a la vez que el Z8.** Solo reinicia la web y no corta la grabación.
   - Si se deja para después, mientras tanto no crees usuarios "solo streamer", porque el Z600 los dejaría entrar a su Recorder.

Si te valen las cuatro recomendaciones, basta con responder "apruebo".

---

## Detalle técnico

### A. Columna "Módulos" en la hoja

- **Posición:** la columna G se añade después de F, nunca insertada entre columnas. Las dos estaciones escriben "Ultimo acceso" en la columna E por posición. Antes de instalar, se comprueba que G está vacía.
- **Formato:** lista separada por comas; el desplegable de selección múltiple de Sheets produce `recorder, streamer`. Se ignoran mayúsculas y espacios.
- **Valores por defecto:**
  - Celda vacía = solo `recorder`.
  - Los `admins_fijos` de config.json tienen todos los módulos, estén o no en la hoja.
- **Valor desconocido** (por ejemplo, un módulo futuro que esa estación aún no conoce):
  - Sale como aviso en el panel de usuarios, y la fila sigue valiendo con los módulos que sí conoce.
  - Nunca invalida la fila ni cierra sesiones.
  - Si la celda solo tiene valores desconocidos, esa estación no le da ningún módulo.
- **Módulos nuevos:** primero se instala en las dos estaciones el código que conoce el módulo; después se añade el valor al desplegable.
- **Rol y Estado:** son únicos para todos los módulos. Bloquear a alguien en la hoja lo saca de todo.

### B. Inicio de sesión único (Recorder 1.5.0 como única autoridad)

- **Qué sigue igual:** el Recorder sigue leyendo la hoja, enviando los códigos y guardando las sesiones (cookie `msr_sesion`).
- **Módulos en el Recorder.** Un usuario sin `recorder` puede iniciar sesión, pero en el Recorder:
  - solo alcanza `/api/auth/yo` y salir;
  - las páginas lo llevan a su primer módulo;
  - el resto de `/api/*` responde 403.
- **`/api/auth/yo`:** añade `modulos`, `segura` (sesión creada por HTTPS) y `creada`.
- **Vuelta tras el login:**
  - `volver` es una clave de módulo, no una ruta: `/login?volver=streamer`.
  - login.html la traduce con una tabla fija (`{streamer: "/streamer/"}`). Cualquier otro valor lleva a la página de inicio del usuario.
  - Esto impide redirecciones a sitios falsos y enlaces `javascript:`.
- **Validación en el Streamer.** Cada petición se valida contra `http://127.0.0.1:8081/api/auth/yo`, reenviando la cookie:
  - **Implementación:** solo biblioteca estándar (urllib), fuera del bucle principal y con 2 s de espera máxima. Un Recorder lento no congela el panel, y el despliegue no necesita instalar nada nuevo.
  - **Caché:** las respuestas válidas se guardan 15 s en memoria, por hash del token. Crear, editar y borrar se validan siempre en el momento.
  - **Respuesta 401 del Recorder:** lleva al login.
  - **Recorder caído o lento:** el panel dice "no se puede validar el acceso" (503), nunca queda abierto. Una identidad ya validada sigue valiendo hasta 5 min para ver, arrancar y parar, así un operador puede actuar mientras el Recorder se reinicia. Las salidas siguen al aire en cualquier caso.
  - **Recorder antiguo** (respuesta sin `modulos`): solo pasan los admins.
  - **Sesiones por HTTP:** con la decisión 1 recomendada, se rechazan las sesiones no creadas por HTTPS, con el aviso "entra por https://recorder2.mediasyntaxis.com".
- **Salir en el Streamer:** hace `POST /api/auth/salir` al Recorder, que cierra la sesión compartida, y vuelve al login.

### C. Dirección y panel

- **Caddy:**
  - `redir /streamer /streamer/ 308`
  - `handle_path /streamer/* { reverse_proxy 127.0.0.1:8095 }`
  - `respond /streamer/api/login* 404`, para que la clave de emergencia nunca sea alcanzable a través de Caddy.
  - Se aplica con `caddy validate` y `reload`. No se hace `restart`, que cortaría a quien esté viendo el Recorder.
- **Rutas del panel:** son relativas (`static/…`, `api/…`), así funciona igual bajo /streamer/ y por túnel en la raíz. Login y salir usan rutas absolutas.
- **Enlace en el Recorder:** la cabecera muestra "Streamer" solo si la estación lo tiene configurado (`url_streamer` en config.json) y el usuario tiene el módulo. El Z600 no lo tiene configurado, así que allí no aparece.
- **Puerto 8095:** tras unos días de uso pasa a escuchar solo en 127.0.0.1. Es un paso manual sobre la unidad msxs-web que no toca MediaMTX ni las salidas.
- **Vista previa:** su URL usa la dirección por la que se entró, para que funcione también fuera de la oficina por Tailscale. Las URL .m3u8 de los destinos HLS siguen con la IP de la red local.

### D. Acceso de emergencia

- **Cuándo se acepta:** `clave_panel` queda solo para emergencias. Se acepta únicamente si la petición llega a 127.0.0.1 sin cabeceras de proxy (`X-Forwarded-For` / `Forwarded`, que Caddy siempre añade).
- **Cómo se usa:** `ssh -L 8095:127.0.0.1:8095 z8` y abrir http://localhost:8095. Solo le sirve a quien tiene SSH al Z8.
- **Falla cerrado:** si la clave está vacía o config.json no se puede leer, no hay acceso de emergencia. Hoy, sin clave, el panel queda abierto; eso se corrige.
- **Sesión de emergencia:** pasa a ser un token aleatorio por entrada, guardado como hash y revocable. Hoy es una firma de la hora de caducidad, igual para todos.
- **Registro:** cada acción de emergencia queda anotada como "emergencia".
- **Cambio de clave:** la clave se cambia al activar el nuevo acceso, porque se ha tecleado por HTTP en la red local.
- **Límite:** si Google falla, nadie puede recibir códigos, tampoco los admins fijos. En ese caso solo queda el túnel.

### E. Roles y credenciales

Los permisos se comprueban en el servidor, no solo en la pantalla.

- **Admin:** crear, editar y borrar entradas y destinos, y todo lo del operador.
- **Operador:** ver el estado y el registro, arrancar y parar entradas y destinos, y abrir la vista previa.
- **Credenciales:**
  - El streamid se trata como la contraseña: nunca se devuelve, solo se indica que existe. Al editar, dejarlo vacío mantiene el guardado.
  - Las URL se muestran sin parámetros ni usuario, y las RTMP sin la clave del final. Al editar, si la URL enviada no trae parámetros, se conservan los guardados.
- **Errores y registro:**
  - Antes de guardar un error de ffmpeg o el evento "salida caída", se sustituyen clave, streamid, contraseña y parámetros por `***`.
  - La línea de arranque, que lleva el comando completo, deja de usarse como texto de error.
  - Al instalar 0.2.0 se limpian los eventos ya guardados.
- **Autor de cada cambio:** el registro guarda el email de quien hizo cada cambio (columna nueva `usuario` en eventos). El supervisor figura como "sistema".

### F. Protección contra peticiones desde otros sitios (CSRF), en los dos programas

- **Regla:** un middleware igual en Recorder 1.5.0 y Streamer 0.2.0 rechaza con 403 todo POST/PUT/PATCH/DELETE cuyo `Origin` no coincida con el `Host` de la propia petición (host y puerto). Sin `Origin`, también se rechaza. Las peticiones GET no cambian.
- **Motivo:** la cookie SameSite=Lax no frena a otros puertos del mismo equipo (restreamer, linux-ui…) ni a otros subdominios de mediasyntaxis.com. Hoy eso ya expone "pausar canal" en el Recorder, así que se cierra también allí.

### G. Conectados y límite de concurrentes

- **Límite:** solo se aplica a quien tiene `recorder`. Un usuario solo-streamer nunca se queda fuera porque el Recorder esté lleno.
- **Latidos:** el panel del Streamer envía latidos al Recorder cada 15 s (`/api/auth/latido`, con actividad "Streamer · …" y un campo módulo).
  - Así el usuario aparece en Conectados y un admin puede expulsarlo.
  - El límite cuenta solo los latidos del Recorder.
- **Sesión compartida:** expulsar a alguien lo saca de los dos programas.
- **Tiempos:**
  - Expulsar tarda hasta 15 s.
  - Bloquear o quitar el módulo en la hoja tarda hasta 75 s (60 s de lectura de la hoja más 15 s de caché).

### H. Arreglo de robustez del Recorder (incluido en 1.5.0)

- **Problema actual:** si el Recorder se reinicia y aún no ha leído la hoja (o Google no responde), en la primera petición borra las sesiones de todos los usuarios de la hoja. Con el Streamer consultando cada pocos segundos, esto pasaría en cada actualización.
- **Arreglo:**
  - 1.5.0 guarda la última copia buena de la hoja en usuarios.db y la carga al arrancar.
  - Si la hoja nunca se ha podido leer, responde "usuarios no disponibles" (503) en vez de cerrar sesiones.
- **Sesiones:** se anota si cada sesión se creó por HTTPS (`segura`) y con qué dirección, para aplicar la decisión 1.

## Despliegue

Ningún paso corta la grabación ni las salidas. Cada instalación deja respaldo en el Z8, el Mac y Drive.

0. **Comprobaciones previas.** Si alguna falla, se para aquí.
   - El token de Google del Recorder está presente en el Z8.
   - En Administración → Usuarios no hay error de lectura, y la hoja se actualizó hace menos de 2 min.
   - Un login real con código por recorder2 funciona.
   - El Z8 y el Z600 usan la misma hoja.
   - Quien hoy usa la clave del panel tiene una fila activa en la hoja.
1. **Recorder 1.5.0 en el Z8** (y en el Z600, según la decisión 4). Reinicia msr-web y msr-guardian; la grabación sigue. Después se repite la comprobación de la hoja.
2. **Columna G:** se añade y se pone `recorder, streamer` a quien corresponda.
3. **Streamer 0.2.0 con `"acceso": "clave"`:** funciona como hoy, pero ya con rutas relativas, claves ocultas y registro con usuario. Solo reinicia el panel.
4. **Ruta /streamer/ en Caddy** (`validate` y `reload`).
5. **Cambio a `"acceso": "recorder"`:** se reinicia solo el panel y se prueba con un admin y con un operador.
6. **Unos días después:** el 8095 pasa a ser solo local y se cambia la clave de emergencia.

## Volver atrás

Primero el Streamer y después el Recorder.

- **Al instante:** `"acceso": "clave"` en el config.json del Streamer y reiniciar msxs-web. Vuelve la clave de siempre.
- **Si ya se cerró el 8095:** devolver la unidad msxs-web a 0.0.0.0 (`daemon-reload` y `restart msxs-web`).
- **Caddy:** quitar la ruta /streamer/ (`reload`).
- **Código:**
  - El Streamer 0.2.0 añade `--volver vX.Y.Z` a su desplegar.sh; hoy solo lo tiene el Recorder.
  - El Recorder se revierte con `scripts/desplegar.sh z8 --volver v1.4.1`, siempre después de dejar el Streamer en `clave`.
  - La columna G puede quedarse, porque 1.4.x la ignora.

## Fuera de esta propuesta

- **La señal (HLS/SRT, puertos 8888/8890):** no pasa por este login. Sigue con sus propias reglas: solo se publica en la red lo que activas por flujo.
- **Python del Recorder:** su actualización a 3.11 o superior (el aviso de Google del 4 de octubre) va aparte.
