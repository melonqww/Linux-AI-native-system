# Turn Router

Обязательная model-neutral граница Workspace. Qwen предлагает закрытую
классификацию `conversation`, `action`, `mixed` или `clarification`; сервис
проверяет confidence и дословные непересекающиеся фрагменты исходного сообщения.

Turn Router не знает capabilities, не строит план и ничего не исполняет.
