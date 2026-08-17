const chatView = document.querySelector("#chat-view");
const settingsView = document.querySelector("#settings-view");
const sidebarView = document.querySelector("#sidebar-view");
const settingsButton = document.querySelector("#settings-button");
const menuButton = document.querySelector("#menu-button");
const createChatButton = document.querySelector("#create-chat-button");
const chatTabs = document.querySelector("#chat-tabs");
const backButton = document.querySelector("#back-to-chat");
const composer = document.querySelector("#composer");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const directoryButton = document.querySelector("#directory-button");
const confirmationButton = document.querySelector("#confirmation-button");
const modelButton = document.querySelector("#model-button");
const modelLabel = document.querySelector("#model-label");
const modelMenu = document.querySelector("#model-menu");
const settingsModel = document.querySelector("#settings-model");
const chatTitle = document.querySelector("#chat-title");
const toast = document.querySelector("#toast");
let nextChatId = 2;
let toastTimer;

function allViews() {
  return [chatView, settingsView, sidebarView];
}

function clearActiveTabs() {
  document.querySelectorAll(".tab-control").forEach((tab) => tab.classList.remove("active-tab"));
}

function selectView(view, activeTab) {
  allViews().forEach((item) => item.classList.remove("active"));
  view.classList.add("active");
  clearActiveTabs();
  activeTab.classList.add("active-tab");
}

function selectChat(tab) {
  chatTitle.textContent = tab.dataset.chatName || tab.childNodes[0].textContent.trim();
  selectView(chatView, tab);
}

function createChat() {
  if (chatTabs.querySelector(".chat-tab")) {
    showTransientNotice("Нельзя открыть больше одного нового чата.");
    return;
  }
  const id = String(nextChatId++);
  const tab = document.createElement("button");
  tab.className = "tab-control chat-tab";
  tab.dataset.chatId = id;
  tab.dataset.chatName = `Новый чат ${id}`;
  tab.setAttribute("aria-label", `Открыть чат Новый чат ${id}`);
  tab.innerHTML = `<span class="tab-label">Новый чат ${id}</span> <i aria-label="Закрыть чат">×</i>`;
  chatTabs.append(tab);
  selectChat(tab);
}

function showTransientNotice(text) {
  toast.textContent = text;
  toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 2600);
}

function closeChat(tab) {
  const wasActive = tab.classList.contains("active-tab");
  tab.remove();
  if (!wasActive) return;
  const fallback = chatTabs.querySelector(".chat-tab");
  if (fallback) selectChat(fallback);
  else selectView(sidebarView, menuButton);
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

menuButton.addEventListener("click", () => selectView(sidebarView, menuButton));
settingsButton.addEventListener("click", () => selectView(settingsView, settingsButton));
backButton.addEventListener("click", () => {
  const selected = chatTabs.querySelector(".chat-tab.active-tab") || chatTabs.querySelector(".chat-tab");
  if (selected) selectChat(selected);
});
createChatButton.addEventListener("click", createChat);

chatTabs.addEventListener("click", (event) => {
  const tab = event.target.closest(".chat-tab");
  if (!tab) return;
  if (event.target.closest("i")) closeChat(tab);
  else selectChat(tab);
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
  resizeInput();
  messages.scrollTop = messages.scrollHeight;
  simulateResponse();
});

input.addEventListener("input", resizeInput);
document.querySelector("#run-demo").addEventListener("click", simulateResponse);
confirmationButton.addEventListener("click", () => {
  const enabled = confirmationButton.getAttribute("aria-pressed") === "true";
  confirmationButton.setAttribute("aria-pressed", String(!enabled));
});
modelButton.addEventListener("click", () => {
  const isOpen = modelButton.getAttribute("aria-expanded") === "true";
  modelButton.setAttribute("aria-expanded", String(!isOpen));
  modelMenu.hidden = isOpen;
});
modelMenu.addEventListener("click", (event) => {
  const option = event.target.closest("[data-model]");
  if (!option) return;
  modelLabel.textContent = option.dataset.model;
  settingsModel.textContent = option.dataset.model;
  modelButton.setAttribute("aria-expanded", "false");
  modelMenu.hidden = true;
});
directoryButton.addEventListener("click", () => { directoryButton.innerHTML = "<span class=\"folder-icon\" aria-hidden=\"true\"></span> ~/Projects/Linux AI System/services/agent-runtime"; });
