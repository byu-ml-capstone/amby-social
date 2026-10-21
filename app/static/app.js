  let nekoLoader;

  function loadNeko() {
    if (window.createNeko) return Promise.resolve(window.createNeko);
    if (!nekoLoader) {
      nekoLoader = new Promise((resolve, reject) => {
        const existing = document.querySelector("script[data-kanbanflow-neko]");
        if (existing) {
          existing.addEventListener("load", () => resolve(window.createNeko), { once: true });
          existing.addEventListener("error", reject, { once: true });
          return;
        }
        const script = document.createElement("script");
        script.src = "https://louisabraham.github.io/nekojs/neko.js";
        script.dataset.kanbanflowNeko = "true";
        script.onload = () => resolve(window.createNeko);
        script.onerror = reject;
        document.head.appendChild(script);
      });
    }
    return nekoLoader;
  }

  function setupNeko() {
    const toggle = document.getElementById("neko-toggle");
    if (!toggle) return;
    let enabled = window.localStorage.getItem("kanbanflow_cat") !== "off";
    let neko;
    let nekoFactory;
    let reactionTimer;
    let cancelled = false;

    const update = () => {
      toggle.textContent = enabled ? "Neko on" : "Neko off";
      toggle.setAttribute("aria-pressed", String(enabled));
    };
    const start = (behaviorMode = 0) => loadNeko().then((createNeko) => {
      if (cancelled || !createNeko) return;
      nekoFactory = createNeko;
      neko = createNeko({ speed: 18, fps: 90, behaviorMode, allowBehaviorChange: true });
      neko.start();
    }).catch(() => {});
    const stop = () => {
      clearTimeout(reactionTimer);
      neko?.destroy();
      neko = undefined;
    };
    const react = (message, _behaviorMode, duration) => {
      toggle.textContent = message;
      toggle.classList.add("is-reacting");
      clearTimeout(reactionTimer);
      const nekoElement = neko?.element || document.querySelector(".neko");
      nekoElement?.classList.add("neko-celebrating");
      reactionTimer = setTimeout(() => {
        nekoElement?.classList.remove("neko-celebrating");
        toggle.classList.remove("is-reacting");
        update();
      }, duration);
    };
    window.kanbanNekoReact = react;

    update();
    if (enabled) start();
    toggle.addEventListener("click", () => {
      enabled = !enabled;
      window.localStorage.setItem("kanbanflow_cat", enabled ? "on" : "off");
      toggle.classList.remove("is-reacting");
      update();
      if (enabled) start(); else stop();
    });
    window.addEventListener("pagehide", () => { cancelled = true; stop(); delete window.kanbanNekoReact; }, { once: true });
  }

  function keepNekoAboveOpenDialog() {
    const neko = document.querySelector(".neko");
    if (!neko) return;
    const openDialog = document.querySelector("dialog[open]");
    if (openDialog && neko.parentElement !== openDialog) {
      openDialog.appendChild(neko);
    } else if (!openDialog && neko.parentElement !== document.body) {
      document.body.appendChild(neko);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupNeko();
    const dialogObserver = new MutationObserver(keepNekoAboveOpenDialog);
    dialogObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ["open"],
      childList: true,
      subtree: true,
    });
    keepNekoAboveOpenDialog();
  });
  document.addEventListener("close", keepNekoAboveOpenDialog, true);

  let lastSubtaskCompleted;
  function checkSubtaskProgress() {
    const section = document.querySelector("#ticket-subtasks");
    if (!section) return;
    const completed = Number(section.dataset.subtaskCompleted || 0);
    const total = Number(section.dataset.subtaskTotal || 0);
    if (lastSubtaskCompleted !== undefined && completed > lastSubtaskCompleted) {
      const finished = total > 0 && completed === total;
      window.kanbanNekoReact?.(
        finished ? "Neko celebrates!" : "Neko says: nice!",
        2,
        finished ? 2800 : 1600,
      );
    }
    lastSubtaskCompleted = completed;
  }
  document.addEventListener("DOMContentLoaded", checkSubtaskProgress);
  document.addEventListener("htmx:afterSwap", (event) => {
    if (event.target?.id === "ticket-subtasks") checkSubtaskProgress();
  });

