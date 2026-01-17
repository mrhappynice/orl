## Set domains: get cert, then re-enable

Right now you can’t load nginx with the 443 config because the cert files don’t exist yet.

### A) Temporarily make it HTTP-only (no SSL lines)

Use this for now:

```nginx
server {
  listen 80;
  server_name yourradio.live;

  location / {
    proxy_pass http://127.0.0.1:5880/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

Then:

```bash
nginx -t && systemctl reload nginx
```

### B) Issue the cert

If certbot is installed:

```bash
certbot --nginx -d yourradio.live
```

(If you also want `www`)

```bash
certbot --nginx -d yourradio.live -d www.yourradio.live
```

If you don’t have certbot yet (Ubuntu/Debian):

```bash
apt update
apt install -y certbot python3-certbot-nginx
```

### C) Put back HTTPS + redirect once cert exists

After certbot succeeds, change to full two-server config (80 redirect → 443 with ssl), then:

```bash
nginx -t && systemctl reload nginx
```

Full config:

```bash
server {
  listen 80;
  server_name yourradio.live;

  # If you have TLS, redirect HTTP -> HTTPS:
  return 301 https://$host$request_uri;
}

server {
  listen 443 ssl http2;
  server_name yourradio.live;

  # --- TLS (use your real cert paths)
  ssl_certificate     /etc/letsencrypt/live/yourradio.live/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/yourradio.live/privkey.pem;

  # Proxy to the web container
  location / {
    proxy_pass http://127.0.0.1:5880/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location /hls/ {
    proxy_pass http://127.0.0.1:5880/hls/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
  }

  location /dashboard/ {
    proxy_pass http://127.0.0.1:5880/dashboard/;
    proxy_set_header Host $host;
  }

  # Stats API
  location /api/ {
    proxy_pass http://127.0.0.1:8090/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```



## If the cert exists but under a different folder name

Check what folders you actually have:

```bash
ls -la /etc/letsencrypt/live/
```

If you see something like `/etc/letsencrypt/live/www.yourradio.live/`, then your nginx config must match that directory:

```nginx
ssl_certificate     /etc/letsencrypt/live/www.yourradio.live/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/www.yourradio.live/privkey.pem;
```

---

## If you’re using Docker

If nginx is inside a container, it won’t see host paths unless you mounted them. In that case you must either:

* mount `/etc/letsencrypt` into the container, **or**
* run certbot in the same container/environment as nginx.

---
