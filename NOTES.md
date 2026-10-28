# NOTES — контекст проекта для продолжения

Дата записи контекста: 2026-10-16 (вечер).
Проект: `/home/biggriz/vibecod/hddmon`.
Основные документы: `SPEC.md`, `NOTES.md`.

## Обновление 2026-10-18 (вечер) — установка завершена

- БД перенесена в `/var/lib/hddmon/hddmon.db`, история цела (снимки 16.10 и 18.10). Резервная копия — `~/.local/share/hddmon/hddmon.db`.
- Юниты установлены в `/etc/systemd/system/`; таймер `hddmon-collect.timer` включён (ежедневно ~00:00 + случайный разброс до 1ч, `Persistent=true`).
- Права для общей БД (root-сервис + CLI пользователя): `/var/lib/hddmon` → `root:biggriz 0775`, БД → `root:biggriz 0664`; в юните `Group=biggriz`, `StateDirectoryMode=0775`, `UMask=0002` (WAL требует запись). Без этого `python3 -m hddmon status` падал с `readonly database`.
- `SuccessExitStatus=10 20 30 40` в юните: `collect` возвращает код худшего вердикта (WATCH=10, BACKUP_SOON=20, BACKUP_NOW=30, REPLACE_NOW=40) — для systemd это не сбой.
- Проверено: `python3 -m hddmon status` без `--db` видит свежие данные из `/var/lib/hddmon`.
- Изменения юнита и NOTES.md закоммичены и запушены в v0.2 (`7e10b8a`).

Дальше: копить снимки (2-3 дня), затем проверить дельты/прогноз (`history`, `predict`, `report`) на реальных данных.

## Где остановились (конец дня 2026-10-16)

Реальный снимок **работает**. Sudoers настроен. systemd установлен и БД перенесена — см. «Обновление 2026-10-18» выше.

### Сделано сегодня (кроме готового v0.1)

1. **Sudoers настроен** (работает):
   ```bash
   /etc/sudoers.d/hddmon:
   biggriz ALL=(root) NOPASSWD: /usr/sbin/smartctl
   ```
   Проверка: `sudo -n smartctl --scan` — ок.

2. **Реальный `collect` отработал** — все 4 диска сняты, `status` показывает данные:
   - sda Seagate 45850ч 37C WATCH / sdb Hitachi 69307ч 42C WATCH / sdc WD Blue 30519ч 40C OK / sdd WD Green 67114ч 40C WATCH
   - Все realloc=0, pending=0, uncorr=0 — физически здоровы; WATCH только по наработке (>43200ч).
   - Exit-код status = 10 (WATCH).
   - БД сейчас в `~/.local/share/hddmon/hddmon.db` (путь по умолчанию для не-root).

3. **`report` исправлен** — стал честным superset над `status` (добавлены temp/poh/realloc/pending/uncorr/selftest в human- и JSON-вывод) + прогноз + события. Тесты 26/26 зелёные.

4. **Юнит подготовлен под PYTHONPATH** (в `systemd/hddmon-collect.service`):
   ```ini
   [Service]
   Type=oneshot
   User=root
   Environment=PYTHONPATH=/home/biggriz/vibecod/hddmon
   ExecStart=/usr/bin/python3 -m hddmon --db /var/lib/hddmon/hddmon.db collect
   ```
   Причина: root не видит `~/.local/bin` и `~/.local/share` пользователя → используем `python3 -m` + PYTHONPATH + общий путь БД `/var/lib/hddmon/hddmon.db`.

### TODO (выполнено 2026-10-18)

- [x] Выполнить установку systemd (команды ниже).
- [x] Перенести/создать БД в `/var/lib/hddmon/hddmon.db`, чтобы root и пользователь работали с одной БД.
- [x] Проверить `systemctl list-timers hddmon-collect.timer`, ручной `sudo systemctl start hddmon-collect.service`, `journalctl -u hddmon-collect.service`.
- [x] Проверить, что `python3 -m hddmon status` (без --db) подхватывает `/etc/hddmon/config.toml` и видит данные из `/var/lib/...`.
- [ ] При желании: `pip install --user -e .` для команды `hddmon` без `python3 -m`.