(() => {
  const inputSelector = "[data-mention-input]";

  function menuFor(input) {
    return document.getElementById(input.getAttribute("aria-controls"));
  }

  function mentionAtCursor(input) {
    const before = input.value.slice(0, input.selectionStart);
    const match = before.match(/(?:^|\s)@([^@\n]*)$/);
    if (!match) return null;
    return { query: match[1].trim().toLowerCase(), start: before.lastIndexOf("@") };
  }

  function closeMenu(input) {
    const menu = menuFor(input);
    if (!menu) return;
    menu.hidden = true;
    input.setAttribute("aria-expanded", "false");
  }

  function syncSelected(input) {
    const holder = input.closest("form").querySelector("[data-selected-mentions]");
    holder.querySelectorAll("input").forEach((hidden) => {
      const name = hidden.dataset.mentionName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const token = new RegExp(`(^|\\s)@${name}(?=\\s|$|[.,!?;:])`);
      if (!token.test(input.value)) hidden.remove();
    });
  }

  function showMatches(input) {
    syncSelected(input);
    const mention = mentionAtCursor(input);
    const menu = menuFor(input);
    if (!mention || !menu) return closeMenu(input);
    let visible = 0;
    menu.querySelectorAll("[data-mention-id]").forEach((option) => {
      option.hidden = !option.dataset.mentionSearch.includes(mention.query);
      option.setAttribute("aria-selected", visible === 0 && !option.hidden ? "true" : "false");
      if (!option.hidden) visible += 1;
    });
    menu.hidden = visible === 0;
    input.setAttribute("aria-expanded", String(visible > 0));
  }

  function selectMention(input, option) {
    const mention = mentionAtCursor(input);
    if (!mention) return;
    const name = option.dataset.mentionName;
    const insertion = `@${name} `;
    input.setRangeText(insertion, mention.start, input.selectionStart, "end");
    const holder = input.closest("form").querySelector("[data-selected-mentions]");
    if (![...holder.children].some((item) => item.value === option.dataset.mentionId)) {
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "mention_ids";
      hidden.value = option.dataset.mentionId;
      hidden.dataset.mentionName = name;
      holder.append(hidden);
    }
    closeMenu(input);
    input.focus();
  }

  function visibleOptions(input) {
    const menu = menuFor(input);
    return menu ? [...menu.querySelectorAll("[data-mention-id]:not([hidden])")] : [];
  }

  function localizeTimes(root = document) {
    root.querySelectorAll("[data-local-time]").forEach((time) => {
      const value = new Date(time.dateTime);
      if (!Number.isNaN(value.getTime())) {
        time.textContent = new Intl.DateTimeFormat([], {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(value);
      }
    });
  }

  const attachmentsSelector = "[data-ticket-attachments]";

  function setAttachmentStatus(section, message) {
    section.querySelector("[data-attachment-status]").textContent = message;
  }

  // Swap whichever section is live now: an earlier response may already have replaced the one
  // this request started from. Resolves true when the server accepted the change.
  async function postAttachments(section, url, body, failure) {
    body.append("_csrf", section.dataset.csrf);
    const live = () => document.querySelector(attachmentsSelector) || section;
    live().setAttribute("aria-busy", "true");
    try {
      const response = await fetch(url, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
      });
      // Errors come back as the section too (with its message); anything else is a failure.
      const template = document.createElement("template");
      template.innerHTML = await response.text();
      const next = template.content.querySelector(attachmentsSelector);
      if (!next) throw new Error();
      live().replaceWith(next);
      return response.ok;
    } catch {
      live().removeAttribute("aria-busy");
      setAttachmentStatus(live(), failure);
      return false;
    }
  }

  // One request per file keeps each under the proxy's body limit; the first rejection stops the rest.
  async function uploadAttachments(section, fileList) {
    const files = [...fileList];
    if (!section || !files.length) return;
    const tooLarge = files.find((file) => file.size > Number(section.dataset.maxBytes));
    if (tooLarge) return setAttachmentStatus(section, `${tooLarge.name} is larger than 10 MB.`);
    for (const [index, file] of files.entries()) {
      const current = document.querySelector(attachmentsSelector) || section;
      setAttachmentStatus(current, files.length > 1 ? `Uploading ${index + 1} of ${files.length}…` : "Uploading…");
      const body = new FormData();
      body.append("files", file, file.name || "pasted-image.png");
      if (!(await postAttachments(current, section.dataset.uploadUrl, body, "Upload failed. Try again."))) return;
    }
  }

  // The whole ticket panel is the drop target; the section shows where files land.
  const attachmentsIn = (target) => target.closest?.("#ticket-detail-panel")?.querySelector(attachmentsSelector);
  const carriesFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");

  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-attachment-input]")) return;
    uploadAttachments(event.target.closest(attachmentsSelector), event.target.files);
    event.target.value = "";
  });

  document.addEventListener("dragover", (event) => {
    const section = carriesFiles(event) && attachmentsIn(event.target);
    if (!section) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    section.classList.add("is-drag-over");
  });

  document.addEventListener("dragleave", (event) => {
    const panel = event.target.closest?.("#ticket-detail-panel");
    if (panel && !panel.contains(event.relatedTarget)) {
      panel.querySelector(attachmentsSelector)?.classList.remove("is-drag-over");
    }
  });

  document.addEventListener("drop", (event) => {
    const section = carriesFiles(event) && attachmentsIn(event.target);
    if (!section) return;
    event.preventDefault();
    section.classList.remove("is-drag-over");
    uploadAttachments(section, event.dataTransfer.files);
  });

  document.addEventListener("paste", (event) => {
    const section = document.querySelector(attachmentsSelector);
    const clipboard = event.clipboardData;
    if (!section || !clipboard?.files.length) return;
    // Office apps add a picture of copied text; in a text field that stays a text paste.
    if (event.target.matches?.("input, textarea") && clipboard.types.includes("text/plain")) return;
    event.preventDefault();
    uploadAttachments(section, clipboard.files);
  });

  document.addEventListener("DOMContentLoaded", () => localizeTimes());
  document.addEventListener("htmx:afterSwap", (event) => localizeTimes(event.target));

  document.addEventListener("input", (event) => {
    if (event.target.matches(inputSelector)) showMatches(event.target);
  });

  document.addEventListener("keydown", (event) => {
    const input = event.target;
    if (!input.matches(inputSelector) || input.getAttribute("aria-expanded") !== "true") return;
    const options = visibleOptions(input);
    let index = options.findIndex((option) => option.getAttribute("aria-selected") === "true");
    if (event.key === "Escape") return closeMenu(input);
    if (event.key === "Enter" && options[index]) {
      event.preventDefault();
      return selectMention(input, options[index]);
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    options[index]?.setAttribute("aria-selected", "false");
    index = (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[index].setAttribute("aria-selected", "true");
    options[index].scrollIntoView({ block: "nearest" });
  });

  document.addEventListener("click", (event) => {
    const editDescription = event.target.closest("[data-description-edit]");
    if (editDescription) {
      const editor = editDescription.closest("[data-description-editor]");
      editDescription.hidden = true;
      editor.querySelector("[data-description-view]").hidden = true;
      editor.querySelector("[data-description-fields]").hidden = false;
      editor.querySelector("textarea").focus();
      return;
    }
    const cancelDescription = event.target.closest("[data-description-cancel]");
    if (cancelDescription) {
      const editor = cancelDescription.closest("[data-description-editor]");
      const textarea = editor.querySelector("textarea");
      textarea.value = textarea.defaultValue;
      editor.querySelector("[data-description-fields]").hidden = true;
      editor.querySelector("[data-description-view]").hidden = false;
      const editButton = editor.querySelector("[data-description-edit]");
      editButton.hidden = false;
      editButton.focus();
      return;
    }
    const copyButton = event.target.closest("[data-copy-text]");
    if (copyButton) {
      const status = copyButton.nextElementSibling;
      navigator.clipboard.writeText(copyButton.dataset.copyText).then(
        () => { status.textContent = "Copied"; },
        () => { status.textContent = "Copy failed"; },
      );
      return;
    }
    const pickAttachments = event.target.closest("[data-attachment-pick]");
    if (pickAttachments) {
      pickAttachments.closest(attachmentsSelector).querySelector("[data-attachment-input]").click();
      return;
    }
    const deleteAttachment = event.target.closest("[data-attachment-delete]");
    if (deleteAttachment) {
      if (window.confirm("Delete this file?")) {
        postAttachments(
          deleteAttachment.closest(attachmentsSelector),
          deleteAttachment.dataset.attachmentDelete,
          new FormData(),
          "Could not delete the file. Try again.",
        );
      }
      return;
    }
    const option = event.target.closest("[data-mention-id]");
    if (option) return selectMention(option.closest("form").querySelector(inputSelector), option);
    document.querySelectorAll(inputSelector).forEach(closeMenu);
  });

  document.addEventListener("submit", (event) => {
    const input = event.target.querySelector(inputSelector);
    if (input) syncSelected(input);
  }, true);
})();

