const chatView = document.querySelector("#chat-view");
const settingsView = document.querySelector("#settings-view");
const softwareView = document.querySelector("#software-view");
const sidebarView = document.querySelector("#sidebar-view");
const settingsButton = document.querySelector("#settings-button");
const menuButton = document.querySelector("#menu-button");
const chatTabs = document.querySelector("#chat-tabs");
const composer = document.querySelector("#composer");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const confirmationButton = document.querySelector("#confirmation-button");
const panelShell = document.querySelector(".panel-shell");
const panelToggle = document.querySelector("#panel-toggle");
const panelToggleIcon = panelToggle.querySelector("span");

const catalogList = document.querySelector("#catalog-list");
const installedList = document.querySelector("#installed-list");
const backupList = document.querySelector("#backup-list");
const backupCount = document.querySelector("#backup-count");
const catalogSearch = document.querySelector("#catalog-search");
const networkBanner = document.querySelector("#network-banner");
const dialogBackdrop = document.querySelector("#software-dialog-backdrop");
const dialogTitle = document.querySelector("#software-dialog-title");
const dialogMessage = document.querySelector("#software-dialog-message");
const dialogIcon = document.querySelector("#dialog-app-icon");
const dialogOptions = document.querySelector("#software-dialog-options");
const dialogConfirm = document.querySelector("#dialog-confirm");
let pendingDialogAction = null;
const prototypeParams = new URLSearchParams(window.location.search);
let networkOnline = !prototypeParams.has("offline");

const applications = [
  ["steam", "Steam", "Игровой магазин и библиотека", "steam", "#171a21", "https://store.steampowered.com/about/"],
  ["discord", "Discord", "Голосовое и текстовое общение", "discord", "#5865f2", "https://discord.com/"],
  ["spotify", "Spotify", "Музыка, подкасты и плейлисты", "spotify", "#1db954", "https://www.spotify.com/download/linux/"],
  ["telegram-desktop", "Telegram", "Быстрый и безопасный мессенджер", "telegram", "#229ed9", "https://telegram.org/"],
  ["vlc", "VLC", "Свободный медиаплеер", "vlcmediaplayer", "#e85d04", "https://www.videolan.org/vlc/"],
  ["code", "Visual Studio Code", "Редактор кода от Microsoft", "visualstudiocode", "#007acc", "https://code.visualstudio.com/"],
  ["chromium", "Chromium", "Открытый веб-браузер", "googlechrome", "#4285f4", "https://www.chromium.org/"],
  ["firefox", "Firefox", "Приватный веб-браузер Mozilla", "firefoxbrowser", "#ff7139", "https://www.mozilla.org/firefox/"],
  ["obs-studio", "OBS Studio", "Запись экрана и трансляции", "obsstudio", "#302e31", "https://obsproject.com/"],
  ["blender", "Blender", "Создание и рендеринг 3D-графики", "blender", "#e87d0d", "https://www.blender.org/"],
  ["inkscape", "Inkscape", "Редактор векторной графики", "inkscape", "#333333", "https://inkscape.org/"],
  ["gimp", "GIMP", "Редактор растровых изображений", "gimp", "#5c5543", "https://www.gimp.org/"],
  ["slack", "Slack", "Рабочие пространства и команды", "slack", "#4a154b", "https://slack.com/"],
  ["zoom-client", "Zoom", "Видеовстречи и конференции", "zoom", "#2d8cff", "https://zoom.us/download?os=linux"],
  ["postman", "Postman", "Разработка и проверка API", "postman", "#ff6c37", "https://www.postman.com/downloads/"],
  ["pycharm-community", "PyCharm Community", "Python IDE от JetBrains", "pycharm", "#21d789", "https://www.jetbrains.com/pycharm/"],
  ["intellij-idea-community", "IntelliJ IDEA Community", "JVM IDE от JetBrains", "intellijidea", "#fe315d", "https://www.jetbrains.com/idea/"],
  ["libreoffice", "LibreOffice", "Свободный офисный пакет", "libreoffice", "#18a303", "https://www.libreoffice.org/"],
  ["thunderbird", "Thunderbird", "Почта и календарь", "thunderbird", "#0a84ff", "https://www.thunderbird.net/"],
  ["bitwarden", "Bitwarden", "Менеджер паролей", "bitwarden", "#175ddc", "https://bitwarden.com/download/"],
].map(([id, name, description, icon, color, officialUrl], index) => ({
  id, name, description, icon, color, officialUrl,
  installed: ["discord", "vlc", "code"].includes(id),
  running: id === "discord",
  starting: false,
  downloading: false,
  pausing: false,
  pausedByUser: false,
  waitingForNetwork: false,
  cancelingInstall: false,
  restoring: false,
  restoreBackup: null,
  removing: false,
  progress: 0,
  downloadTotalBytes: (180 + index * 37) * 1024 * 1024,
  downloadedBytes: 0,
  downloadSpeedBps: null,
  etaSeconds: null,
  lastProgressAt: null,
  lastDownloadedBytes: 0,
  installPreferences: {pinToGnome: false, launchAfterInstall: false},
}));

