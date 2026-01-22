# openradio.live — All-in-One Live Audio Stack

A small, self-contained live audio streaming stack built around SRT, HLS, Nginx, and FFmpeg, with a real-time dashboard and a simple web listener.

Run it on a VPS or locally. Very low RAM usage. 

---

## What This Is

This project takes a live audio source and turns it into a public HLS stream.

Audio comes in over SRT.
FFmpeg packages it as HLS.
Nginx serves the stream, a web player, and a dashboard.
A Python service reads real access logs and produces live stats.

Everything runs together in one Docker container.

---

## Features

* Public SRT ingest (UDP)
* HLS output for broad device compatibility
* Web-based listener (PWA-style)
* Live dashboard with real metrics
* Listener counts based on actual segment requests
* New vs returning listener detection
* Estimated live latency
* No client-side tracking
* One-command Docker deployment

---

## How It Works (Quick Overview)

Audio is pushed to the server using SRT.
FFmpeg listens for that stream and writes HLS segments.
Nginx serves those segments and logs every request.
The stats service reads the logs and calculates metrics.
The dashboard pulls live JSON from the stats API.

No polling from the player.
No embedded analytics scripts.

---

## Repo Layout

* `docker-compose.yml`
  Single-service deployment.

* `Dockerfile`
  Builds Nginx, FFmpeg, Supervisor, and the stats app.

* `web/`
  The listener web app.

* `dashboard/`
  The live monitoring UI.

* `stats/`
  Python service that analyzes access logs.

* `streamer/`
  Local tools for sending audio to the server.

* `streamer/audio`  
  Directory for auto-dj sound files

---

## Getting It Running

#### Quick Start - on VPS 🏁
```bash
curl -fsSL https://gitlab.com/mrhappynice/orl/-/raw/main/install.sh | sudo bash -s -- IP_ADDRESS_HERE
```
Put your public IP from your VPS. 
Installs Docker, Nginx, and ORL then runs the server.   
Be sure to compose down and change passphrase in .env file after testing connection. 

---

#### Build and start everything:  
Download:  
 ```
 git clone https://gitlab.com/mrhappynice/orl.git && cd orl
 ```
Set passphrase and config in .env then:
```
docker compose up -d --build
```

Defaults:

* Web listener: [http://localhost:5880](http://localhost:5880)
* Dashboard: [http://localhost:5880/dashboard/](http://localhost:5880/dashboard/)
* Stats API: [http://localhost:8090/api/stats](http://localhost:8090/api/stats)
* SRT ingest: UDP port 9000

See `set-nginx.md` for proxy insructions for host Nginx setup on VPS etc.

---

## Sending Audio

You can stream audio in several ways.

Use a microphone.
Stream desktop audio.
Loop a local playlist.
Mix multiple inputs together.

The `streamer/` folder includes:

* simple shell scripts
* a Python playlist streamer
* interactive TUI stream app

These tools are optional.
Any SRT-capable sender will work. e.g.: OBS Studio, mobile apps, etc.
For auto-dj on the server, copy files to /opt/orl/streamer/audio if using the install.sh script.

---

## The Web Listener

The listener is intentionally minimal.

It loads fast.
It works on mobile and desktop.
It can be installed as a PWA.
It avoids frameworks and heavy JS.

You’re expected to customize it.

---

## The Dashboard

The dashboard shows what is happening on the server.

Listener counts are based on HLS segment requests.
Stats come from Nginx logs
You can see:

* active listeners (short and long windows)
* new vs returning listeners
* request rates
* error rates
* estimated latency
* client types and user agents
* live history graphs

---

## Why This Exists

This project is about keeping things understandable.

You should be able to:

* see how audio moves through the system
* trust the numbers you’re looking at
* change one part without breaking everything

It works as a real stream, a demo platform, or a learning project.

---

## Customization

This is meant to be modified.

Replace the web UI.
Change HLS settings.
Add auth to the dashboard.
Record or archive streams.
Run multiple instances for multiple channels.

Nothing here is locked in.

---

## HTTPS and Domains

See `set-domains.md` for notes on Nginx proxy and certbot setup

---

## License

Personal use.
Use it.
Fork it.
Build something better on top of it.