### Команды для установки systemd (от пользователя, sudo)

```bash
# 1. Системный конфиг с общим путём БД
sudo mkdir -p /etc/hddmon /var/lib/hddmon
printf 'db_path = "/var/lib/hddmon/hddmon.db"\nstore_raw_json = false\n' | sudo tee /etc/hddmon/config.toml

# 2. Перенос существующей БД (опционально, чтобы не потерять первый снимок)
cp ~/.local/share/hddmon/hddmon.db* /tmp/
sudo cp /tmp/hddmon.db* /var/lib/hddmon/
sudo chown root:root /var/lib/hddmon/hddmon.db*

# 3. Юниты
sudo cp /home/biggriz/vibecod/hddmon/systemd/hddmon-collect.service /home/biggriz/vibecod/hddmon/systemd/hddmon-collect.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hddmon-collect.timer

# 4. Проверки
systemctl list-timers hddmon-collect.timer
sudo systemctl start hddmon-collect.service
journalctl -u hddmon-collect.service -n 20 --no-pager
python3 -m hddmon status   # должно видеть данные из /var/lib/hddmon
```

Важно: конфиг-поиск идёт `./hddmon.toml` → `$HDDMON_CONFIG` → `~/.config/hddmon/config.toml` → `/etc/hddmon/config.toml`. Если появится пользовательский `~/.config/hddmon/config.toml`, он перекроет системный — держи в нём тот же `db_path` либо не создавай его.

### Как смотреть состояние (памятка)

```bash
python3 -m hddmon status          # вердикты
python3 -m hddmon status --json
python3 -m hddmon smart Z1W6366E  # все атрибуты последнего снимка
python3 -m hddmon history Z1W6366E   # история по серийнику
python3 -m hddmon predict
python3 -m hddmon report          # status + прогноз + события
python3 -m hddmon devices
```

## Статус реализации (v0.1)

Готово:
- Пакет `hddmon/`: models, config, collector, normalizer, analyzer, store, forecast, notify, cli.
- CLI: `collect`, `status`, `history`, `predict`, `report`, `devices`; флаги `--json`, `--db`, `-c`.
- SQLite: схема v1 (devices, snapshots, attributes, events, meta), миграции, WAL.
- Нормализация ATA/SCSI/NVMe из smartctl JSON.
- Вердикты OK/WATCH/BACKUP_SOON/BACKUP_NOW/REPLACE_NOW + дельты + события.
- Прогноз: линейная регрессия критичных атрибутов → ETA.
- Уведомления: Telegram + email (опционально, из конфига).
- Systemd: юниты установлены, таймер активен (2026-10-18).
- Пример конфига: `config.example.toml`.
- Тесты: 41 шт (`tests/`), все проходят: `python3 -m unittest discover -s tests`.
- Реальный прогон от `biggriz` через sudoers — работает.

Не сделано:
- Веб-интерфейс (отложен по решению).
- Наработка истории для дельт/прогноза (нужен не первый день).

## Ключевой момент по доступу

- `smartctl --scan` работает без root (список дисков).
- `smartctl -a -d scsi /dev/sdX` без root → JSON с `Permission denied` (exit_status=2).
- **Работает:** sudoers `NOPASSWD: /usr/sbin/smartctl` → коллектор идёт через `sudo -n`.
- systemd будет работать от root напрямую (без sudo).

## Диски (lsblk)

| Узел | Размер | Модель | Серийный | Транспорт |
|---|---|---|---|---|
| /dev/sda | 931,5G | WDC WD10EZEX-08M2NA0 (Blue) | WD-WCC3FANSP1TD | sata |
| /dev/sdb | 931,5G | ST1000NM0033-9ZM173 (Seagate Enterprise) | Z1W6366E | sata |
| /dev/sdc | 1,8T | Hitachi HDS723020BLA642 (Deskstar) | MN1220F32JARWD | sata |
| /dev/sdd | 1,8T | WDC WD20EZRX-00DC0B0 (Green) | WD-WCC1T0279702 | sata |

