# NOTES — контекст проекта для продолжения

Дата записи контекста: 2026-10-16 (вечер).
Проект: `/home/biggriz/vibecod/hddmon`.
Основные документы: `SPEC.md`, `NOTES.md`.

## Где остановились (конец дня 2026-10-16)

Реальный снимок **работает**. Sudoers настроен. Осталось поставить systemd и перенести БД.

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

### TODO (с этого начать завтра)

- [ ] Выполнить установку systemd (команды ниже).
- [ ] Перенести/создать БД в `/var/lib/hddmon/hddmon.db` (см. ниже), чтобы root и пользователь работали с одной БД.
- [ ] Проверить `systemctl list-timers hddmon-collect.timer`, ручной `sudo systemctl start hddmon-collect.service`, `journalctl -u hddmon-collect.service`.
- [ ] Проверить, что `python3 -m hddmon status` (без --db) подхватывает `/etc/hddmon/config.toml` и видит данные из `/var/lib/...`.
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
- Systemd: юниты готовы (см. выше), **на машину не установлены**.
- Пример конфига: `config.example.toml`.
- Тесты: 26 шт (`tests/`), все проходят: `python3 -m unittest discover -s tests`.
- Реальный прогон от `biggriz` через sudoers — работает.

Не сделано:
- Установка systemd на машину (команды выше).
- Перенос БД в `/var/lib/hddmon`.
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
11. ~~Установить systemd на машину.~~ — команды готовы, **выполнить**.
12. ~~Перенести БД в /var/lib/hddmon.~~ — **выполнить**.
13. Накопить историю (несколько дней collect), проверить дельты/прогноз на реальных данных.
