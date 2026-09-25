import pytest

from ai_native_intents import destination_role
from ai_native_intents.destinations import explicitly_named_destination


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Find PDFs and copy them to a folder called Private", True),
        ("Перемести файл в папку Готово на рабочем столе", True),
        ("Find files about Private", False),
        ("Find files about Private and copy to a folder called Private", False),
    ],
)
def test_explicit_folder_name_is_separate_from_content_topic(text, expected):
    name = "Готово" if "Готово" in text else "Private"
    assert explicitly_named_destination(name, text) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Copy the files to my Desktop", "desktop"),
        ("Скопируй файлы на рабочий стол", "desktop"),
        ("Put them in Downloads", "downloads"),
        ("Сохрани в документы", "documents"),
        ("Copy the files to my Dekstop", None),
        ("Скопируй файлы на рабочем стлое", None),
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
        ("On my Dekstop", "desktop"),
        ("На рабочем стлое", "desktop"),
        ("На рабчоем столе", "desktop"),
        (
            "Пожалуйста, будь внимателен и ничего лишнего не делай: На рабочем столе",
            "desktop",
        ),
        ("Положи в докмуенты", "documents"),
        ("not Desktop", None),
        ("not Dekstop", None),
        ("nto Dekstop", None),
        ("Do not put it on Desktop", None),
        ("Do not put it on Dekstop", None),
        ("Do nto put it on Dekstop", None),
        ("Not Desktop, use Downloads", "downloads"),
        ("Not Dekstop, use Downlaods", "downloads"),
        ("не на рабочем столе", None),
        ("не на рабочем стлое", None),
        ("Не на рабочем столе, а в документах", "documents"),
        ("Desktop or Downloads", None),
        ("Dekstop or Downlaods", None),
    ],
)
def test_clarification_destination_accepts_natural_wrappers_but_not_ambiguity(
    text, expected
):
    assert destination_role(text, clarification=True) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Put them there", "context.last_destination"),
        ("Положи туда", "context.last_destination"),
        ("Do not put them there", None),
        ("Не клади туда", None),
    ],
)
def test_previous_destination_reference_preserves_negation(text, expected):
    assert destination_role(text, has_last=True, clarification=True) == expected