Все ротационные (ROTA=1), SATA. NVMe нет.
`smartctl --scan` отдаёт их как `-d scsi`; нормализатор это учитывает.
**Буквы устройств могут меняться между загрузками** — идентификация по серийнику (serial UNIQUE в devices).

## Что решено (финально, 2026-10-16)

- Чтение SMART: **`smartctl -j -a`**, без сторонних библиотек; авто-тип с фоллбэком.
- Доступ: sudoers NOPASSWD для ручных запусков; systemd timer — от root.
- Хранилище: **SQLite**, один хост, путь настраивается, схема с версией/миграциями; перенос = копия файла.
- Хранение: **гибрид** — ключевые атрибуты колонками в `snapshots` + все атрибуты в `attributes`; raw JSON только флагом `--store-raw`.
- Стек: **Python 3.11, только stdlib** (tomllib для конфига).
- Вердикты: OK / WATCH / BACKUP_SOON / BACKUP_NOW / REPLACE_NOW, на основе порогов + дельт между снимками.
- Диски: автоматически по серийнику (upsert); пропавший → `present=0`, история сохраняется.
- Уведомления: Telegram + email, опционально, независимо.
- UI: CLI; web — возможно позже.
- `report` = superset `status` + прогноз + события.

## План работ

1. ~~Уточнить открытые вопросы.~~ ✔
2. ~~Скелет проекта.~~ ✔
3. ~~Коллектор + нормализатор.~~ ✔
4. ~~Store: SQLite.~~ ✔
5. ~~Analyzer.~~ ✔
6. ~~CLI.~~ ✔
7. ~~Systemd unit + timer (файлы).~~ ✔
8. ~~Уведомления Telegram/email.~~ ✔
9. ~~Прогноз (регрессия/ETA).~~ ✔
10. ~~Sudoers + реальный прогон.~~ ✔
11. ~~Установить systemd на машину.~~ ✔ 2026-10-18.
12. ~~Перенести БД в /var/lib/hddmon.~~ ✔ 2026-10-18.
13. Накопить историю (несколько дней collect), проверить дельты/прогноз на реальных данных.

## Ревью v0.1 (2026-10-18, ночь) — критика и план v0.2

> Полный текст ревью, чтобы продолжить с этого места. Код прочитан целиком (11 модулей, ~2000 строк).

Общая оценка: для v0.1 аккуратно — stdlib-only, гибридное хранение (колонки + все атрибуты), миграции, нормализация трёх протоколов, разделены collect/analyze/store/notify, есть тесты. Ниже — найденное.

### Баг

**`mark_missing` путает «диск не читается» с «диск исчез».** В `cli.py:56` ошибки сбора (таймаут, отвалившийся smartctl) попадают в `errors`, а в `seen_ids` — только успешно снятые. Дальше `store.mark_missing(conn, seen_ids)` (cli.py:72) помечает все остальные `present=1` диски как пропавшие и пишет событие «диск исчез» (cli.py:73). Одна транзиентная ошибка чтения → ложная тревога. Фикс: передавать в `mark_missing` множество *обнаруженных* сканом устройств (успех + ошибка); «пропал» = «скан его не увидел».

### Дизайн

- **Просмотр требует права записи.** `store.connect` на каждом запуске делает `PRAGMA journal_mode=WAL` (store.py:89), поэтому `status`/`history`/`predict`/`report` падают на read-only БД — из-за этого вся возня с `root:biggriz` и StateDirectory, и другой пользователь не сможет даже смотреть. Для команд-просмотра стоило открывать `mode=ro&immutable=1` (проверено — работает) и не трогать WAL.
- **collect возвращает код вердикта** (cli.py:111-112) — для cron удобно, но для systemd пришлось `SuccessExitStatus`. Альтернатива: коды только у `status`/`report`, а `collect` — 0/1, либо флаг `--exit-verdict`.
- **Не перехватываются ошибки БД.** `main()` ловит только `CollectorError`/`FileNotFoundError` (cli.py:383-390); readonly-ошибка вылезла сырым трейсбеком. Нужен catch-all с человеческим сообщением.

