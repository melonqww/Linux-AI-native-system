# Module SDK

Формальный контракт capability-модулей AI-native Linux. Первая версия содержит
JSON Schema `schema/module-manifest.schema.json`. Runtime Registry выполняет ту
же строгую проверку стандартной библиотекой Python, поэтому для запуска core не
нужна внешняя зависимость `jsonschema`.

Manifest располагается рядом с модулем под именем `module.json`. Относительный
`entrypoint.python_path` разрешается только внутри каталога модуля.
