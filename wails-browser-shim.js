/**
 * Wails v2 Browser Compatibility Shim for SpotiFLAC-Next
 * Enables the official React / Tailwind / Radix UI to run in standard web browsers
 * by proxying Wails Go bindings (window.go) and event system (window.runtime)
 * to the headless backend via HTTP and Server-Sent Events (SSE).
 */
(function () {
  console.log("[Wails Web Shim] Initializing browser adapter for SpotiFLAC-Next...");

  // ============================================================================
  // 1. Event Subscription & Dispatch Bus (window.runtime.Events*)
  // ============================================================================
  const eventListeners = new Map(); // eventName -> Array<{ callback, max, count }>

  function addEventListener(name, callback, max = -1) {
    if (typeof callback !== 'function') return () => {};
    if (!eventListeners.has(name)) {
      eventListeners.set(name, []);
    }
    const record = { callback, max, count: 0 };
    eventListeners.get(name).push(record);

    return function unsubscribe() {
      removeEventListener(name, callback);
    };
  }

  function removeEventListener(name, ...callbacks) {
    if (!eventListeners.has(name)) return;
    if (callbacks.length === 0) {
      eventListeners.delete(name);
      return;
    }
    const list = eventListeners.get(name);
    const filtered = list.filter(item => !callbacks.includes(item.callback));
    if (filtered.length > 0) {
      eventListeners.set(name, filtered);
    } else {
      eventListeners.delete(name);
    }
  }

  function dispatchEventLocally(name, ...data) {
    const list = eventListeners.get(name);
    if (!list || list.length === 0) return;

    const remaining = [];
    for (const item of list) {
      try {
        item.count++;
        item.callback(...data);
      } catch (err) {
        console.error(`[Wails Web Shim] Error in event listener for "${name}":`, err);
      }
      if (item.max === -1 || item.count < item.max) {
        remaining.push(item);
      }
    }

    if (remaining.length > 0) {
      eventListeners.set(name, remaining);
    } else {
      eventListeners.delete(name);
    }
  }

  // ============================================================================
  // 2. Real-time Event Streaming via Server-Sent Events (SSE)
  // ============================================================================
  let eventSource = null;
  let sseReconnectTimer = null;

  function connectEventStream() {
    if (eventSource) {
      try { eventSource.close(); } catch (e) {}
      eventSource = null;
    }

    try {
      eventSource = new EventSource('/api/wails/events/stream');

      eventSource.onopen = function () {
        console.log("[Wails Web Shim] Connected to real-time event stream.");
      };

      eventSource.onmessage = function (e) {
        try {
          const payload = JSON.parse(e.data);
          if (payload && payload.name) {
            const args = Array.isArray(payload.data) ? payload.data : (payload.data !== undefined ? [payload.data] : []);
            dispatchEventLocally(payload.name, ...args);
          }
        } catch (err) {
          console.warn("[Wails Web Shim] Failed to parse event payload:", err, e.data);
        }
      };

      eventSource.onerror = function () {
        if (eventSource) {
          try { eventSource.close(); } catch (e) {}
          eventSource = null;
        }
        if (!sseReconnectTimer) {
          sseReconnectTimer = setTimeout(() => {
            sseReconnectTimer = null;
            connectEventStream();
          }, 3000);
        }
      };
    } catch (err) {
      console.warn("[Wails Web Shim] EventSource connection failed, will retry in 3s:", err);
      if (!sseReconnectTimer) {
        sseReconnectTimer = setTimeout(() => {
          sseReconnectTimer = null;
          connectEventStream();
        }, 3000);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connectEventStream);
  } else {
    connectEventStream();
  }

  // ============================================================================
  // 3. window.runtime Emulation
  // ============================================================================
  window.runtime = {
    EventsOn: (name, callback) => addEventListener(name, callback, -1),
    EventsOnMultiple: (name, callback, max) => addEventListener(name, callback, max),
    EventsOnce: (name, callback) => addEventListener(name, callback, 1),
    EventsOff: (name, ...callbacks) => removeEventListener(name, ...callbacks),
    EventsOffAll: () => eventListeners.clear(),
    EventsEmit: (name, ...data) => {
      dispatchEventLocally(name, ...data);
      fetch('/api/wails/emit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, data: data })
      }).catch(err => {
        console.warn('[Wails Web Shim] Failed to emit event to server:', err);
      });
    },
    BrowserOpenURL: (url) => {
      if (url) {
        window.open(url, '_blank', 'noopener,noreferrer');
      }
    },
    ClipboardGetText: async () => {
      try {
        if (navigator.clipboard && navigator.clipboard.readText) {
          return await navigator.clipboard.readText();
        }
      } catch (e) {}
      return "";
    },
    ClipboardSetText: async (text) => {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          return true;
        }
      } catch (e) {}
      return false;
    },
    OnFileDrop: () => () => {},
    OnFileDropOff: () => {},
    WindowMinimise: () => {},
    WindowToggleMaximise: () => {},
    Quit: () => {
      console.log("[Wails Web Shim] Quit requested (no-op in web browser)");
    },
    Environment: async () => ({
      buildType: "production",
      platform: "linux",
      arch: "amd64"
    }),
    LogTrace: (...args) => console.debug('[Trace]', ...args),
    LogDebug: (...args) => console.debug('[Debug]', ...args),
    LogInfo: (...args) => console.info('[Info]', ...args),
    LogWarning: (...args) => console.warn('[Warning]', ...args),
    LogError: (...args) => console.error('[Error]', ...args),
    LogFatal: (...args) => console.error('[Fatal]', ...args)
  };

  // ============================================================================
  // 4. window.wails Emulation
  // ============================================================================
  window.wails = {
    Callback: () => {},
    EventsNotify: (data) => {
      try {
        const parsed = typeof data === 'string' ? JSON.parse(data) : data;
        if (parsed && parsed.name) {
          const args = Array.isArray(parsed.data) ? parsed.data : (parsed.data !== undefined ? [parsed.data] : []);
          dispatchEventLocally(parsed.name, ...args);
        }
      } catch (e) {}
    },
    flags: {
      disableScrollbarDrag: false,
      disableDefaultContextMenu: false,
      enableResize: false,
      defaultCursor: null,
      borderThickness: 6,
      shouldDrag: false,
      deferDragToMouseMove: true,
      cssDragProperty: "--wails-draggable",
      cssDragValue: "drag",
      cssDropProperty: "--wails-drop-target",
      cssDropValue: "drop",
      enableWailsDragAndDrop: false
    },
    setCSSDragProperties: () => {},
    setCSSDropProperties: () => {}
  };
  window.WailsInvoke = () => {};

  // ============================================================================
  // 5. window.go Dynamic RPC Proxy
  // ============================================================================
  function createGoProxy(path = []) {
    return new Proxy(function () {}, {
      get(target, prop) {
        if (typeof prop !== 'string') return target[prop];
        if (prop === 'then') return undefined; // Avoid false Promise detection by async/await
        return createGoProxy([...path, prop]);
      },
      apply(target, thisArg, args) {
        const method = path.join('.');

        // Special handling for browser-incompatible desktop dialogs:
        if (method === 'main.App.SelectFolder') {
          const currentPath = args[0] || '/root/Downloads';
          const entered = window.prompt("Enter music/download folder path on server:", currentPath);
          return Promise.resolve(entered || currentPath);
        }

        if (method === 'main.App.SelectFile' || method === 'main.App.SelectAudioFiles' || method === 'main.App.SelectLyricsFiles') {
          const entered = window.prompt("Enter file path on server:", args[0] || "");
          return Promise.resolve(entered ? [entered] : []);
        }

        if (method === 'main.App.OpenFolder' || method === 'main.App.OpenConfigFolder') {
          console.log(`[Wails Web Shim] Server folder opened: ${args[0] || 'config'}`);
          return Promise.resolve();
        }

        // Call backend REST endpoint
        return fetch('/api/wails/call', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ method: method, args: args })
        }).then(async res => {
          const contentType = res.headers.get('content-type') || '';
          if (!contentType.includes('application/json')) {
            const txt = await res.text();
            throw new Error(`Server returned non-JSON (${res.status}): ${txt.slice(0, 100)}`);
          }
          const data = await res.json();
          if (!data.success) {
            throw new Error(data.error || `Call failed for ${method}`);
          }
          return data.result;
        });
      }
    });
  }

  window.go = createGoProxy();
  console.log("[Wails Web Shim] Loaded successfully. window.runtime and window.go ready.");
})();