### Логика

- **Цели прогноза расходятся с порогами**: `ETA_TARGETS` захардкожены (forecast.py:12) — pending=20, reallocated=100, а пороги конфига `pending_backup_now=10`, `reallocated_backup_now=20`. ETA врёт в «мягкую» сторону; нет оценки качества регрессии (r²) — 3 шумные точки дадут уверенный, но случайный тренд.
- **`_detect_protocol` проверяет `scsi` раньше `ata`** (normalizer.py:45-47): если smartctl отдаст оба блока (бывает на SAT/USB-мостах), критические ATA-атрибуты 197/198 потеряются.
- **`grown_defects`-ветка в analyzer (98-101) фактически мертва**: normalizer уже кладёт `reallocated = grown_defects` (normalizer.py:137), и reallocated-блок раньше поднял вердикт. Работает «по совпадению».
- **События несимметричны**: `device_new` объявлен (models.py:107), но не создаётся; рост `reported_uncorrect` меняет вердикт, но не создаёт событие (analyzer.py:93-96); `attr_growth` уведомляет только по трём атрибутам из четырёх.
- **Мелочи**: `ORDER BY taken_at DESC` без tiebreak по id (store.py:173) — два снимка в одну секунду (iso до секунд, models.py:59) → «последний» неопределён; `_merge` молча игнорирует опечатки в конфиге (config.py:66); ретенция raw_json (SPEC §3, 90 дней) не реализована; время везде UTC — человеку в +08 неудобно (`taken=2026-10-18T15:19:49+00:00`).

### Инфраструктура

- **PYTHONPATH хардкодом на путь репо** в unit — переедет каталог, сервис сломается. Достаточно `WorkingDirectory=/home/biggriz/vibecod/hddmon` (для `python3 -m` cwd попадает в sys.path) или установки пакета.
- **Общая БД в /var/lib — сложнее, чем нужно** для однопользовательской машины: root-сервис + `Group=biggriz` + режимы + UMask. Проще было бы гонять сервис от `biggriz` (sudoers на smartctl уже есть) с БД в `~/.local/share` — ноль прав и трюков. Осознанный выбор, но пересмотреть при следующей правке unit.

### Тесты

26 штук — хорошо, но нет теста на сценарий «часть дисков не снялась» (ровно баг №1), нет тестов поиска/слияния конфига, `notify` не покрыт (легко мокается), нет теста открытия БД без прав записи.

### Приоритет для v0.2 (начать со следующей сессии)

1. Фикс `mark_missing` + тест на него.
2. Read-only просмотр (`mode=ro&immutable=1`) + человеческие ошибки БД вместо трейсбеков.
3. Согласовать ETA-цели с конфигом + фильтр регрессии по r².
4. События `device_new`/reported_uncorrect, мелочи unit, локальное время в human-выводе.

## Сделано в v0.2 (2026-10-19)

Версия поднята до 0.2.0. Закрыты все четыре приоритета ревью:

1. **`mark_missing`**: `collect_all` теперь возвращает и обнаруженные сканом узлы; «пропал» = узел не читается/не найден. Ошибка чтения больше не даёт ложное «диск исчез». Отдельно: диск, заменённый на том же `/dev/*` (новый серийник), корректно помечает старую запись `present=0`. Убран дубль вставки события `device_missing`.
2. **Read-only просмотр**: `status`/`history`/`predict`/`report`/`devices` открывают БД через `mode=ro` (с `immutable=1`, если нет WAL) и не требуют прав записи. `collect` закрывает соединение (WAL чекпоинтится). Ошибки SQLite/OSError ловятся в `main()` и печатаются без трейсбека.
3. **Прогноз**: ETA-цели берутся из конфига (`targets_from_thresholds`, добавлен `thresholds.reported_uncorrect_backup_now = 50`); тренд с `r² < 0.5` не даёт ETA, `r2` виден в human/JSON. Колонки без цели (end_to_end/command_timeout) больше не дают фиктивный ETA=0.
4. **События/вывод/unit**: создаётся `device_new`; `attr_growth` теперь и для uncorrectable/reported_uncorrect; human-вывод в локальном времени (`+08`), JSON — по-прежнему UTC; в unit вместо `PYTHONPATH` — `WorkingDirectory`.

