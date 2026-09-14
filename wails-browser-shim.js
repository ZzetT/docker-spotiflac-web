/**
 * Wails v2 Browser Compatibility Shim for SpotiFLAC-Next
 * Enables the official React / Tailwind / Radix UI to run in standard web browsers
 * by proxying Wails Go bindings (window.go) and event system (window.runtime)
 * to the headless backend via HTTP and Server-Sent Events (SSE).
 */
(function () {
  console.log("[Wails Web Shim] Initializing browser adapter for SpotiFLAC-Next...");

  // ============================================================================
  // 0. Non-Secure Context & Mobile Browser Polyfills (LAN HTTP access e.g. http://192.168.x.x)
  // In modern browsers, crypto.randomUUID() and navigator.clipboard are only available
  // in Secure Contexts (HTTPS or localhost). When accessing over LAN IP via HTTP from a phone,
  // clipboard reading/writing throws or is undefined, and Radix UI ContextMenu suppresses
  // mobile touch callouts / context menus with -webkit-touch-callout: none and preventDefault().
  // We polyfill clipboard and restore native touch paste behavior.
  // ============================================================================
  if (typeof window.crypto !== 'object') {
    window.crypto = {};
  }
  if (typeof window.crypto.randomUUID !== 'function') {
    window.crypto.randomUUID = function () {
      if (typeof window.crypto.getRandomValues === 'function') {
        try {
          return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, function (c) {
            return (c ^ (window.crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16);
          });
        } catch (e) {}
      }
      return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
        var r = (Math.random() * 16) | 0;
        var v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
      });
    };
    console.log("[Wails Web Shim] Polyfilled crypto.randomUUID for non-secure HTTP context.");
  }

  const isTouchDevice = typeof window !== 'undefined' && (
    'ontouchstart' in window ||
    (navigator.maxTouchPoints && navigator.maxTouchPoints > 0) ||
    /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '')
  );

  let nativeClipboard = typeof navigator !== 'undefined' ? navigator.clipboard : null;

  async function safeReadClipboardText(promptFallback = true) {
    if (nativeClipboard && typeof nativeClipboard.readText === 'function') {
      try {
        const text = await nativeClipboard.readText();
        if (typeof text === 'string' && text.length > 0) {
          return text;
        }
      } catch (err) {
        // Insecure HTTP context or permission denied
      }
    }

    if (promptFallback) {
      try {
        const entered = window.prompt("Paste song link or search query:");
        if (entered) {
          return entered.trim();
        }
      } catch (e) {}
    }
    return "";
  }

  async function safeWriteClipboardText(text) {
    if (nativeClipboard && typeof nativeClipboard.writeText === 'function') {
      try {
        await nativeClipboard.writeText(text);
        return true;
      } catch (err) {}
    }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      ta.style.pointerEvents = "none";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const success = document.execCommand('copy');
      document.body.removeChild(ta);
      return success;
    } catch (e) {}
    return false;
  }

  // Polyfill / wrap navigator.clipboard for mobile & insecure HTTP contexts
  const clipboardPolyfill = {
    readText: () => safeReadClipboardText(true),
    writeText: (t) => safeWriteClipboardText(t)
  };

  try {
    Object.defineProperty(navigator, 'clipboard', {
      value: clipboardPolyfill,
      configurable: true,
      enumerable: true,
      writable: true
    });
    console.log("[Wails Web Shim] Polyfilled/adapted navigator.clipboard for mobile & HTTP contexts.");
  } catch (e) {
    try {
      if (typeof Navigator !== 'undefined' && Navigator.prototype) {
        Object.defineProperty(Navigator.prototype, 'clipboard', {
          get: () => clipboardPolyfill,
          configurable: true,
          enumerable: true
        });
        console.log("[Wails Web Shim] Polyfilled Navigator.prototype.clipboard.");
      } else {
        navigator.clipboard = clipboardPolyfill;
      }
    } catch (e2) {
      try { navigator.clipboard = clipboardPolyfill; } catch (e3) {}
    }
  }

  // Restore native mobile touch callout & context menu for text inputs
  try {
    const style = document.createElement('style');
    style.setAttribute('data-spotiflac-mobile-fix', 'true');
    style.textContent = `
      input, textarea, [data-slot="input"], [data-slot="context-menu-trigger"] {
        -webkit-touch-callout: default !important;
        -webkit-user-select: text !important;
        user-select: text !important;
      }
    `;
    if (document.head) {
      document.head.appendChild(style);
    } else {
      document.addEventListener('DOMContentLoaded', () => {
        if (document.head && !document.querySelector('[data-spotiflac-mobile-fix]')) {
          document.head.appendChild(style);
        }
      });
    }
  } catch (e) {}

  // On touch devices, prevent desktop context menu triggers from hijacking inputs
  if (isTouchDevice) {
    window.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'touch' && e.target && (
        e.target.tagName === 'INPUT' ||
        e.target.tagName === 'TEXTAREA' ||
        e.target.isContentEditable
      )) {
        e.stopPropagation();
      }
    }, true);

    window.addEventListener('contextmenu', function (e) {
      if (e.target && (
        e.target.tagName === 'INPUT' ||
        e.target.tagName === 'TEXTAREA' ||
        e.target.isContentEditable
      )) {
        // Prevent Radix ContextMenu from calling preventDefault() on native mobile menu
        e.stopPropagation();
      }
    }, true);
  }

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
            if (window._wails && typeof window._wails.dispatchWailsEvent === 'function') {
              window._wails.dispatchWailsEvent(payload);
            }
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
    ClipboardGetText: async (promptFallback = false) => {
      return await safeReadClipboardText(promptFallback);
    },
    ClipboardSetText: async (text) => {
      return await safeWriteClipboardText(text);
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
  // 5. Target Container Folder Picker Modal
  // Allows users to visually browse and select directories inside the target
  // container (e.g. /root/Music), completely isolated from the host machine.
  // ============================================================================
  function showTargetFolderPicker(initialPath) {
    return new Promise((resolve) => {
      let currentPath = initialPath ? initialPath.trim() : '/root/Music';
      if (!currentPath) currentPath = '/root/Music';

      const oldModal = document.getElementById('target-folder-picker-modal');
      if (oldModal) oldModal.remove();

      const overlay = document.createElement('div');
      overlay.id = 'target-folder-picker-modal';
      overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:999999;display:flex;align-items:center;justify-content:center;padding:16px;backdrop-filter:blur(4px);';

      const card = document.createElement('div');
      card.style.cssText = 'background:var(--card,#18181b);color:var(--foreground,#f4f4f5);border:1px solid var(--border,#27272a);border-radius:12px;width:100%;max-width:540px;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 25px 50px -12px rgba(0,0,0,0.5);overflow:hidden;font-family:system-ui,-apple-system,sans-serif;';

      card.innerHTML = `
        <div style="padding:16px 20px;border-bottom:1px solid var(--border,#27272a);display:flex;align-items:center;justify-content:space-between;background:var(--card,#18181b);">
          <div style="display:flex;align-items:center;gap:10px;">
            <svg style="width:20px;height:20px;color:#38bdf8;fill:currentColor;" viewBox="0 0 24 24">
              <path d="M19.5 21a3 3 0 0 0 3-3v-4.5a3 3 0 0 0-3-3h-1.5V9a3 3 0 0 0-3-3h-3.379a3 3 0 0 1-2.121-.879L8.379 4.001A3 3 0 0 0 6.257 3.122H4.5A3 3 0 0 0 1.5 6.122v11.878A3 3 0 0 0 4.5 21h15z"/>
            </svg>
            <span style="font-size:16px;font-weight:600;">Select Target Folder</span>
            <span style="font-size:11px;font-weight:600;background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);border-radius:9999px;padding:2px 8px;">Target Container</span>
          </div>
          <button id="tfp-close-btn" style="background:transparent;border:none;color:#a1a1aa;cursor:pointer;font-size:20px;padding:2px 6px;border-radius:4px;line-height:1;">&times;</button>
        </div>
        <div style="padding:10px 20px 6px 20px;font-size:12px;color:#a1a1aa;">
          Select or enter the destination folder inside the target container.
        </div>
        <div style="padding:6px 20px 10px 20px;display:flex;gap:8px;align-items:center;">
          <button id="tfp-up-btn" title="Parent Directory" style="background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);border-radius:6px;padding:6px 12px;cursor:pointer;font-size:12px;font-weight:500;display:flex;align-items:center;gap:4px;flex-shrink:0;">
            &uarr; Up
          </button>
          <div id="tfp-current-bar" style="flex:1;font-family:monospace;font-size:12px;background:rgba(0,0,0,0.3);border:1px solid var(--border,#27272a);border-radius:6px;padding:6px 10px;overflow-x:auto;white-space:nowrap;color:#38bdf8;">/root/Music</div>
          <button id="tfp-mkdir-btn" title="New Folder" style="background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12px;font-weight:500;flex-shrink:0;">
            + Folder
          </button>
        </div>
        <div id="tfp-folder-list" style="flex:1;min-height:200px;max-height:300px;overflow-y:auto;padding:8px 20px;display:flex;flex-direction:column;gap:4px;border-top:1px solid var(--border,#27272a);border-bottom:1px solid var(--border,#27272a);background:rgba(0,0,0,0.15);">
          <div style="padding:24px;text-align:center;color:#a1a1aa;font-size:13px;">Loading...</div>
        </div>
        <div style="padding:12px 20px;display:flex;flex-direction:column;gap:6px;">
          <label style="font-size:12px;color:#a1a1aa;font-weight:500;">Selected Path:</label>
          <input id="tfp-path-input" type="text" style="width:100%;box-sizing:border-box;background:rgba(0,0,0,0.3);border:1px solid var(--border,#3f3f46);border-radius:6px;padding:8px 12px;color:var(--foreground,#f4f4f5);font-family:monospace;font-size:13px;outline:none;" />
        </div>
        <div style="padding:12px 20px 16px 20px;display:flex;justify-content:flex-end;gap:10px;background:var(--muted,#18181b);">
          <button id="tfp-cancel-btn" style="background:transparent;border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);border-radius:6px;padding:7px 16px;cursor:pointer;font-size:13px;font-weight:500;">Cancel</button>
          <button id="tfp-select-btn" style="background:#38bdf8;border:none;color:#09090b;border-radius:6px;padding:7px 18px;cursor:pointer;font-size:13px;font-weight:600;">Select This Folder</button>
        </div>
      `;

      overlay.appendChild(card);
      document.body.appendChild(overlay);

      const pathInput = card.querySelector('#tfp-path-input');
      const currentBar = card.querySelector('#tfp-current-bar');
      const folderList = card.querySelector('#tfp-folder-list');
      const upBtn = card.querySelector('#tfp-up-btn');
      const mkdirBtn = card.querySelector('#tfp-mkdir-btn');
      const cancelBtn = card.querySelector('#tfp-cancel-btn');
      const closeBtn = card.querySelector('#tfp-close-btn');
      const selectBtn = card.querySelector('#tfp-select-btn');

      pathInput.value = currentPath;
      currentBar.textContent = currentPath;

      let parentDir = null;

      function closePicker(result) {
        window.removeEventListener('keydown', handleKey);
        overlay.remove();
        resolve(result);
      }

      function handleKey(e) {
        if (e.key === 'Escape') {
          e.preventDefault();
          closePicker(null);
        } else if (e.key === 'Enter' && e.target === pathInput) {
          e.preventDefault();
          closePicker(pathInput.value.trim());
        }
      }
      window.addEventListener('keydown', handleKey);

      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closePicker(null);
      });
      cancelBtn.addEventListener('click', () => closePicker(null));
      closeBtn.addEventListener('click', () => closePicker(null));
      selectBtn.addEventListener('click', () => closePicker(pathInput.value.trim() || currentPath));

      async function loadFolders(targetPath) {
        folderList.innerHTML = '<div style="padding:24px;text-align:center;color:#a1a1aa;font-size:13px;">Loading...</div>';
        try {
          const fetchFn = typeof window._rawFetch === 'function' ? window._rawFetch : fetch;
          const res = await fetchFn('/api/target/folders?path=' + encodeURIComponent(targetPath));
          if (!res.ok) throw new Error('HTTP ' + res.status);
          const data = await res.json();
          currentPath = data.current || targetPath;
          parentDir = data.parent;
          pathInput.value = currentPath;
          currentBar.textContent = currentPath;

          upBtn.disabled = !parentDir;
          upBtn.style.opacity = parentDir ? '1' : '0.4';
          upBtn.style.cursor = parentDir ? 'pointer' : 'default';

          folderList.innerHTML = '';
          const folders = data.folders || [];
          if (folders.length === 0) {
            folderList.innerHTML = '<div style="padding:24px;text-align:center;color:#71717a;font-size:13px;">No subdirectories found. You can select this folder or create a new one.</div>';
          } else {
            folders.forEach((f) => {
              const item = document.createElement('div');
              item.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;cursor:pointer;user-select:none;font-size:13px;transition:background 0.15s;';
              item.innerHTML = `
                <svg style="width:16px;height:16px;color:#eab308;fill:currentColor;flex-shrink:0;" viewBox="0 0 24 24">
                  <path d="M19.5 21a3 3 0 0 0 3-3v-4.5a3 3 0 0 0-3-3h-1.5V9a3 3 0 0 0-3-3h-3.379a3 3 0 0 1-2.121-.879L8.379 4.001A3 3 0 0 0 6.257 3.122H4.5A3 3 0 0 0 1.5 6.122v11.878A3 3 0 0 0 4.5 21h15z"/>
                </svg>
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">${f.name}</span>
              `;
              item.addEventListener('mouseenter', () => item.style.backgroundColor = 'rgba(255,255,255,0.06)');
              item.addEventListener('mouseleave', () => item.style.backgroundColor = 'transparent');
              item.addEventListener('click', () => {
                loadFolders(f.path);
              });
              folderList.appendChild(item);
            });
          }
        } catch (e) {
          folderList.innerHTML = `<div style="padding:24px;text-align:center;color:#ef4444;font-size:13px;">Failed to list directory: ${e.message}</div>`;
        }
      }

      upBtn.addEventListener('click', () => {
        if (parentDir) loadFolders(parentDir);
      });

      mkdirBtn.addEventListener('click', async () => {
        const name = window.prompt('Enter new folder name inside ' + currentPath + ':');
        if (!name || !name.trim()) return;
        const newPath = currentPath.replace(/\/+$/, '') + '/' + name.trim();
        try {
          const fetchFn = typeof window._rawFetch === 'function' ? window._rawFetch : fetch;
          const res = await fetchFn('/api/target/folders/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: newPath })
          });
          const d = await res.json();
          if (d.success) {
            loadFolders(newPath);
          } else {
            alert('Failed to create folder: ' + (d.error || 'Unknown error'));
          }
        } catch (err) {
          alert('Failed to create folder: ' + err.message);
        }
      });

      loadFolders(currentPath);
    });
  }

  // ============================================================================
  // 5b. Downloaded Artifact Folder & File Inspector Modal
  // Allows users to inspect downloaded albums, play audio tracks, view lyrics/art,
  // copy container paths, and download individual files or entire albums as ZIP.
  // ============================================================================
  function showArtifactFolderInspector(initialPath) {
    let currentPath = initialPath ? initialPath.trim() : '/root/Music';
    if (!currentPath) currentPath = '/root/Music';

    const oldModal = document.getElementById('artifact-folder-inspector-modal');
    if (oldModal) {
      const oldAudio = oldModal._audioEl;
      if (oldAudio) { oldAudio.pause(); oldAudio.src = ''; }
      oldModal.remove();
    }

    const overlay = document.createElement('div');
    overlay.id = 'artifact-folder-inspector-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.78);z-index:999999;display:flex;align-items:center;justify-content:center;padding:16px;backdrop-filter:blur(6px);box-sizing:border-box;';

    const card = document.createElement('div');
    card.style.cssText = 'background:var(--card,#121214);color:var(--foreground,#f4f4f5);border:1px solid var(--border,#27272a);border-radius:14px;width:100%;max-width:680px;max-height:90vh;display:flex;flex-direction:column;box-shadow:0 25px 50px -12px rgba(0,0,0,0.6);overflow:hidden;font-family:system-ui,-apple-system,sans-serif;';

    card.innerHTML = `
      <!-- Header -->
      <div style="padding:14px 18px;border-bottom:1px solid var(--border,#27272a);display:flex;align-items:center;justify-content:space-between;background:var(--card,#121214);flex-shrink:0;">
        <div style="display:flex;align-items:center;gap:10px;min-width:0;">
          <svg style="width:20px;height:20px;color:#10b981;fill:currentColor;flex-shrink:0;" viewBox="0 0 24 24">
            <path d="M19.5 21a3 3 0 0 0 3-3v-4.5a3 3 0 0 0-3-3h-1.5V9a3 3 0 0 0-3-3h-3.379a3 3 0 0 1-2.121-.879L8.379 4.001A3 3 0 0 0 6.257 3.122H4.5A3 3 0 0 0 1.5 6.122v11.878A3 3 0 0 0 4.5 21h15z"/>
          </svg>
          <span id="afi-folder-name" style="font-size:16px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Folder Inspector</span>
          <span style="font-size:11px;font-weight:600;background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.3);border-radius:9999px;padding:2px 8px;flex-shrink:0;">Downloaded Artifact</span>
        </div>
        <button id="afi-close-btn" title="Close (Esc)" style="background:transparent;border:none;color:#a1a1aa;cursor:pointer;font-size:22px;padding:2px 6px;border-radius:6px;line-height:1;transition:color 0.15s;">&times;</button>
      </div>

      <!-- Path bar & Quick Actions -->
      <div style="padding:10px 18px;background:rgba(0,0,0,0.25);border-bottom:1px solid var(--border,#27272a);display:flex;flex-wrap:wrap;gap:8px;align-items:center;flex-shrink:0;">
        <button id="afi-up-btn" title="Parent Directory" style="background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);border-radius:6px;padding:5px 10px;cursor:pointer;font-size:12px;font-weight:500;display:flex;align-items:center;gap:4px;flex-shrink:0;">
          &uarr; Up
        </button>
        <div id="afi-path-bar" style="flex:1;min-width:180px;font-family:monospace;font-size:12px;background:rgba(0,0,0,0.35);border:1px solid var(--border,#27272a);border-radius:6px;padding:5px 10px;overflow-x:auto;white-space:nowrap;color:#38bdf8;">/root/Music</div>
        <button id="afi-copy-btn" title="Copy server path to clipboard" style="background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);border-radius:6px;padding:5px 10px;cursor:pointer;font-size:12px;font-weight:500;display:flex;align-items:center;gap:5px;flex-shrink:0;transition:all 0.2s;">
          <svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
          <span id="afi-copy-text">Copy Path</span>
        </button>
        <a id="afi-zip-btn" href="#" download style="text-decoration:none;background:#10b981;border:none;color:#09090b;border-radius:6px;padding:5px 12px;cursor:pointer;font-size:12px;font-weight:600;display:flex;align-items:center;gap:5px;flex-shrink:0;transition:opacity 0.15s;">
          <svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
          <span id="afi-zip-text">Download ZIP</span>
        </a>
      </div>

      <!-- Stats row -->
      <div id="afi-stats-bar" style="padding:6px 18px;font-size:11px;color:#a1a1aa;background:rgba(255,255,255,0.02);border-bottom:1px solid var(--border,#27272a);display:flex;gap:12px;flex-wrap:wrap;flex-shrink:0;">
        <span>Loading folder details...</span>
      </div>

      <!-- Integrated Audio Player Bar (hidden initially) -->
      <div id="afi-player-bar" style="display:none;padding:10px 18px;background:rgba(16,185,129,0.08);border-bottom:1px solid rgba(16,185,129,0.25);flex-shrink:0;flex-direction:column;gap:6px;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">
          <div style="display:flex;align-items:center;gap:8px;min-width:0;flex:1;">
            <button id="afi-player-play-btn" style="width:30px;height:30px;border-radius:50%;background:#10b981;border:none;color:#09090b;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;">
              <svg id="afi-player-play-icon" style="width:14px;height:14px;fill:currentColor;" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
            </button>
            <div style="min-width:0;flex:1;">
              <div id="afi-player-title" style="font-size:13px;font-weight:600;color:var(--foreground,#f4f4f5);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Track Name</div>
              <div id="afi-player-time" style="font-size:11px;font-family:monospace;color:#a1a1aa;">0:00 / 0:00</div>
            </div>
          </div>
          <button id="afi-player-close-btn" title="Stop & Close Player" style="background:transparent;border:none;color:#a1a1aa;cursor:pointer;font-size:16px;padding:4px;border-radius:4px;line-height:1;">&times;</button>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <input id="afi-player-progress" type="range" min="0" max="100" value="0" step="0.1" style="flex:1;height:4px;accent-color:#10b981;cursor:pointer;" />
        </div>
      </div>

      <!-- File List -->
      <div id="afi-file-list" style="flex:1;min-height:220px;max-height:420px;overflow-y:auto;padding:8px 18px;display:flex;flex-direction:column;gap:3px;background:rgba(0,0,0,0.15);">
        <div style="padding:28px;text-align:center;color:#a1a1aa;font-size:13px;">Loading artifacts...</div>
      </div>

      <!-- Preview Drawer (e.g. for lyrics or image preview) -->
      <div id="afi-preview-drawer" style="display:none;max-height:180px;overflow-y:auto;padding:12px 18px;background:rgba(0,0,0,0.4);border-top:1px solid var(--border,#27272a);font-size:12px;font-family:monospace;white-space:pre-wrap;color:#e4e4e7;position:relative;">
        <button id="afi-preview-close" style="position:absolute;top:8px;right:12px;background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:#a1a1aa;cursor:pointer;border-radius:4px;padding:2px 6px;font-size:11px;">Close</button>
        <div id="afi-preview-content"></div>
      </div>

      <!-- Footer -->
      <div style="padding:10px 18px;display:flex;justify-content:space-between;align-items:center;background:var(--muted,#18181b);border-top:1px solid var(--border,#27272a);flex-shrink:0;">
        <span style="font-size:11px;color:#71717a;">Press Esc to close · Space to play/pause</span>
        <button id="afi-bottom-close-btn" style="background:var(--card,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);border-radius:6px;padding:6px 16px;cursor:pointer;font-size:12px;font-weight:500;">Close</button>
      </div>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const folderNameEl = card.querySelector('#afi-folder-name');
    const pathBar = card.querySelector('#afi-path-bar');
    const upBtn = card.querySelector('#afi-up-btn');
    const copyBtn = card.querySelector('#afi-copy-btn');
    const copyText = card.querySelector('#afi-copy-text');
    const zipBtn = card.querySelector('#afi-zip-btn');
    const zipText = card.querySelector('#afi-zip-text');
    const statsBar = card.querySelector('#afi-stats-bar');
    const fileList = card.querySelector('#afi-file-list');
    const closeBtn = card.querySelector('#afi-close-btn');
    const bottomCloseBtn = card.querySelector('#afi-bottom-close-btn');

    // Player Elements
    const playerBar = card.querySelector('#afi-player-bar');
    const playerPlayBtn = card.querySelector('#afi-player-play-btn');
    const playerPlayIcon = card.querySelector('#afi-player-play-icon');
    const playerTitle = card.querySelector('#afi-player-title');
    const playerTime = card.querySelector('#afi-player-time');
    const playerProgress = card.querySelector('#afi-player-progress');
    const playerCloseBtn = card.querySelector('#afi-player-close-btn');

    // Preview Drawer
    const previewDrawer = card.querySelector('#afi-preview-drawer');
    const previewContent = card.querySelector('#afi-preview-content');
    const previewClose = card.querySelector('#afi-preview-close');

    // State
    const audio = new Audio();
    overlay._audioEl = audio;
    let currentTracks = [];
    let currentPlayingTrack = null;
    let parentDir = null;
    let isSeeking = false;

    function formatTime(secs) {
      if (!isFinite(secs) || isNaN(secs) || secs < 0) return '0:00';
      const m = Math.floor(secs / 60);
      const s = Math.floor(secs % 60);
      return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function closeModal() {
      audio.pause();
      audio.src = '';
      window.removeEventListener('keydown', handleKey);
      overlay.remove();
    }

    function handleKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeModal();
      } else if (e.key === ' ' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
        e.preventDefault();
        togglePlayPause();
      }
    }
    window.addEventListener('keydown', handleKey);

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeModal();
    });
    closeBtn.addEventListener('click', closeModal);
    bottomCloseBtn.addEventListener('click', closeModal);

    // Copy Path Button
    copyBtn.addEventListener('click', async () => {
      const ok = await safeWriteClipboardText(currentPath);
      if (ok) {
        copyText.textContent = 'Copied!';
        copyBtn.style.borderColor = '#10b981';
        copyBtn.style.color = '#10b981';
        setTimeout(() => {
          copyText.textContent = 'Copy Path';
          copyBtn.style.borderColor = 'var(--border,#3f3f46)';
          copyBtn.style.color = 'var(--foreground,#f4f4f5)';
        }, 2000);
      }
    });

    previewClose.addEventListener('click', () => {
      previewDrawer.style.display = 'none';
    });

    // Audio Playback Engine
    function playTrack(track) {
      if (!track) return;
      currentPlayingTrack = track;
      playerTitle.textContent = track.name;
      playerBar.style.display = 'flex';
      audio.src = track.url;
      audio.play().catch(e => console.warn('[AFI Audio] Autoplay blocked/failed:', e));
      updatePlayIcons();
    }

    function togglePlayPause() {
      if (!currentPlayingTrack && currentTracks.length > 0) {
        playTrack(currentTracks[0]);
        return;
      }
      if (audio.paused) {
        audio.play().catch(() => {});
      } else {
        audio.pause();
      }
      updatePlayIcons();
    }

    playerPlayBtn.addEventListener('click', togglePlayPause);
    playerCloseBtn.addEventListener('click', () => {
      audio.pause();
      audio.src = '';
      currentPlayingTrack = null;
      playerBar.style.display = 'none';
      updatePlayIcons();
    });

    audio.addEventListener('play', updatePlayIcons);
    audio.addEventListener('pause', updatePlayIcons);
    audio.addEventListener('timeupdate', () => {
      if (!isSeeking && audio.duration) {
        playerProgress.value = (audio.currentTime / audio.duration) * 100;
        playerTime.textContent = formatTime(audio.currentTime) + ' / ' + formatTime(audio.duration);
      }
    });
    audio.addEventListener('ended', () => {
      if (!currentPlayingTrack) return;
      const idx = currentTracks.findIndex(t => t.path === currentPlayingTrack.path);
      if (idx !== -1 && idx + 1 < currentTracks.length) {
        playTrack(currentTracks[idx + 1]);
      } else {
        updatePlayIcons();
      }
    });

    playerProgress.addEventListener('input', () => {
      isSeeking = true;
      if (audio.duration) {
        const targetTime = (playerProgress.value / 100) * audio.duration;
        playerTime.textContent = formatTime(targetTime) + ' / ' + formatTime(audio.duration);
      }
    });
    playerProgress.addEventListener('change', () => {
      if (audio.duration) {
        audio.currentTime = (playerProgress.value / 100) * audio.duration;
      }
      isSeeking = false;
    });

    function updatePlayIcons() {
      const isPlaying = !audio.paused && audio.src && currentPlayingTrack;
      if (playerPlayIcon) {
        playerPlayIcon.innerHTML = isPlaying
          ? '<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>'
          : '<path d="M8 5v14l11-7z"/>';
      }
      const listBtns = fileList.querySelectorAll('[data-play-path]');
      listBtns.forEach(btn => {
        const p = btn.getAttribute('data-play-path');
        const row = btn.closest('[data-file-row]');
        if (currentPlayingTrack && p === currentPlayingTrack.path && isPlaying) {
          btn.innerHTML = '<svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
          btn.style.background = '#10b981';
          btn.style.color = '#09090b';
          if (row) row.style.borderColor = 'rgba(16,185,129,0.5)';
        } else {
          btn.innerHTML = '<svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
          btn.style.background = 'var(--muted,#27272a)';
          btn.style.color = 'var(--foreground,#f4f4f5)';
          if (row && !row.style.borderColor.includes('56,189,248')) row.style.borderColor = 'transparent';
        }
      });
    }

    async function loadDirectory(targetPath) {
      fileList.innerHTML = '<div style="padding:28px;text-align:center;color:#a1a1aa;font-size:13px;">Loading artifacts...</div>';
      try {
        const fetchFn = typeof window._rawFetch === 'function' ? window._rawFetch : fetch;
        const res = await fetchFn('/api/target/files?path=' + encodeURIComponent(targetPath));
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        if (!data.success && data.error) throw new Error(data.error);

        currentPath = data.current || targetPath;
        parentDir = data.parent;
        folderNameEl.textContent = data.folder_name || 'Folder Inspector';
        pathBar.textContent = currentPath;
        pathBar.title = currentPath;

        upBtn.disabled = !parentDir;
        upBtn.style.opacity = parentDir ? '1' : '0.4';
        upBtn.style.cursor = parentDir ? 'pointer' : 'default';

        zipBtn.href = data.zip_url || '#';
        zipText.textContent = `Download ZIP (${data.formatted_total_size || '0 B'})`;

        statsBar.innerHTML = `
          <span>🎵 <strong>${data.audio_count || 0}</strong> Tracks</span>
          <span>·</span>
          <span>📁 <strong>${data.total_files || 0}</strong> Files</span>
          <span>·</span>
          <span>💾 <strong>${data.formatted_total_size || '0 B'}</strong> Total Size</span>
          ${data.lyrics_count ? `<span>·</span><span>📄 <strong>${data.lyrics_count}</strong> Lyrics</span>` : ''}
          ${data.image_count ? `<span>·</span><span>🖼️ <strong>${data.image_count}</strong> Images</span>` : ''}
        `;

        fileList.innerHTML = '';
        currentTracks = (data.files || []).filter(f => f.type === 'audio');

        // Render subfolders first if any
        (data.folders || []).forEach(f => {
          const item = document.createElement('div');
          item.style.cssText = 'display:flex;align-items:center;gap:10px;padding:7px 10px;border-radius:8px;border:1px solid transparent;cursor:pointer;user-select:none;font-size:13px;background:rgba(255,255,255,0.03);transition:background 0.15s;';
          item.innerHTML = `
            <svg style="width:16px;height:16px;color:#eab308;fill:currentColor;flex-shrink:0;" viewBox="0 0 24 24">
              <path d="M19.5 21a3 3 0 0 0 3-3v-4.5a3 3 0 0 0-3-3h-1.5V9a3 3 0 0 0-3-3h-3.379a3 3 0 0 1-2.121-.879L8.379 4.001A3 3 0 0 0 6.257 3.122H4.5A3 3 0 0 0 1.5 6.122v11.878A3 3 0 0 0 4.5 21h15z"/>
            </svg>
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500;">${f.name}</span>
            <span style="font-size:11px;color:#a1a1aa;margin-right:4px;">${f.count} items · ${f.formatted_size}</span>
            <svg style="width:12px;height:12px;color:#a1a1aa;fill:currentColor;flex-shrink:0;" viewBox="0 0 24 24"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>
          `;
          item.addEventListener('mouseenter', () => item.style.backgroundColor = 'rgba(255,255,255,0.08)');
          item.addEventListener('mouseleave', () => item.style.backgroundColor = 'rgba(255,255,255,0.03)');
          item.addEventListener('click', () => loadDirectory(f.path));
          fileList.appendChild(item);
        });

        const files = data.files || [];
        if (files.length === 0 && (!data.folders || data.folders.length === 0)) {
          fileList.innerHTML = '<div style="padding:32px;text-align:center;color:#71717a;font-size:13px;">No files found in this directory.</div>';
          return;
        }

        files.forEach(f => {
          const row = document.createElement('div');
          row.setAttribute('data-file-row', 'true');
          row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:8px;border:1px solid transparent;font-size:13px;background:rgba(255,255,255,0.02);transition:background 0.15s, border-color 0.15s;';

          let iconHtml = '';
          let badgeColor = '#71717a';
          let badgeBg = 'rgba(113,113,122,0.15)';

          if (f.type === 'audio') {
            badgeColor = '#38bdf8';
            badgeBg = 'rgba(56,189,248,0.15)';
            iconHtml = `<svg style="width:16px;height:16px;color:#38bdf8;fill:currentColor;flex-shrink:0;" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>`;
          } else if (f.type === 'lyrics') {
            badgeColor = '#f59e0b';
            badgeBg = 'rgba(245,158,11,0.15)';
            iconHtml = `<svg style="width:16px;height:16px;color:#f59e0b;fill:currentColor;flex-shrink:0;" viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>`;
          } else if (f.type === 'image') {
            badgeColor = '#10b981';
            badgeBg = 'rgba(16,185,129,0.15)';
            iconHtml = `<img src="${f.url}" alt="" style="width:24px;height:24px;border-radius:4px;object-fit:cover;flex-shrink:0;" onerror="this.style.display='none'" />`;
          } else {
            iconHtml = `<svg style="width:16px;height:16px;color:#a1a1aa;fill:currentColor;flex-shrink:0;" viewBox="0 0 24 24"><path d="M6 2c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6H6zm7 7V3.5L18.5 9H13z"/></svg>`;
          }

          let extraActionHtml = '';
          if (f.type === 'audio') {
            extraActionHtml = `
              <button data-play-path="${f.path}" title="Play Track Preview" style="width:26px;height:26px;border-radius:6px;background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:all 0.15s;">
                <svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              </button>
            `;
          } else if (f.type === 'lyrics') {
            extraActionHtml = `
              <button data-view-lyrics="${f.path}" title="View Lyrics" style="width:26px;height:26px;border-radius:6px;background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:all 0.15s;">
                <svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>
              </button>
            `;
          } else if (f.type === 'image') {
            extraActionHtml = `
              <button data-view-image="${f.url}" title="View Cover Art" style="width:26px;height:26px;border-radius:6px;background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:all 0.15s;">
                <svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>
              </button>
            `;
          }

          row.innerHTML = `
            ${iconHtml}
            <div style="flex:1;min-width:0;display:flex;align-items:center;gap:8px;">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500;">${f.name}</span>
              <span style="font-size:10px;font-weight:600;background:${badgeBg};color:${badgeColor};border:1px solid ${badgeColor}33;border-radius:4px;padding:1px 5px;flex-shrink:0;">${f.ext}</span>
            </div>
            <span style="font-size:11px;font-family:monospace;color:#a1a1aa;white-space:nowrap;">${f.formatted_size}</span>
            <div style="display:flex;align-items:center;gap:5px;flex-shrink:0;">
              ${extraActionHtml}
              <a href="${f.download_url}" download title="Download file" style="width:26px;height:26px;border-radius:6px;background:var(--muted,#27272a);border:1px solid var(--border,#3f3f46);color:var(--foreground,#f4f4f5);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;text-decoration:none;transition:all 0.15s;">
                <svg style="width:13px;height:13px;fill:currentColor;" viewBox="0 0 24 24"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
              </a>
            </div>
          `;

          row.addEventListener('mouseenter', () => {
            if (!row.style.borderColor.includes('16,185,129') && !row.style.borderColor.includes('56,189,248')) {
              row.style.backgroundColor = 'rgba(255,255,255,0.06)';
            }
          });
          row.addEventListener('mouseleave', () => {
            if (!row.style.borderColor.includes('16,185,129') && !row.style.borderColor.includes('56,189,248')) {
              row.style.backgroundColor = 'rgba(255,255,255,0.02)';
            }
          });

          // Play button action
          const playBtn = row.querySelector('[data-play-path]');
          if (playBtn) {
            playBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              if (currentPlayingTrack && currentPlayingTrack.path === f.path) {
                togglePlayPause();
              } else {
                playTrack(f);
              }
            });
          }

          // View Lyrics action
          const lyricsBtn = row.querySelector('[data-view-lyrics]');
          if (lyricsBtn) {
            lyricsBtn.addEventListener('click', async (e) => {
              e.stopPropagation();
              previewDrawer.style.display = 'block';
              previewContent.textContent = 'Loading lyrics...';
              try {
                const fetchFn = typeof window._rawFetch === 'function' ? window._rawFetch : fetch;
                const lRes = await fetchFn(f.url);
                const lTxt = await lRes.text();
                previewContent.textContent = lTxt || '(Empty lyrics file)';
              } catch (err) {
                previewContent.textContent = 'Failed to load lyrics: ' + err.message;
              }
            });
          }

          // View Image action
          const imgBtn = row.querySelector('[data-view-image]');
          if (imgBtn) {
            imgBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              previewDrawer.style.display = 'block';
              previewContent.innerHTML = `<div style="text-align:center;"><img src="${f.url}" alt="" style="max-height:140px;max-width:100%;border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,0.5);" /></div>`;
            });
          }

          fileList.appendChild(row);

          // Focus highlight
          if (data.focused_file && f.name === data.focused_file) {
            row.style.borderColor = 'rgba(56,189,248,0.7)';
            row.style.backgroundColor = 'rgba(56,189,248,0.1)';
            setTimeout(() => row.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 100);
          }
        });

        updatePlayIcons();

      } catch (err) {
        fileList.innerHTML = `<div style="padding:28px;text-align:center;color:#ef4444;font-size:13px;">Failed to inspect folder: ${err.message}</div>`;
      }
    }

    upBtn.addEventListener('click', () => {
      if (parentDir) loadDirectory(parentDir);
    });

    loadDirectory(currentPath);
  }

  window.showArtifactFolderInspector = showArtifactFolderInspector;

  // ============================================================================
  // 6. Wails v3 fetch Interception for Headless / Browser Dialogs
  // ============================================================================
  if (typeof window.fetch === 'function') {
    const originalFetch = window.fetch;
    window._rawFetch = originalFetch;
    window.fetch = async function (resource, init) {
      const url = typeof resource === 'string' ? resource : (resource?.href || resource?.url || String(resource) || '');
      if (url && url.includes('/wails/runtime') && init && init.method === 'POST' && init.body) {
        try {
          const payload = typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
          const mid = payload?.args?.methodID;
          // 237181597: main.App.SelectFolder
          if (mid === 237181597) {
            const currentPath = payload.args?.args?.[0] || '/root/Music';
            const selected = await showTargetFolderPicker(currentPath);
            return new Response(JSON.stringify(selected || currentPath), {
              status: 200,
              headers: { 'Content-Type': 'application/json' }
            });
          }
          // 3561358672: SelectLyricsFiles, 3818965540: SelectAudioFiles, 2427571203: SelectFile
          if (mid === 3561358672 || mid === 3818965540 || mid === 2427571203) {
            const entered = window.prompt("Enter file path on server:", "/root/Music");
            return new Response(JSON.stringify(entered ? [entered] : []), {
              status: 200,
              headers: { 'Content-Type': 'application/json' }
            });
          }
          // 3894305329: OpenFolder, 431469111: OpenConfigFolder
          if (mid === 3894305329 || mid === 431469111) {
            const folderPath = payload.args?.args?.[0] || (mid === 431469111 ? '/root/.spotiflac-next' : '/root/Music');
            showArtifactFolderInspector(folderPath);
            return new Response(JSON.stringify(null), {
              status: 200,
              headers: { 'Content-Type': 'application/json' }
            });
          }
        } catch (e) {}
      }
      return originalFetch.apply(this, arguments);
    };
  }

  // ============================================================================
  // 7. window.go Dynamic RPC Proxy
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
          const currentPath = args[0] || '/root/Music';
          return showTargetFolderPicker(currentPath).then(selected => selected || currentPath);
        }

        if (method === 'main.App.SelectFile' || method === 'main.App.SelectAudioFiles' || method === 'main.App.SelectLyricsFiles') {
          const entered = window.prompt("Enter file path on server:", args[0] || "");
          return Promise.resolve(entered ? [entered] : []);
        }

        if (method === 'main.App.OpenFolder' || method === 'main.App.OpenConfigFolder') {
          const folderPath = args[0] || (method === 'main.App.OpenConfigFolder' ? '/root/.spotiflac-next' : '/root/Music');
          showArtifactFolderInspector(folderPath);
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

  // ============================================================================
  // 8. Browser Navigation History Synchronization (Browser Back/Forward Buttons)
  // Integrates browser history with SpotiFLAC-Next's in-app navigation so clicking
  // the browser Back/Forward buttons (and Alt+Left/Right or mobile back gestures)
  // behaves identically to clicking the in-app back/forward buttons inside the website.
  // ============================================================================
  const historyMgr = {
    step: 0,
    isNavigating: false,
    handlers: null,

    register: function (h) {
      this.handlers = h;
    },

    push: function (info) {
      if (this.isNavigating) return;
      this.step++;
      try {
        window.history.pushState({ spotiflacStep: this.step, info: info || null }, '', window.location.pathname);
      } catch (e) {
        console.warn('[Wails Web Shim] pushState failed:', e);
      }
    },

    onInAppBack: function () {
      if (this.isNavigating) return;
      if (this.step > 0) {
        this.isNavigating = true;
        this.step--;
        try {
          window.history.back();
        } catch (e) {}
        setTimeout(() => { this.isNavigating = false; }, 80);
      }
    },

    onInAppForward: function () {
      if (this.isNavigating) return;
      this.isNavigating = true;
      this.step++;
      try {
        window.history.forward();
      } catch (e) {}
      setTimeout(() => { this.isNavigating = false; }, 80);
    },

    init: function () {
      if (this._initialized) return;
      this._initialized = true;

      try {
        // Reset base history state on fresh page load/reload
        window.history.replaceState({ spotiflacStep: 0 }, '', window.location.pathname);
        this.step = 0;
      } catch (e) {}

      window.addEventListener('popstate', (e) => {
        if (this.isNavigating) {
          this.isNavigating = false;
          if (e.state && typeof e.state.spotiflacStep === 'number') {
            this.step = e.state.spotiflacStep;
          }
          return;
        }

        const targetStep = (e.state && typeof e.state.spotiflacStep === 'number') ? e.state.spotiflacStep : 0;
        const prevStep = this.step;
        this.step = targetStep;

        this.isNavigating = true;
        try {
          if (targetStep < prevStep) {
            const steps = prevStep - targetStep;
            for (let s = 0; s < steps; s++) {
              if (this.handlers && typeof this.handlers.canGoBack === 'function' && !this.handlers.canGoBack()) {
                break;
              }
              if (this.handlers && typeof this.handlers.onBack === 'function') {
                this.handlers.onBack();
              } else {
                // DOM fallback
                const backBtn = document.querySelector('button[aria-label="Go to previous page"]') ||
                                document.querySelector('div.fixed.top-1\\.5.left-16 button');
                if (backBtn && !backBtn.disabled) {
                  backBtn.click();
                }
              }
            }
          } else if (targetStep > prevStep) {
            const steps = targetStep - prevStep;
            for (let s = 0; s < steps; s++) {
              if (this.handlers && typeof this.handlers.canGoForward === 'function' && !this.handlers.canGoForward()) {
                break;
              }
              if (this.handlers && typeof this.handlers.onForward === 'function') {
                this.handlers.onForward();
              } else {
                // DOM fallback
                const forwardBtns = document.querySelectorAll('div.fixed.top-1\\.5.left-16 button');
                if (forwardBtns && forwardBtns.length >= 2 && !forwardBtns[1].disabled) {
                  forwardBtns[1].click();
                }
              }
            }
          }
        } catch (err) {
          console.error('[Wails Web Shim] Error handling browser history popstate:', err);
        } finally {
          setTimeout(() => { this.isNavigating = false; }, 80);
        }
      });

      // Keyboard navigation shortcuts: Alt+Left Arrow (back) and Alt+Right Arrow (forward)
      window.addEventListener('keydown', (e) => {
        if (e.altKey && e.key === 'ArrowLeft') {
          if (this.handlers && typeof this.handlers.canGoBack === 'function' && this.handlers.canGoBack()) {
            e.preventDefault();
            this.handlers.onBack();
          }
        } else if (e.altKey && e.key === 'ArrowRight') {
          if (this.handlers && typeof this.handlers.canGoForward === 'function' && this.handlers.canGoForward()) {
            e.preventDefault();
            this.handlers.onForward();
          }
        }
      });
    }
  };

  historyMgr.init();
  window.__spotiflacHistory = historyMgr;

  window.go = createGoProxy();
  console.log("[Wails Web Shim] Loaded successfully. window.runtime and window.go ready.");
})();
