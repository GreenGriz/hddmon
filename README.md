# hddmon

Мониторинг и прогноз состояния HDD/SSD по SMART. Снимает показатели через `smartctl`,
хранит историю в SQLite, считает вердикты по порогам и динамике, прогнозирует ETA
деградации, умеет уведомлять в Telegram и по email.

- Python 3.11+, только стандартная библиотека.
- ATA/SATA, SCSI и NVMe.
- Вердикты: `OK`, `WATCH`, `BACKUP_SOON`, `BACKUP_NOW`, `REPLACE_NOW`.
- Exit-коды для cron/мониторинга: `0`, `10`, `20`, `30`, `40`.
- Гибридное хранение: ключевые атрибуты колонками + все атрибуты в отдельной таблице.
- Прогноз по линейной регрессии с оценкой качества `r²`.
- Опциональные уведомления Telegram и email.

Подробности: [SPEC.md](SPEC.md) и [NOTES.md](NOTES.md).

## Требования

- Linux.
- Python 3.11 или новее.
- [smartmontools](https://www.smartmontools.org/) (`smartctl` в `PATH`).
- Права на чтение SMART: запуск от root либо узкое правило `sudoers` (см. ниже).

## Установка

### Вариант 1: pipx (рекомендуется)

```bash
sudo apt install pipx        # или пакетный менеджер вашего дистрибутива
pipx ensurepath
pipx install "git+https://github.com/GreenGriz/hddmon.git"
hddmon --version
```

После `pipx ensurepath` может понадобиться перезапустить терминал.

### Вариант 2: venv из исходников

```bash
git clone https://github.com/GreenGriz/hddmon.git
cd hddmon
python3 -m venv .venv
.venv/bin/pip install .
ln -s "$PWD/.venv/bin/hddmon" ~/.local/bin/hddmon
```

`~/.local/bin` должен быть в `PATH`. Если не хотите симлинк, вызывайте
`.venv/bin/hddmon`.

### Вариант 3: pip --user

```bash
python3 -m pip install --user "git+https://github.com/GreenGriz/hddmon.git"
```

На Debian/Ubuntu с PEP 668 это может быть запрещено — используйте pipx или venv.
Каталог `~/.local/bin` должен быть в `PATH`.

## Доступ к SMART

`smartctl --scan` работает без root, но чтение атрибутов — нет. Для обычного
пользователя настройте `sudoers` на конкретный бинарник:

```bash
SMARTCTL=$(command -v smartctl)
echo "$USER ALL=(root) NOPASSWD: $SMARTCTL" | sudo tee /etc/sudoers.d/hddmon
sudo chmod 440 /etc/sudoers.d/hddmon
sudo visudo -c
sudo -n "$SMARTCTL" --scan >/dev/null && echo "sudoers OK"
```

Путь в правиле должен совпадать с путём, который находит `hddmon`
(`smartctl_path` в конфиге, по умолчанию `smartctl` из `PATH`).
Если сервис запускается от root, `sudoers` не нужен.

## Первый запуск

```bash
hddmon collect     # снять SMART со всех дисков
hddmon status      # текущие вердикты
hddmon report      # статус + прогноз + события
```

По умолчанию БД создаётся в:

- `$XDG_DATA_HOME/hddmon/hddmon.db`, если переменная задана;
- иначе `~/.local/share/hddmon/hddmon.db`.

Путь можно изменить в конфиге или флагом `--db`.

## Конфигурация

```bash
mkdir -p ~/.config/hddmon
cp config.example.toml ~/.config/hddmon/config.toml
```

Порядок поиска конфига:

1. `./hddmon.toml` в текущем каталоге;
2. путь из `$HDDMON_CONFIG`;
3. `~/.config/hddmon/config.toml`;
4. `/etc/hddmon/config.toml`.

В конфиге задаются путь к БД, пороги вердиктов, `smartctl_path`, `use_sudo`,
хранение raw JSON и параметры уведомлений. Не коммитьте реальные токены и пароли.

## Systemd: таймер от пользователя (рекомендуется)

Юниты лежат в `systemd/user/`. Они рассчитаны на `hddmon` в `~/.local/bin`
(так ставят pipx и `pip --user`). Если используете venv в другом месте,
поправьте `ExecStart`.

```bash
mkdir -p ~/.config/systemd/user
curl -fsSL https://raw.githubusercontent.com/GreenGriz/hddmon/main/systemd/user/hddmon-collect.service \
  -o ~/.config/systemd/user/hddmon-collect.service
curl -fsSL https://raw.githubusercontent.com/GreenGriz/hddmon/main/systemd/user/hddmon-collect.timer \
  -o ~/.config/systemd/user/hddmon-collect.timer

systemctl --user daemon-reload
systemctl --user enable --now hddmon-collect.timer
systemctl --user list-timers hddmon-collect.timer
journalctl --user -u hddmon-collect.service -n 20
```

Таймер запускается ежедневно со случайным разбросом до часа и добирает
пропущенный запуск (`Persistent=true`). Чтобы сервис работал без активной
сессии пользователя:

```bash
loginctl enable-linger "$USER"
```

## Systemd: системный сервис (альтернатива)

Файлы `systemd/hddmon-collect.service` и `.timer` — пример системной установки.
В них нужно заменить пользователя, группу, `WorkingDirectory` и путь к БД
под свою машину, затем:

```bash
sudo cp systemd/hddmon-collect.service systemd/hddmon-collect.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hddmon-collect.timer
```

Для системного сервиса удобно держать конфиг в `/etc/hddmon/config.toml`:

```toml
db_path = "/var/lib/hddmon/hddmon.db"
store_raw_json = false
```

и дать обычному пользователю доступ на чтение БД, если он хочет смотреть статус.

## Команды

| Команда | Назначение |
|---|---|
| `hddmon collect` | снять SMART со всех дисков и записать снимок |
| `hddmon status` | текущие вердикты и причины |
| `hddmon history <serial>` | история снимков по диску |
| `hddmon predict` | тренды и ETA по критичным атрибутам |
| `hddmon report` | статус + прогноз + последние события |
| `hddmon devices` | известные диски |

Полезные флаги: `--json`, `--db PATH`, `-c/--config PATH`,
`collect --store-raw`.

Exit-коды `status`/`report`/`collect`: `0` — OK, `10` — WATCH,
`20` — BACKUP_SOON, `30` — BACKUP_NOW, `40` — REPLACE_NOW, `1` — ошибка.

## Уведомления

В `config.example.toml` есть секции `[telegram]` и `[email]`. Каналы включаются
независимо. Уведомления отправляются при событиях уровня `warning` и выше:
смена вердикта, рост критичных атрибутов, провал self-test, исчезновение диска.

## Разработка

```bash
git clone https://github.com/GreenGriz/hddmon.git
cd hddmon
python3 -m unittest discover -s tests
```

Запуск без установки: `python3 -m hddmon --help`.

## Частые проблемы

- **`hddmon: command not found`** — добавьте `~/.local/bin` в `PATH`
  (например, в `~/.bashrc`) и перезапустите терминал.
- **`Permission denied` при `collect`** — не настроен `sudoers` или
  `smartctl_path` не совпадает с путём в правиле.
- **`БД не найдена`** — сначала выполните `hddmon collect`.
- **`readonly database`** — `collect` нужен доступ на запись к БД и каталогу;
  команды просмотра работают только на чтение.
