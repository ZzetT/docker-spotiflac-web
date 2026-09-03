FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11-utils \
    libwebkit2gtk-4.1-0 \
    libgtk-3-0 \
    libgdk-pixbuf2.0-0 \
    libglib2.0-0 \
    libjavascriptcoregtk-4.1-0 \
    build-essential \
    pkg-config \
    libwebkit2gtk-4.1-dev \
    libgtk-3-dev \
    fonts-freefont-ttf \
    ca-certificates \
    curl \
    python3 \
    ffmpeg \
    file \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Compile bridge
COPY bridge.c /app/bridge.c
RUN gcc -O2 -shared -fPIC -o /app/libbridge.so /app/bridge.c $(pkg-config --cflags --libs webkit2gtk-4.1 gtk+-3.0) && \
    rm -rf /app/bridge.c

# Directory for mounting supporter AppImage at runtime
RUN mkdir -p /app/appimage

# Copy Web application & API server
COPY web /app/web
COPY web_server.py /app/web_server.py
COPY extract_frontend.py /app/extract_frontend.py
COPY wails-browser-shim.js /app/wails-browser-shim.js
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/web_server.py /app/extract_frontend.py

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
