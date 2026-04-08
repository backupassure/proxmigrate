/**
 * Interactive terminal for community script deployments.
 *
 * Connects xterm.js to a WebSocket that bridges to an SSH PTY on the
 * Proxmox host.  Provides a real terminal experience — users can see
 * live output and interact with any script prompts.
 */
(function () {
  "use strict";

  var containerEl = document.getElementById("terminal-container");
  if (!containerEl) return;

  var jobId = containerEl.dataset.jobId;
  var wsPath = containerEl.dataset.wsPath;
  var isTerminal = containerEl.dataset.isTerminal === "true";

  // ── Initialise xterm.js ────────────────────────────────────────────
  var term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, monospace",
    theme: {
      background: "#1a1a2e",
      foreground: "#e0e0e0",
      cursor: "#3273dc",
      selectionBackground: "rgba(50, 115, 220, 0.3)",
    },
    scrollback: 5000,
    convertEol: true,
  });

  var fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(containerEl);
  fitAddon.fit();

  // ── Handle window resize ───────────────────────────────────────────
  var resizeTimeout;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(function () {
      fitAddon.fit();
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "resize",
            cols: term.cols,
            rows: term.rows,
          })
        );
      }
    }, 150);
  });

  // If the job is already done, just show stored output (no WebSocket)
  if (isTerminal) {
    var replayData = containerEl.dataset.replayData;
    if (replayData) {
      term.write(replayData);
    }
    term.write("\r\n\x1b[90m--- Session ended ---\x1b[0m\r\n");
    return;
  }

  // ── WebSocket connection ───────────────────────────────────────────
  var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  var wsUrl = protocol + "//" + window.location.host + wsPath;
  var ws = null;
  var reconnectAttempts = 0;
  var maxReconnectAttempts = 3;

  function connect() {
    term.write("\x1b[90mConnecting to deployment terminal...\x1b[0m\r\n");
    ws = new WebSocket(wsUrl);

    ws.onopen = function () {
      reconnectAttempts = 0;
      // Send initial terminal size
      ws.send(
        JSON.stringify({
          type: "resize",
          cols: term.cols,
          rows: term.rows,
        })
      );
    };

    ws.onmessage = function (evt) {
      var data = evt.data;

      // Control messages are prefixed with \x00
      if (data.charAt(0) === "\x00") {
        try {
          var msg = JSON.parse(data.substring(1));
          handleControlMessage(msg);
        } catch (e) {
          // Not valid JSON control — write as terminal output
          term.write(data);
        }
        return;
      }

      // Regular terminal output
      term.write(data);

      // ── Detect VMID/CTID to show Console Jump button ──────────────
      detectProxmoxId(data);
    };

    function detectProxmoxId(text) {
      // Patterns commonly seen in tteck scripts:
      // "Virtual Machine ID is 100" or "Container ID is 100"
      // "Successfully created a <name> VM (100)"
      var vmMatch = text.match(/(?:VM|Virtual Machine|Container)\s+ID(?:\s+is)?[:\s]+(\d+)/i) ||
                    text.match(/created.*(?:\s+\((\d+)\))/i);
      
      if (vmMatch && vmMatch[1]) {
        var id = vmMatch[1];
        var isLxc = wsPath.indexOf("/lxc/") !== -1;
        var jumpEl = document.getElementById(isLxc ? "ct-console-jump" : "vm-console-jump");
        var linkEl = document.getElementById(isLxc ? "ct-console-link" : "vm-console-link");
        
        if (jumpEl && linkEl && jumpEl.style.display === "none") {
          // Construct the Proxmox console URL (using our internal console viewer)
          var consoleUrl = isLxc ? "/lxc/" + id + "/console/" : "/vm/" + id + "/console/";
          linkEl.href = consoleUrl;
          jumpEl.style.display = "block";
          
          // Also try to update the page header if possible
          var headerId = document.querySelector(".is-monospace");
          if (headerId && headerId.textContent.trim() === "") {
             headerId.textContent = id;
          }
        }
      }
    }

    ws.onclose = function (evt) {
      if (evt.code === 4401) {
        term.write("\r\n\x1b[31mAuthentication required. Please log in.\x1b[0m\r\n");
        return;
      }
      if (evt.code === 4404) {
        term.write("\r\n\x1b[31mDeployment job not found.\x1b[0m\r\n");
        return;
      }

      // Auto-reconnect for unexpected disconnects
      if (reconnectAttempts < maxReconnectAttempts) {
        reconnectAttempts++;
        var delay = reconnectAttempts * 2;
        term.write(
          "\r\n\x1b[33mConnection lost. Reconnecting in " +
            delay +
            "s...\x1b[0m\r\n"
        );
        setTimeout(connect, delay * 1000);
      } else {
        term.write(
          "\r\n\x1b[90m--- Disconnected ---\x1b[0m\r\n"
        );
      }
    };

    ws.onerror = function () {
      // onclose will handle reconnection
    };
  }

  // ── Forward user input to WebSocket ────────────────────────────────
  term.onData(function (data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(data);
    }
  });

  // ── Handle control messages from the server ────────────────────────
  function handleControlMessage(msg) {
    switch (msg.type) {
      case "stage":
        updateStageUI(msg.stage);
        break;

      case "exit":
        term.write("\r\n\x1b[90m--- Deployment complete ---\x1b[0m\r\n");
        // Reload page after a short delay to show final state
        setTimeout(function () {
          location.reload();
        }, 2000);
        break;

      case "error":
        term.write(
          "\r\n\x1b[31m" + (msg.message || "An error occurred") + "\x1b[0m\r\n"
        );
        setTimeout(function () {
          location.reload();
        }, 3000);
        break;

      case "replay":
        if (msg.data) {
          term.write(msg.data);
        }
        break;
    }
  }

  // ── Update pipeline stage indicators ───────────────────────────────
  function updateStageUI(stage) {
    // The HTMX polling on the status partial will catch up,
    // but we can also update immediately for responsiveness
    var statusEl = document.getElementById("community-pipeline-status");
    if (statusEl) {
      // Trigger an immediate HTMX refresh of the status partial
      if (typeof htmx !== "undefined") {
        htmx.trigger(statusEl, "refresh");
      }
    }
  }

  // ── Start connection ───────────────────────────────────────────────
  connect();
})();