let backups = [{
  id: "steam",
  created: "Сегодня, 12:40",
  remaining: "Осталось 30 дней",
  size: "1,8 ГБ",
}];

if (prototypeParams.has("native"))
  document.body.classList.add("native-shell");

function setPanelCollapsed(collapsed) {
  panelShell.classList.toggle("is-collapsed", collapsed);
  panelToggle.setAttribute("aria-expanded", String(!collapsed));
  panelToggle.setAttribute("aria-label", collapsed ? "Открыть чат" : "Свернуть чат");
  panelToggleIcon.textContent = collapsed ? "‹" : "›";
}

function allViews() {
  return [chatView, settingsView, softwareView, sidebarView];
}

function clearActiveTabs() {
  document.querySelectorAll(".tab-control").forEach((tab) => tab.classList.remove("active-tab"));
}

function selectView(view, activeTab) {
  if (!view.classList.contains("active")) {
    allViews().forEach((item) => item.classList.remove("active"));
    view.classList.add("active");
  }
  clearActiveTabs();
  activeTab.classList.add("active-tab");
}

function selectChat(tab) {
  const title = tab.querySelector(".chat-tab-title");
  const name = tab.dataset.chatName || "Рабочая область";
  if (title) title.innerHTML = `<span class="folder-icon" aria-hidden="true"></span>${name}`;
  selectView(chatView, tab);
}

function openSoftware(mainTab = "catalog", libraryTab = "installed") {
  selectView(softwareView, settingsButton);
  document.querySelectorAll(".software-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.softwareTab === mainTab);
  });
  document.querySelector("#software-catalog-section").classList.toggle("active", mainTab === "catalog");
  document.querySelector("#software-library-section").classList.toggle("active", mainTab === "library");
  if (mainTab === "library") selectLibraryTab(libraryTab);
  renderSoftware();
}

function selectLibraryTab(tab) {
  document.querySelectorAll(".library-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.libraryTab === tab);
  });
  installedList.classList.toggle("hidden", tab !== "installed");
  backupList.classList.toggle("hidden", tab !== "backups");
}

function createIcon(app, extraClass = "") {
  const icon = document.createElement("div");
  icon.className = `app-icon ${extraClass}`.trim();
  icon.style.backgroundColor = app.color;
  const image = document.createElement("img");
  image.src = `https://cdn.simpleicons.org/${app.icon}/ffffff`;
  image.alt = "";
  image.addEventListener("error", () => {
    image.remove();
    icon.textContent = app.name.slice(0, 2).toUpperCase();
  }, {once: true});
  icon.append(image);
  return icon;
}