Дополнительно: при наличии обоих блоков smartctl предпочитается ATA (не теряются 197/198 на SAT/USB), `ORDER BY ... id` для снимков/событий.

Тесты: 41/41 (`python3 -m unittest discover -s tests`). Обновлённый unit установлен в `/etc/systemd/system` (2026-10-19), сервис проверен ручным запуском — снимок добавился. Команда `hddmon` доступна через `pip install --user -e .`; `~/.local/bin` добавлен в `PATH` в `~/.bashrc`.

Из ревью остались мелочи: ретенция raw_json (90 дней), предупреждения о неизвестных ключах конфига, покрытие `notify` тестами, пересмотр схемы «root-сервис + общая БД в /var/lib».

## Публикация для внешних пользователей (2026-10-19)

- Добавлен `README.md`: установка через pipx / venv / `pip --user` прямо с GitHub, настройка sudoers, первый запуск, конфиг, systemd, команды, exit-коды, troubleshooting.
- `pyproject.toml`: добавлены `readme`, authors, keywords, classifiers, project URLs. Установка из чистого venv проверена (`pip install .` → `hddmon --version` = 0.2.0).
- Добавлены переносимые user-юниты `systemd/user/hddmon-collect.{service,timer}` (`%h/.local/bin/hddmon`, PATH с `/usr/sbin`, `TimeoutStartSec=10min`); проверены `systemd-analyze verify`.
- Лицензия по решению владельца не выбрана.
- Изменения (README, pyproject, user-юниты, этот раздел) закоммичены (`941f3e4`).

## Проверка 2026-10-21 — сессия восстановлена

- Репозиторий чистый, HEAD `941f3e4`, версия 0.2.0, тесты 41/41.
- Таймер `hddmon-collect.timer` активен, последний запуск 21.10 00:57, следующий 22.10 00:41. Юнит в `/etc/systemd/system`, user-юниты не включены (и не нужны).
- БД `/var/lib/hddmon/hddmon.db`: 4 устройства, по 8 снимков на диск (16.10 → 21.10), события отсутствуют (роста критичных атрибутов не было).
- `status`/`report`: sda OK, sdb/sdc/sdd WATCH только по наработке; все realloc/pending/uncorr = 0. `predict`: роста критичных атрибутов нет — ETA не строится, что корректно.
- Пункт 13 плана (накопить историю и проверить дельты/прогноз на реальных данных) можно считать выполненным: дельты и read-only просмотр работают на живой БД.
- Остаток бэклога без изменений: ретенция raw_json (90 дней), предупреждения о неизвестных ключах конфига, тесты `notify`, пересмотр схемы «root-сервис + общая БД в /var/lib», выбор лицензии.

## v0.3.0 (2026-10-21) — команда `smart`

- Добавлена `hddmon smart <serial|/dev|model> [--json]`: показывает полный SMART последнего снимка диска из БД, без вызова smartctl.
- Human-вывод: устройство (модель, серийник, протокол, ёмкость), снимок/вердикт/причины, ключевые поля (temp, poh, realloc, pending, uncorr, runc, e2e, timeout, load/power cycle, grown), затем таблица всех сохранённых атрибутов: id, имя, normalized, worst, raw, FAILING.
- `--json`: `{device, snapshot, attributes}` — удобно для скриптов.
- Особенность: у атрибутов 190/194 smartctl пакует min/max в старшие байты raw; в human-выводе показывается младший байт (текущая температура).
- Новое: `store.get_attributes()`; версия 0.3.0; README/SPEC обновлены.
- Тесты: +4 (всего 45/45), включая read-only `smart` и SCSI-атрибуты.
- Ограничение: показываются только сохранённые поля; raw-строка атрибута (например, «34 (Min/Max 16/45)») не хранится — при необходимости добавить колонку и миграцию схемы.

## v0.4.0 (2026-10-21) — описания критических параметров SMART

