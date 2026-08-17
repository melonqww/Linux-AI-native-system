const chatView = document.querySelector("#chat-view");
const settingsView = document.querySelector("#settings-view");
const settingsButton = document.querySelector("#settings-button");
const backButton = document.querySelector("#back-to-chat");
const menuButton = document.querySelector("#menu-button");
const chatMenu = document.querySelector("#chat-menu");
const composer = document.querySelector("#composer");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const generationState = document.querySelector("#generation-state");
const directoryButton = document.querySelector("#directory-button");
const fileInput = document.querySelector("#file-input");

function showChat() {
  settingsView.classList.remove("active");
  chatView.classList.add("active");
}

function showSettings() {
  chatView.classList.remove("active");
  settingsView.classList.add("active");
}

function addAssistantMessage(text) {
  const message = document.createElement("p");
  message.className = "assistant-message";
  message.textContent = text;
  messages.append(message);
  messages.scrollTop = messages.scrollHeight;
}

function simulateResponse() {
  generationState.hidden = false;
  window.setTimeout(() => {
    generationState.hidden = true;
    addAssistantMessage("Готово. В рабочей версии здесь появится проверенный результат инструмента и запись в audit log.");
  }, 1100);
}

settingsButton.addEventListener("click", showSettings);
backButton.addEventListener("click", showChat);

menuButton.addEventListener("click", () => {
  chatMenu.hidden = !chatMenu.hidden;
});

document.querySelector("#new-chat").addEventListener("click", () => {
  document.querySelector("#chat-title").textContent = "Новый чат";
  chatMenu.hidden = true;
  showChat();
});

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  const message = document.createElement("div");
  message.className = "user-message";
  message.textContent = text;
  messages.append(message);
  input.value = "";
  messages.scrollTop = messages.scrollHeight;
  simulateResponse();
});

document.querySelector("#run-demo").addEventListener("click", simulateResponse);

directoryButton.addEventListener("click", () => {
  directoryButton.textContent = "~/Projects/Linux AI System/services/agent-runtime";
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) {
    addAssistantMessage(`Файл «${fileInput.files[0].name}» прикреплён к текущей задаче. Перед отправкой в модель его контекст будет показан отдельно.`);
  }
});