function appInfo(app, meta = "") {
  const info = document.createElement("div");
  info.className = "app-info";
  const nameLine = document.createElement("div");
  nameLine.className = "app-name-line";
  const name = document.createElement("span");
  name.className = "app-name";
  name.textContent = app.name;
  const official = document.createElement("a");
  official.className = "official-link";
  official.href = app.officialUrl;
  official.target = "_blank";
  official.rel = "noreferrer";
  official.title = `Официальный сайт ${app.name}`;
  official.setAttribute("aria-label", `Официальный сайт ${app.name}`);
  official.textContent = "Официальный сайт ↗";
  nameLine.append(name, official);
  const description = document.createElement("p");
  description.className = "app-description";
  description.textContent = app.description;
  info.append(nameLine, description);
  if (meta) {
    const metaLabel = document.createElement("div");
    metaLabel.className = "app-meta";
    metaLabel.textContent = meta;
    info.append(metaLabel);
  }
  return info;
}

function actionButton(label, className, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `app-action ${className}`.trim();
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function formatSpeed(bytesPerSecond) {
  if (!bytesPerSecond) return "";
  const megabytes = bytesPerSecond / (1024 * 1024);
  return `${megabytes >= 10 ? megabytes.toFixed(0) : megabytes.toFixed(1)} МБ/с`;
}

function formatEta(seconds) {
  if (seconds == null) return "Время уточняется";
  if (seconds < 60) return `Осталось ~${Math.max(1, seconds)} сек`;
  const minutes = Math.ceil(seconds / 60);
  return `Осталось ~${minutes} мин`;
}

function downloadStatus(app, prefix) {
  const details = [
    `${prefix} · ${app.progress}%`,
    formatSpeed(app.downloadSpeedBps),
    formatEta(app.etaSeconds),
  ].filter(Boolean);
  return details.join(" · ");
}

function catalogCard(app, animate = false) {
  const card = document.createElement("article");
  card.className = "software-card";
  card.dataset.appId = app.id;
  card.classList.toggle("animate-entry", animate);
  let catalogState = "Snap Store";
  if (app.removing)
    catalogState = "Удаление приложения…";
  else if (app.cancelingInstall)
    catalogState = `Отмена загрузки · ${app.progress}%`;
  else if (app.pausing)
    catalogState = `Останавливается · ${app.progress}%`;
  else if (app.waitingForNetwork)
    catalogState = `Ожидание сети · ${app.progress}% · Продолжим автоматически`;
  else if (app.pausedByUser)
    catalogState = `На паузе · ${app.progress}% · Интернет не используется`;
  else if (app.restoring && app.downloading)
    catalogState = downloadStatus(app, "Установка из бэкапа");
  else if (app.restoring && app.progress > 0)
    catalogState = `Установка из бэкапа приостановлена · ${app.progress}%`;
  else if (app.downloading)
    catalogState = downloadStatus(app, "Загрузка");
  const info = appInfo(app, catalogState);
  const actions = document.createElement("div");
  actions.className = "app-actions";
  if (app.removing) {
    const removing = document.createElement("span");
    removing.className = "operation-label";
    removing.textContent = "Удаление…";
    actions.append(removing);
  } else if (app.cancelingInstall || app.pausing) {
    const stopping = document.createElement("span");
    stopping.className = "operation-label";
    stopping.textContent = app.cancelingInstall ? "Отменяется…" : "Останавливается…";
    actions.append(stopping);
  } else if (app.downloading) {
    actions.append(
      actionButton("Пауза", "secondary", () => pauseInstall(app)),
      actionButton("Отменить", "danger", () => confirmCancelInstall(app)),
    );
  } else if (app.waitingForNetwork) {
    actions.append(
      actionButton("Пауза", "secondary", () => pauseInstall(app)),
      actionButton("Отменить", "danger", () => confirmCancelInstall(app)),
    );
  } else if (app.pausedByUser) {
    actions.append(
      actionButton("Продолжить", "", () => resumeInstall(app)),
      actionButton("Отменить", "danger", () => confirmCancelInstall(app)),
    );
  } else if (app.installed) {
    actions.append(actionButton("Удалить", "danger", () => confirmRemoval(app)));
  } else {
    actions.append(actionButton(
      app.restoring && app.progress > 0 ? "Возобновить восстановление" : app.progress > 0 ? "Возобновить" : "Установить",
      "",
      () => app.progress > 0 ? startInstall(app) : confirmInstallation(app),
    ));
  }
  if (app.progress > 0 && !app.installed && !app.removing) {
    const progress = document.createElement("div");
    progress.className = "download-progress";
    progress.innerHTML = `<span style="width:${app.progress}%"></span>`;
    info.append(progress);
  }
  card.append(createIcon(app), info, actions);
  return card;
}

function libraryCard(app, animate = false) {
  const card = document.createElement("article");
  card.className = "software-card";
  card.dataset.appId = app.id;
  card.classList.toggle("animate-entry", animate);
  const state = app.running ? "Запущено" : app.starting ? "Запускается…" : "Установлено";
  const actions = document.createElement("div");
  actions.className = "app-actions";
  if (app.removing) {
    const removing = document.createElement("span");
    removing.className = "operation-label";
    removing.textContent = "Удаление…";
    actions.append(removing);
  } else if (app.running) {
    const running = document.createElement("span");
    running.className = "running-label";
    running.textContent = "Запущено";
    actions.append(running, actionButton("Закрыть", "secondary", () => {
      app.running = false;
      renderSoftware();
    }));
  } else if (app.starting) {
    const starting = document.createElement("span");
    starting.className = "running-label";
    starting.textContent = "Запускается…";
    actions.append(starting);
  } else {
    actions.append(actionButton("Запустить", "", () => launchApp(app)));
  }
  if (!app.removing)
    actions.append(actionButton("Удалить", "danger", () => confirmRemoval(app)));
  card.append(createIcon(app), appInfo(app, state), actions);
  return card;
}

function backupCard(backup, animate = false) {
  const app = applications.find((item) => item.id === backup.id);
  const card = document.createElement("article");
  card.className = "software-card";
  card.dataset.appId = backup.id;
  card.classList.toggle("animate-entry", animate);
  const actions = document.createElement("div");
  actions.className = "app-actions";
  actions.append(actionButton("Восстановить", "", () => confirmRestore(app)));
  card.append(
    createIcon(app),
    appInfo(app, `${backup.remaining} · ${backup.size}`),
    actions,
  );
  return card;
}

function emptyState(kind) {
  const empty = document.createElement("div");
  empty.className = "empty-library";
  const isBackup = kind === "backups";
  empty.innerHTML = `
    <div class="empty-library-icon" aria-hidden="true">${isBackup ? "↶" : "▦"}</div>
    <h2>${isBackup ? "Бэкапов пока нет" : "Библиотека пуста"}</h2>
    <p>${isBackup ? "После удаления с сохранением данных бэкап появится здесь." : "Загляните в каталог — там наверняка есть то, что вам нужно."}</p>
  `;
  if (!isBackup)
    empty.append(actionButton("Перейти в каталог", "", () => openSoftware("catalog")));
  return empty;
}

function renderSoftware() {
  networkBanner.classList.toggle("hidden", networkOnline);
  const query = catalogSearch.value.trim().toLocaleLowerCase("ru");
  const filtered = applications.filter((app) =>
    `${app.name} ${app.description}`.toLocaleLowerCase("ru").includes(query));
  const previousCatalog = new Set(
    [...catalogList.querySelectorAll("[data-app-id]")].map((node) => node.dataset.appId),
  );
  const previousInstalled = new Set(
    [...installedList.querySelectorAll("[data-app-id]")].map((node) => node.dataset.appId),
  );
  const previousBackups = new Set(
    [...backupList.querySelectorAll("[data-app-id]")].map((node) => node.dataset.appId),
  );
  catalogList.replaceChildren(
    ...filtered.map((app) => catalogCard(app, !previousCatalog.has(app.id))),
  );

  const installed = applications.filter((app) => app.installed);
  installedList.replaceChildren(...(installed.length
    ? installed.map((app) => libraryCard(app, !previousInstalled.has(app.id)))
    : [emptyState("installed")]));
  backupList.replaceChildren(...(backups.length
    ? backups.map((backup) => backupCard(backup, !previousBackups.has(backup.id)))
    : [emptyState("backups")]));
  backupCount.textContent = backups.length ? `(${backups.length})` : "";
}

function startInstall(app) {
  if (app.downloading) return;
  if (!networkOnline) {
    app.waitingForNetwork = true;
    app.pausedByUser = false;
    app.pausing = false;
    app.downloadSpeedBps = null;
    app.etaSeconds = null;
    renderSoftware();
    return;
  }
  app.downloading = true;
  app.waitingForNetwork = false;
  app.pausedByUser = false;
  app.pausing = false;
  app.progress = Math.max(8, app.progress);
  app.downloadedBytes = Math.round(app.downloadTotalBytes * app.progress / 100);
  app.lastDownloadedBytes = app.downloadedBytes;
  app.lastProgressAt = performance.now();
  renderSoftware();
  const timer = window.setInterval(() => {
    if (!app.downloading) {
      window.clearInterval(timer);
      return;
    }
    if (!networkOnline) {
      window.clearInterval(timer);
      app.downloading = false;
      app.waitingForNetwork = true;
      app.downloadSpeedBps = null;
      app.etaSeconds = null;
      renderSoftware();
      return;
    }
    const now = performance.now();
    app.progress = Math.min(100, app.progress + 13);
    app.downloadedBytes = Math.round(app.downloadTotalBytes * app.progress / 100);
    const elapsedSeconds = Math.max(.001, (now - app.lastProgressAt) / 1000);
    const instantSpeed = Math.max(
      1,
      Math.round((app.downloadedBytes - app.lastDownloadedBytes) / elapsedSeconds),
    );
    app.downloadSpeedBps = app.downloadSpeedBps == null
      ? instantSpeed
      : Math.round((app.downloadSpeedBps * 2 + instantSpeed) / 3);
    app.etaSeconds = Math.ceil(
      Math.max(0, app.downloadTotalBytes - app.downloadedBytes) / app.downloadSpeedBps,
    );
    app.lastProgressAt = now;
    app.lastDownloadedBytes = app.downloadedBytes;
    if (app.progress >= 100) {
      window.clearInterval(timer);
      app.downloading = false;
      app.installed = true;
      app.restoring = false;
      app.restoreBackup = null;
      app.downloadSpeedBps = null;
      app.etaSeconds = 0;
      backups = backups.filter((backup) => backup.id !== app.id);
      if (app.installPreferences.launchAfterInstall)
        launchApp(app);
    }
    renderSoftware();
  }, 260);
}

function pauseInstall(app) {
  if (app.waitingForNetwork) {
    app.waitingForNetwork = false;
    app.pausedByUser = true;
    app.downloadSpeedBps = null;
    app.etaSeconds = null;
    renderSoftware();
    return;
  }
  if (!app.downloading || app.pausing) return;
  app.pausing = true;
  app.downloading = false;
  app.downloadSpeedBps = null;
  app.etaSeconds = null;
  renderSoftware();
  window.setTimeout(() => {
    app.pausing = false;
    app.pausedByUser = true;
    renderSoftware();
  }, 420);
}

function resumeInstall(app) {
  if (!app.pausedByUser) return;
  app.pausedByUser = false;
  startInstall(app);
}

function confirmCancelInstall(app) {
  openDialog(app, {
    title: `Отменить установку ${app.name}?`,
    message: "Загрузка остановится, а задача установки будет удалена.",
    confirmLabel: "Да, отменить",
    action: () => {
      app.downloading = false;
      app.pausing = false;
      app.pausedByUser = false;
      app.waitingForNetwork = false;
      app.cancelingInstall = true;
      renderSoftware();
      window.setTimeout(() => {
        if (app.restoring && app.restoreBackup && !backups.some((item) => item.id === app.id))
          backups.unshift(app.restoreBackup);
        app.cancelingInstall = false;
        app.restoring = false;
        app.restoreBackup = null;
        app.progress = 0;
        app.downloadedBytes = 0;
        app.downloadSpeedBps = null;
        app.etaSeconds = null;
        renderSoftware();
      }, 520);
    },
  });
}

function setNetworkOnline(online) {
  networkOnline = Boolean(online);
  if (!networkOnline) {
    applications.filter((app) => app.downloading).forEach((app) => {
      app.downloading = false;
      app.waitingForNetwork = true;
      app.downloadSpeedBps = null;
      app.etaSeconds = null;
    });
  } else {
    applications.filter((app) => app.waitingForNetwork && !app.pausedByUser)
      .forEach((app) => startInstall(app));
  }
  renderSoftware();
}

window.softwarePrototype = Object.freeze({setNetworkOnline});

function launchApp(app) {
  app.starting = true;
  renderSoftware();
  window.setTimeout(() => {
    if (!app.installed) return;
    app.starting = false;
    app.running = true;
    renderSoftware();
  }, 850);
}

function optionRow(label, value) {
  const row = document.createElement("div");
  row.className = "dialog-option-row";
  const labelNode = document.createElement("span");
  labelNode.textContent = label;
  const valueNode = document.createElement("strong");
  valueNode.textContent = value;
  row.append(labelNode, valueNode);
  return row;
}

function checkboxOption(id, label, checked = false) {
  const wrapper = document.createElement("label");
  wrapper.className = "dialog-check";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.id = id;
  checkbox.checked = checked;
  const text = document.createElement("span");
  text.textContent = label;
  wrapper.append(checkbox, text);
  return wrapper;
}

function openDialog(app, {
  title,
  message,
  confirmLabel,
  confirmClass = "danger-action",
  options = [],
  action,
}) {
  dialogTitle.textContent = title;
  dialogMessage.textContent = message;
  dialogIcon.replaceChildren(createIcon(app));
  dialogConfirm.textContent = confirmLabel;
  dialogConfirm.className = confirmClass;
  dialogOptions.replaceChildren(...options);
  dialogOptions.classList.toggle("hidden", options.length === 0);
  pendingDialogAction = action;
  dialogBackdrop.classList.remove("hidden");
}

function closeDialog() {
  pendingDialogAction = null;
  dialogBackdrop.classList.add("hidden");
  dialogOptions.replaceChildren();
  dialogOptions.classList.add("hidden");
}

function confirmInstallation(app) {
  const pin = checkboxOption(
    `pin-${app.id}`,
    "Закрепить в панели GNOME",
    app.installPreferences.pinToGnome,
  );
  const launch = checkboxOption(
    `launch-${app.id}`,
    "Запустить после установки",
    app.installPreferences.launchAfterInstall,
  );
  openDialog(app, {
    title: `Установить ${app.name}?`,
    message: "Проверьте источник и параметры установки.",
    confirmLabel: "Установить",
    confirmClass: "primary-action",
    options: [
      optionRow("Источник", "Snap Store"),
      optionRow("Канал", "stable"),
      optionRow("Язык", "Язык системы"),
      optionRow("Расположение", "Стандартное"),
      pin,
      launch,
    ],
    action: () => {
      app.installPreferences = {
        pinToGnome: pin.querySelector("input").checked,
        launchAfterInstall: launch.querySelector("input").checked,
      };
      startInstall(app);
    },
  });
}

function confirmRemoval(app) {
  const backup = checkboxOption(
    `backup-${app.id}`,
    "Создать бэкап перед удалением",
    true,
  );
  const warning = document.createElement("div");
  warning.className = "dialog-option-warning hidden";
  warning.textContent = "Без бэкапа данные приложения нельзя будет восстановить.";
  backup.querySelector("input").addEventListener("change", (event) => {
    warning.classList.toggle("hidden", event.target.checked);
  });
  openDialog(app, {
    title: `Удалить ${app.name}?`,
    message: "Приложение будет удалено после вашего подтверждения.",
    confirmLabel: "Да, удалить",
    options: [backup, optionRow("Срок хранения", "31 день"), warning],
    action: () => {
      const createBackup = backup.querySelector("input").checked;
      app.removing = true;
      app.running = false;
      app.starting = false;
      app.downloading = false;
      app.pausing = false;
      app.pausedByUser = false;
      app.waitingForNetwork = false;
      app.cancelingInstall = false;
      app.restoring = false;
      app.restoreBackup = null;
      renderSoftware();
      window.setTimeout(() => {
        app.installed = false;
        app.removing = false;
        app.progress = 0;
        app.downloadedBytes = 0;
        app.downloadSpeedBps = null;
        app.etaSeconds = null;
        if (createBackup) {
          const existing = backups.find((item) => item.id === app.id);
          if (!existing)
            backups.unshift({id: app.id, created: "Только что", remaining: "Осталось 31 день", size: "Расчёт…"});
        } else {
          backups = backups.filter((item) => item.id !== app.id);
        }
        renderSoftware();
      }, 750);
    },
  });
}

function confirmRestore(app) {
  openDialog(app, {
    title: `Восстановить ${app.name}?`,
    message: "Приложение будет установлено заново, после чего snapd восстановит сохранённые данные.",
    confirmLabel: "Восстановить",
    confirmClass: "primary-action",
    action: () => {
      app.restoreBackup = backups.find((backup) => backup.id === app.id) || null;
      backups = backups.filter((backup) => backup.id !== app.id);
      app.installed = false;
      app.running = false;
      app.starting = false;
      app.downloading = false;
      app.pausing = false;
      app.pausedByUser = false;
      app.waitingForNetwork = false;
      app.cancelingInstall = false;
      app.restoring = true;
      app.progress = 0;
      openSoftware("catalog");
      startInstall(app);
    },
  });
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
document.querySelector("#library-quick-button").addEventListener("click", () => openSoftware("library", "installed"));
document.querySelector("#open-catalog-button").addEventListener("click", () => openSoftware("catalog"));
document.querySelector("#software-back-button").addEventListener("click", () => selectView(settingsView, settingsButton));
document.querySelectorAll(".software-tab").forEach((button) => {
  button.addEventListener("click", () => openSoftware(button.dataset.softwareTab));
});
document.querySelectorAll(".library-tab").forEach((button) => {
  button.addEventListener("click", () => selectLibraryTab(button.dataset.libraryTab));
});
catalogSearch.addEventListener("input", renderSoftware);
document.querySelector("#dialog-cancel").addEventListener("click", closeDialog);
dialogConfirm.addEventListener("click", () => {
  const action = pendingDialogAction;
  closeDialog();
  if (action) action();
});

panelToggle.addEventListener("click", () => setPanelCollapsed(!panelShell.classList.contains("is-collapsed")));
chatTabs.addEventListener("click", (event) => {
  const tab = event.target.closest(".chat-tab");
  if (tab) selectChat(tab);
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
document.querySelectorAll("[data-dependency-notice]").forEach((notice) => {
  const hide = notice.querySelector("[data-notice-hide]");
  const neverShow = notice.querySelector("[data-notice-never]");
  const install = notice.querySelector("[data-notice-install]");
  const dismiss = () => { notice.hidden = true; };
  hide.addEventListener("click", dismiss);
  neverShow.addEventListener("change", () => { if (neverShow.checked) dismiss(); });
  install.addEventListener("click", () => {
    install.disabled = true;
    install.textContent = "Подготовлено";
    dismiss();
    addAssistantMessage("Установка будет подключена после интеграции backend.");
  });
});

renderSoftware();