- Добавлен модуль `hddmon/guide.py`: по каждому критическому параметру — что это, что показывает, почему важно, когда волноваться. Пороги подставляются из `[thresholds]` конфига.
- `hddmon smart <диск> --explain` (`-e`) добавляет описания к выводу; для ATA показываются только те параметры, которые реально есть в последнем снимке. В `--json` добавлен ключ `explanations`.
- Новый `SMART.md` — подробный справочник (ATA, SCSI, NVMe) + объяснение, почему 1/7/200 не входят в вердикт. README ссылается на него и на `--explain`.
- Версия 0.4.0. Тесты: 52/52 (+7: guide и CLI `--explain`).

## Проверка 2026-10-28 — сессия восстановлена

- Репозиторий чистый, HEAD `2af88bc` (v0.4.0), совпадает с `origin/main`; тесты 52/52.
- `hddmon` в `~/.local/bin` — editable-установка из репозитория, `hddmon --version` = 0.4.0. Нюанс: `pip show hddmon` показывает старые метаданные 0.1.0 (dist-info не обновлялся при бампах версии); на работу не влияет, при случае обновить через venv/pipx или `pip install --user -e . --break-system-packages`.
- Таймер `hddmon-collect.timer` активен: последний запуск 28.10 00:04, следующий 29.10 00:33. После первого сбоя 18.10 (до фикса `SuccessExitStatus`) все запуски успешны, снимки пишутся ежедневно; системный юнит совпадает с репозиторием, `systemd-analyze verify` проходит.
- БД `/var/lib/hddmon/hddmon.db`, schema v1: 4 устройства, 60 снимков (по 15 на диск, 16.10 → 28.10), события отсутствуют — критичные атрибуты не растут.
- Буквы устройств снова переставились: `/dev/sda` WD Blue (OK), `/dev/sdb` Hitachi, `/dev/sdc` Seagate, `/dev/sdd` WD Green (WATCH). Идентификация по серийникам сработала, история не потерялась.
- `status`/`report`: WATCH только по `power_on_hours` (sdb 69490, sdc 46033, sdd 67291), у всех `realloc=0`, `pending=0`, `uncorr=0`. `predict`: роста критичных атрибутов нет, ETA не строится — корректно.
- Бэклог без изменений: ретенция raw_json (90 дней), предупреждения о неизвестных ключах конфига, тесты `notify`, пересмотр схемы «root-сервис + общая БД в /var/lib».

## Лицензия (2026-10-28) — MIT

- Выбрана MIT. Добавлены `LICENSE` (Copyright (c) 2026 GreenGriz), `license = { text = "MIT" }` и classifier в `pyproject.toml`, раздел «Лицензия» в README.
- `LICENSE` попадает в wheel автоматически (проверено сборкой). Версия не менялась — это не функциональное изменение.

## v0.5.0 (2026-10-29) — notify-test, SMTP_SSL и тесты notify

- Добавлена команда `hddmon notify-test [--json]`: отправляет тестовое сообщение во все включённые каналы и возвращает `1`, если хотя бы один канал не ответил. Без включённых каналов — понятная ошибка.
- Email: поддержка `SMTP_SSL` (`use_ssl = true`, обычно порт 465) в дополнение к STARTTLS; пароль можно задать через `HDDMON_SMTP_PASSWORD` вместо конфига. При включённом канале с неполными настройками теперь явная ошибка, а не молчаливый пропуск.
- Добавлен `tests/test_notify.py` (19 тестов) + 2 CLI-теста: отключённые каналы, неполный конфиг, формирование письма/Telegram-payload, STARTTLS/SSL, env-пароль, фильтр severity, независимость ошибок каналов, `notify_test`. Всего тестов 73/73.
- README/config.example: пример Яндекс.Почты (`smtp.yandex.ru`, 465, `use_ssl`), описание `notify-test`.
- Для живой отправки на `greengriz@yandex.ru` осталось добавить в `/etc/hddmon/config.toml` пароль приложения (или `HDDMON_SMTP_PASSWORD` в systemd EnvironmentFile): прав на запись в `/etc` у агента нет.
