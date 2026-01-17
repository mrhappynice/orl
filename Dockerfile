FROM python:3.12-alpine

# 1. Install System Dependencies (Nginx, FFmpeg, Supervisor)
# We use alpine's ffmpeg (usually v6+) which includes libsrt
RUN apk add --no-cache nginx ffmpeg supervisor

# 2. Setup Python Stats App
WORKDIR /app/stats
COPY stats/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY stats/ .

# 3. Setup Web & Dashboard Files
COPY web /srv/web
COPY dashboard /srv/dashboard

# 4. Configure Nginx
# We remove default config and add yours
RUN rm /etc/nginx/http.d/default.conf
COPY nginx-site.conf /etc/nginx/http.d/default.conf
# Ensure the log file exists for the stats app to tail
RUN mkdir -p /var/log/nginx && touch /var/log/nginx/hls_access.log && chmod 666 /var/log/nginx/hls_access.log

# 5. Setup HLS Directory
RUN mkdir -p /hls && chmod 777 /hls

# 6. Configure Supervisor
COPY supervisord.conf /etc/supervisord.conf

# 7. Runtime
# Expose HTTP (80) and SRT (UDP port, usually 9000 based on your scripts)
EXPOSE 80
EXPOSE 9000/udp

# Start Supervisor (which starts Nginx, FFmpeg, and Python)
CMD ["supervisord", "-c", "/etc/supervisord.conf"]