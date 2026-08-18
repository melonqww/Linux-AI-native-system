const chatView = document.querySelector("#chat-view");
const settingsView = document.querySelector("#settings-view");
const sidebarView = document.querySelector("#sidebar-view");
const settingsButton = document.querySelector("#settings-button");
const menuButton = document.querySelector("#menu-button");
const chatTabs = document.querySelector("#chat-tabs");
const backButton = document.querySelector("#back-to-chat");
const composer = document.querySelector("#composer");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const confirmationButton = document.querySelector("#confirmation-button");
const modelButton = document.querySelector("#model-button");
const modelLabel = document.querySelector("#model-label");
const modelMenu = document.querySelector("#model-menu");
const settingsModel = document.querySelector("#settings-model");
const panelShell = document.querySelector(".panel-shell");
const panelToggle = document.querySelector("#panel-toggle");
const panelToggleIcon = panelToggle.querySelector("span");

function setPanelCollapsed(collapsed) {
  panelShell.classList.toggle("is-collapsed", collapsed);
  panelToggle.setAttribute("aria-expanded", String(!collapsed));
  panelToggle.setAttribute("aria-label", collapsed ? "Открыть чат" : "Свернуть чат");
  panelToggleIcon.textContent = collapsed ? "‹" : "›";
}

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
  const title = tab.querySelector(".chat-tab-title");
  const tooltipTitle = tab.querySelector(".chat-tab-tooltip-title");
  const name = tab.dataset.chatName || "Рабочая область";
  if (title) title.innerHTML = `<span class="folder-icon" aria-hidden="true"></span>${name}`;
  if (tooltipTitle) tooltipTitle.textContent = name;
  selectView(chatView, tab);
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
if (backButton) {
  backButton.addEventListener("click", () => {
    const selected = chatTabs.querySelector(".chat-tab.active-tab") || chatTabs.querySelector(".chat-tab");
    if (selected) selectChat(selected);
  });
}
panelToggle.addEventListener("click", () => {
  setPanelCollapsed(!panelShell.classList.contains("is-collapsed"));
});

chatTabs.addEventListener("click", (event) => {
  const tab = event.target.closest(".chat-tab");
  if (!tab) return;
  selectChat(tab);
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
  if (settingsModel) settingsModel.textContent = option.dataset.model;
  modelButton.setAttribute("aria-expanded", "false");
  modelMenu.hidden = true;
});
