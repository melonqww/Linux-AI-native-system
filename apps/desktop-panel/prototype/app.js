const chatView = document.querySelector("#chat-view");
const settingsView = document.querySelector("#settings-view");
const settingsButton = document.querySelector("#settings-button");
const backButton = document.querySelector("#back-to-chat");
const menuButton = document.querySelector("#menu-button");
const chatMenu = document.querySelector("#chat-menu");
const composer = document.querySelector("#composer");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const directoryButton = document.querySelector("#directory-button");
const confirmationButton = document.querySelector("#confirmation-button");
const modelSelect = document.querySelector("#model-select");
const settingsModel = document.querySelector("#settings-model");

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
  window.setTimeout(() => {
    addAssistantMessage("Готово. В рабочей версии здесь появится проверенный результат инструмента и запись в audit log.");
  }, 1100);
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 142)}px`;
}

settingsButton.addEventListener("click", showSettings);
backButton.addEventListener("click", showChat);
menuButton.addEventListener("click", () => { chatMenu.hidden = !chatMenu.hidden; });
document.querySelector("#new-chat").addEventListener("click", () => { chatMenu.hidden = true; showChat(); });

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  const message = document.createElement("div");
  message.className = "user-message";
  message.textContent = text;
  messages.append(message);
  input.value = "";
  resizeInput();
  messages.scrollTop = messages.scrollHeight;
  simulateResponse();
});

input.addEventListener("input", resizeInput);
document.querySelector("#run-demo").addEventListener("click", simulateResponse);
confirmationButton.addEventListener("click", () => {
  const enabled = confirmationButton.getAttribute("aria-pressed") === "true";
  confirmationButton.setAttribute("aria-pressed", String(!enabled));
  confirmationButton.innerHTML = !enabled ? "<span>●</span> Подтверждать за меня" : "<span>◌</span> Подтверждать за меня";
});
modelSelect.addEventListener("change", () => { settingsModel.textContent = modelSelect.value; });
directoryButton.addEventListener("click", () => { directoryButton.innerHTML = "<span class=\"folder-icon\" aria-hidden=\"true\"></span> ~/Projects/Linux AI System/services/agent-runtime"; });
