const CHAT_POLL_INTERVAL_MS = 5000;

document.addEventListener("DOMContentLoaded", () => {
  const chat = document.getElementById("chat");
  if (chat) initChat(chat);
});

function initChat(chat) {
  const url = chat.dataset.url;
  const list = document.getElementById("chat-messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-body");
  const errorBox = document.getElementById("chat-error");
  const sendButton = form.querySelector("button[type=submit]");
  const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]").value;

  let lastId = 0;
  // Requests run one after another, so messages always arrive in order
  let queue = Promise.resolve();

  function renderMessage(message) {
    if (message.id <= lastId) return; // already on screen

    const empty = list.querySelector(".chat__empty");
    if (empty) empty.remove();

    const item = document.createElement("div");
    item.className = message.is_me ? "chat__message chat__message--me" : "chat__message";

    // textContent (not innerHTML): messages typed by users can't inject HTML or scripts
    const meta = document.createElement("div");
    meta.className = "chat__meta";
    const name = message.is_me ? "You" : message.author;
    meta.textContent = `${name}${message.is_organizer ? " (organizer)" : ""}, ${message.time}`;

    const body = document.createElement("div");
    body.className = "chat__body";
    body.textContent = message.body;

    item.append(meta, body);
    list.appendChild(item);
    lastId = message.id;
  }

  function isNearBottom() {
    return list.scrollHeight - list.scrollTop - list.clientHeight < 60;
  }

  function scrollToBottom() {
    list.scrollTop = list.scrollHeight;
  }

  async function fetchNewMessages() {
    try {
      const response = await fetch(`${url}?after=${lastId}`, { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const data = await response.json();
      const stickToBottom = isNearBottom();
      data.messages.forEach(renderMessage);
      if (data.messages.length > 0 && stickToBottom) scrollToBottom();
    } catch (error) {
      console.error("Could not load chat messages:", error);
    }
  }

  function loadMessages() {
    queue = queue.then(fetchNewMessages);
    return queue;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = input.value.trim();
    if (!body) return;

    errorBox.textContent = "";
    sendButton.disabled = true;

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: new URLSearchParams({ body }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        errorBox.textContent = data.error || "The message could not be sent. Try again.";
        return;
      }

      input.value = "";
      await loadMessages();
      scrollToBottom();
    } catch (error) {
      errorBox.textContent = "The message could not be sent. Check your connection.";
    } finally {
      sendButton.disabled = false;
      input.focus();
    }
  });

  // Enter sends, Shift+Enter adds a new line
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  loadMessages().then(scrollToBottom);

  // Check for new messages every few seconds, only while the tab is visible
  setInterval(() => {
    if (document.visibilityState === "visible") loadMessages();
  }, CHAT_POLL_INTERVAL_MS);
}