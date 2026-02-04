# ~/.config/Ax-Shell/services/audio.py

from fabric.audio.service import Audio as FabricAudio, AudioStream as FabricAudioStream
from fabric.core.service import Property


def _display_name(self) -> str:
    """
    Короткое человекочитаемое имя стрима для UI.

    Специально фильтруем малополезные description вроде 'Playback',
    чтобы для Chromium не получать 'Chromium — Playback'.
    """
    name = self.name or ""
    desc = (self.description or "").strip()
    app_id = getattr(self, "application_id", "") or ""

    # Обрезаем .desktop
    plain_app_id = app_id.replace(".desktop", "") if app_id else ""

    # Если description типа "Playback" — просто игнорируем
    if desc.lower() in ("playback", "воспроизведение"):
        desc = ""

    # Если и name, и desc есть и они различаются — склеиваем
    if name and desc and name != desc:
        base = name
        # Если application_id что‑то даёт и не дублирует name — добавим в скобках
        if plain_app_id and plain_app_id.lower() not in name.lower():
            base = f"{name} ({plain_app_id})"
        return f"{desc}"

    # Только name
    if name:
        return name

    # Только application_id
    if plain_app_id:
        return plain_app_id

    # Фоллбек — description (если оно не пустое после фильтра)
    return desc


# навешиваем Property на уже существующий класс AudioStream
FabricAudioStream.display_name = Property(str, "readable")(_display_name)


class Audio(FabricAudio):
    """Наследуем базовый Audio без изменений логики."""
    pass