(() => {
  let draggedCard = null;

  function setDragStatus(board, message) {
    const status = board.querySelector("[data-board-drag-status]");
    status.hidden = false;
    status.textContent = message;
  }

  document.addEventListener("dragstart", (event) => {
    const card = event.target.closest(".ticket-card[draggable='true']");
    if (!card) return;
    draggedCard = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.id);
  });

  document.addEventListener("dragend", () => {
    draggedCard?.classList.remove("is-dragging");
    document.querySelectorAll("[data-drop-status].is-drag-over").forEach((lane) => lane.classList.remove("is-drag-over"));
    draggedCard = null;
  });

  document.addEventListener("dragover", (event) => {
    const lane = event.target.closest("[data-drop-status]");
    if (!lane || !draggedCard) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    document.querySelectorAll("[data-drop-status].is-drag-over").forEach((other) => other.classList.toggle("is-drag-over", other === lane));
  });

  document.addEventListener("dragleave", (event) => {
    const lane = event.target.closest("[data-drop-status]");
    if (lane && !lane.contains(event.relatedTarget)) lane.classList.remove("is-drag-over");
  });

  document.addEventListener("drop", async (event) => {
    const lane = event.target.closest("[data-drop-status]");
    const card = draggedCard;
    if (!lane || !card) return;
    event.preventDefault();
    lane.classList.remove("is-drag-over");

    const source = card.closest("[data-drop-status]");
    if (source === lane) return;
    const board = lane.closest(".project-board");
    const body = new FormData();
    body.append("status", lane.dataset.dropStatus);
    body.append("_csrf", board.dataset.boardCsrf);

    card.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(card.dataset.ticketStatusUrl, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
      });
      if (!response.ok) throw new Error();
      window.location.reload();
    } catch {
      setDragStatus(board, "Could not move ticket. Try again.");
    } finally {
      card.removeAttribute("aria-busy");
    }
  });
})();
