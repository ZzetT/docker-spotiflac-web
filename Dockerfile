FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV GDK_BACKEND=x11
ENV WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11-utils \
    dbus \
    dbus-x11 \
    libwebkitgtk-6.0-4 \
    libgtk-4-1 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libjavascriptcoregtk-6.0-1 \
    libsoup-3.0-0 \
    build-essential \
    pkg-config \
    libwebkitgtk-6.0-dev \
    libgtk-4-dev \
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
RUN gcc -O2 -shared -fPIC -o /app/libbridge.so /app/bridge.c $(pkg-config --cflags --libs webkitgtk-6.0 gtk4) && \
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
