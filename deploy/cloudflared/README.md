# Exponer la interfaz desde la Raspberry Pi (acceso de prueba)

Sin abrir puertos en el router: un túnel saliente de Cloudflare hacia el
contenedor `app` (Streamlit, puerto 8501, solo en red interna de Docker) +
Cloudflare Access delante para que el grupo de prueba entre con su email y un
código de un solo uso, sin instalar nada.

## 1. Crear el túnel (una vez, desde el dashboard)

1. [dash.cloudflare.com](https://dash.cloudflare.com) → **Zero Trust → Networks → Tunnels →
   Create a tunnel** (tipo *Cloudflared*). Necesita un dominio en Cloudflare (gratis, puede
   ser un subdominio de uno que ya tengas o uno nuevo).
2. Copia el **token del túnel** que te da el asistente → en la Pi, en un `.env` junto a
   `docker-compose.yml` (no versionado):
   ```
   CLOUDFLARE_TUNNEL_TOKEN=eyJ...
   ```
3. En **Public Hostname**, añade una ruta: hostname (p.ej. `baskonia.tudominio.com`) →
   servicio `http://app:8501` (nombre del servicio en `docker-compose.yml`, resuelto por la
   red interna de Docker — no `localhost`).

## 2. Restringir quién entra (Cloudflare Access)

**Zero Trust → Access → Applications → Add an application** (tipo *Self-hosted*), mismo
hostname que arriba. Política: *Allow* por lista de emails (o un dominio de email si todo el
staff comparte uno). Cada persona de la lista recibe un código de un solo uso por email al
entrar — no hace falta usuario/contraseña propio ni que la app implemente login.

## 3. Levantar

```bash
docker compose up -d --build
```

`cloudflared` se conecta solo (túnel saliente, no necesita IP fija ni DNS dinámico). El
servicio `app` no publica ningún puerto en el host (`expose`, no `ports`, en
`docker-compose.yml`) — la única vía de entrada es el túnel.

## Alternativa más simple (grupo de prueba técnico y de confianza)

**Tailscale Funnel**: instala Tailscale en la Pi, añade a los testers a tu tailnet y expón el
servicio con `tailscale funnel 8501`. Más simple de montar, pero cada tester necesita el
cliente de Tailscale instalado — para un grupo no técnico, Cloudflare Access (arriba) es
mejor porque solo necesitan un navegador y su email.
