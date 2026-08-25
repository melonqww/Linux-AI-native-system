# documents.pdf

On-demand capability для локального извлечения текста из PDF. Ограничивает
размер и число страниц, не следует по symbolic links, отклоняет зашифрованные
документы и сохраняет номера страниц в извлечённом тексте. PDF без текстового
слоя помечается как требующий будущего OCR-модуля.

Ошибки имеют стабильный `PdfFailureCode`: `damaged`, `encrypted`, `too_large`,
`too_many_pages`, `page_extraction_failed`, `needs_ocr`, `unreadable` или
`invalid_source`. Текст исключения остаётся диагностикой модуля, а Query Service
публикует только код и продолжает возвращать сам файл по метаданным.
