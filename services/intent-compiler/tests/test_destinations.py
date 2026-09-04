import pytest

from ai_native_intents import destination_role


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Copy the files to my Desktop", "desktop"),
        ("Скопируй файлы на рабочий стол", "desktop"),
        ("Put them in Downloads", "downloads"),
        ("Сохрани в документы", "documents"),
        ("Copy files from Documents to Private", None),
        ("Возьми файлы из загрузок и положи в Private", None),
    ],
)
def test_command_destination_requires_direction(text, expected):
    assert destination_role(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Quickly: On my Desktop", "desktop"),
        ("Please use Documents", "documents"),
        ("Давай на рабочем столе", "desktop"),
        ("Лучше в загрузках, пожалуйста", "downloads"),
        ("not Desktop", None),
        ("Do not put it on Desktop", None),
        ("Not Desktop, use Downloads", "downloads"),
        ("не на рабочем столе", None),
        ("Не на рабочем столе, а в документах", "documents"),
        ("Desktop or Downloads", None),
    ],
)
def test_clarification_destination_accepts_natural_wrappers_but_not_ambiguity(
    text, expected
):
    assert destination_role(text, clarification=True) == expected
