from __future__ import annotations

from .contracts import Application


def _application(
    package_name: str,
    display_name: str,
    category: str,
    description: str,
) -> Application:
    return Application(
        application_id=package_name,
        display_name=display_name,
        package_name=package_name,
        category=category,
        description=description,
        store_url=f"https://snapcraft.io/{package_name}",
    )


POPULAR_APPLICATIONS: tuple[Application, ...] = (
    _application("steam", "Steam", "games", "Игровой магазин и библиотека Steam."),
    _application("discord", "Discord", "communication", "Голосовое и текстовое общение."),
    _application("spotify", "Spotify", "media", "Клиент музыкального сервиса Spotify."),
    _application("telegram-desktop", "Telegram", "communication", "Настольный клиент Telegram."),
    _application("vlc", "VLC", "media", "Медиаплеер VLC."),
    _application("code", "Visual Studio Code", "development", "Редактор кода Visual Studio Code."),
    _application("chromium", "Chromium", "internet", "Веб-браузер Chromium."),
    _application("firefox", "Firefox", "internet", "Веб-браузер Mozilla Firefox."),
    _application("obs-studio", "OBS Studio", "media", "Запись экрана и трансляции."),
    _application("blender", "Blender", "graphics", "Создание и рендеринг 3D-графики."),
    _application("inkscape", "Inkscape", "graphics", "Редактор векторной графики."),
    _application("gimp", "GIMP", "graphics", "Редактор растровых изображений."),
    _application("slack", "Slack", "communication", "Клиент рабочих пространств Slack."),
    _application("zoom-client", "Zoom", "communication", "Клиент видеоконференций Zoom."),
    _application("postman", "Postman", "development", "Инструмент разработки и проверки API."),
    _application("pycharm-community", "PyCharm Community", "development", "Python IDE от JetBrains."),
    _application(
        "intellij-idea-community",
        "IntelliJ IDEA Community",
        "development",
        "JVM IDE от JetBrains.",
    ),
    _application("libreoffice", "LibreOffice", "productivity", "Офисный пакет LibreOffice."),
    _application("thunderbird", "Thunderbird", "communication", "Почтовый клиент Thunderbird."),
    _application("bitwarden", "Bitwarden", "security", "Настольный клиент менеджера паролей Bitwarden."),
)

_BY_ID = {application.application_id: application for application in POPULAR_APPLICATIONS}


def list_applications() -> tuple[Application, ...]:
    return POPULAR_APPLICATIONS


def get_application(application_id: str) -> Application:
    try:
        return _BY_ID[application_id]
    except KeyError as error:
        raise KeyError(f"unknown_application:{application_id}") from error
