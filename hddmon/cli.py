"""CLI: collect / status / smart / history / predict / report / devices."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

from . import __version__, analyzer, collector, forecast, normalizer, notify, store
from .config import Config, load_config
from .models import EXIT_CODES, Event, Snapshot, Verdict, iso


def _fmt(v: Any) -> str:
    return "-" if v is None else str(v)


def _fmt_eta(days: float | None, when: datetime | None) -> str:
    if days is None or when is None:
        return "—"
    return f"~{int(days)}d ({when.date().isoformat()})"


def _fmt_time(value: Any) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _fmt_size(value: Any) -> str:
    if not value:
        return "-"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


def _fmt_passed(value: Any) -> str:
    if value is None:
        return "-"
    return "passed" if value else "FAILED"


def _snap_from_row(row: Any) -> Snapshot:
    from .models import DeviceInfo
    return Snapshot(
        device=DeviceInfo(
            dev_node=row["dev_node"],
            model=row["model"],
            serial=row["serial"],
            transport=row["transport"],
            protocol=row["protocol"],
            capacity_bytes=row["capacity_bytes"],
        ),
        taken_at=datetime.fromisoformat(row["taken_at"]),
        smart_passed=bool(row["smart_passed"]) if row["smart_passed"] is not None else None,
        temperature=row["temperature"],
        power_on_hours=row["power_on_hours"],
        reallocated=row["reallocated"],
        pending=row["pending"],
        uncorrectable=row["uncorrectable"],
        reported_uncorrect=row["reported_uncorrect"],
        end_to_end_error=row["end_to_end_error"],
        command_timeout=row["command_timeout"],
        load_cycle=row["load_cycle"],
        power_cycle=row["power_cycle"],
        grown_defects=row["grown_defects"],
        selftest_status=row["selftest_status"],
    )


def cmd_collect(cfg: Config, args: argparse.Namespace) -> int:
    conn = store.connect(cfg.db_path)
    raws, errors, discovered = collector.collect_all(cfg)
    readable = {r.dev_node for r in raws}
    unreadable = [node for node in discovered if node not in readable]
    seen_ids: list[int] = []
    all_events: list[Event] = []
    results = []

    for raw in raws:
        snap = normalizer.normalize(raw, store_raw=cfg.store_raw_json or args.store_raw)
        serial = snap.device.serial or f"node:{snap.device.dev_node}"
        is_new = store.device_id_by_serial(conn, serial) is None
        device_id = store.upsert_device(conn, snap)
        if is_new:
            all_events.append(Event(
                device_id, "device_new", "info",
                f"{snap.device.dev_node} ({snap.device.serial or '?'}): новый диск",
            ))
        seen_ids.append(device_id)
        prev = store.get_last_snapshot(conn, device_id)
        analysis = analyzer.analyze(snap, prev, cfg.thresholds, device_id)
        store.insert_snapshot(conn, device_id, snap, analysis.verdict.label,
                              analysis.score, analysis.reasons, cfg.store_raw_json or args.store_raw)
        all_events.extend(analysis.events)
        results.append((snap, analysis))

    for m in store.mark_missing(conn, seen_ids, unreadable):
        all_events.append(Event(
            m["id"], "device_missing", "warning",
            f"{m['dev_node']} ({m['serial']}): диск исчез",
        ))

    for ev in all_events:
        store.insert_event(conn, ev)
    conn.commit()
    conn.close()

    if args.json:
        out = [
            {
                "dev_node": s.device.dev_node,
                "model": s.device.model,
                "serial": s.device.serial,
                "verdict": a.verdict.label,
                "score": a.score,
                "reasons": a.reasons,
                "temperature": s.temperature,
                "power_on_hours": s.power_on_hours,
            }
            for s, a in results
        ]
        print(json.dumps({"devices": out, "errors": errors}, ensure_ascii=False, indent=2))
    else:
        for s, a in results:
            print(f"{s.device.dev_node:8} {a.verdict.label:12} score={a.score:3}  "
                  f"{s.device.model or '?'}  temp={_fmt(s.temperature)}C  poh={_fmt(s.power_on_hours)}")
            for r in a.reasons:
                print(f"         ! {r}")
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)

    notify_errors = notify.notify_events(cfg, all_events, min_severity="warning")
    for e in notify_errors:
        print(f"notify error: {e}", file=sys.stderr)

    if not results and errors:
        return 1
    worst = max((a.verdict for _, a in results), default=Verdict.OK)
    return EXIT_CODES[worst]


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    conn = store.connect(cfg.db_path, read_only=True)
    rows = store.latest_snapshots(conn)
    if args.json:
        out = []
        for r in rows:
            out.append({
                "dev_node": r["dev_node"],
                "model": r["model"],
                "serial": r["serial"],
                "present": bool(r["present"]),
                "taken_at": r["taken_at"],
                "verdict": r["verdict"],
                "score": r["score"],
                "reasons": analyzer.reasons_from_row(r),
                "smart_passed": r["smart_passed"],
                "temperature": r["temperature"],
                "power_on_hours": r["power_on_hours"],
                "reallocated": r["reallocated"],
                "pending": r["pending"],
                "uncorrectable": r["uncorrectable"],
                "reported_uncorrect": r["reported_uncorrect"],
                "selftest_status": r["selftest_status"],
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("нет снимков — запусти `hddmon collect`")
            return 1
        for r in rows:
            flag = "" if r["present"] else " [MISSING]"
            print(f"{r['dev_node']:8} {r['verdict'] or '?':12} score={_fmt(r['score']):3}{flag}  "
                  f"{r['model'] or '?'}  serial={_fmt(r['serial'])}")
            print(f"         taken={_fmt_time(r['taken_at'])}  temp={_fmt(r['temperature'])}C  "
                  f"poh={_fmt(r['power_on_hours'])}  realloc={_fmt(r['reallocated'])}  "
                  f"pending={_fmt(r['pending'])}  uncorr={_fmt(r['uncorrectable'])}")
            for reason in analyzer.reasons_from_row(r):
                print(f"         ! {reason}")
    worst = Verdict.OK
    for r in rows:
        try:
            v = Verdict.from_str(r["verdict"] or "OK")
            if v > worst:
                worst = v
        except KeyError:
            pass
    return EXIT_CODES[worst]


def _resolve_device(conn: Any, ref: str):
    dev = store.find_device(conn, ref)
    if not dev:
        print(f"диск не найден: {ref} (см. `hddmon devices`)", file=sys.stderr)
        return None
    return dev


def _fmt_attr_raw(attr: Any) -> str:
    raw = attr["raw_value"]
    if raw is None:
        return "-"
    # 190/194: smartctl пакует min/max в старшие байты, температура — младший
    if attr["attr_id"] in (190, 194):
        return str(raw & 0xFF)
    return str(raw)


def cmd_smart(cfg: Config, args: argparse.Namespace) -> int:
    conn = store.connect(cfg.db_path, read_only=True)
    dev = _resolve_device(conn, args.device)
    if not dev:
        return 1
    snap = store.get_last_snapshot(conn, dev["id"])
    if not snap:
        print(f"нет снимков для {dev['dev_node']} — запусти `hddmon collect`", file=sys.stderr)
        return 1
    attrs = store.get_attributes(conn, snap["id"])
    reasons = analyzer.reasons_from_row(snap)
    device_out = {
        "dev_node": dev["dev_node"],
        "model": dev["model"],
        "model_family": dev["model_family"],
        "serial": dev["serial"],
        "transport": dev["transport"],
        "protocol": dev["protocol"],
        "capacity_bytes": dev["capacity_bytes"],
        "present": bool(dev["present"]),
        "first_seen": dev["first_seen"],
        "last_seen": dev["last_seen"],
    }
    snapshot_out = {
        "taken_at": snap["taken_at"],
        "verdict": snap["verdict"],
        "score": snap["score"],
        "reasons": reasons,
        "smart_passed": snap["smart_passed"],
        "temperature": snap["temperature"],
        "power_on_hours": snap["power_on_hours"],
        "reallocated": snap["reallocated"],
        "pending": snap["pending"],
        "uncorrectable": snap["uncorrectable"],
        "reported_uncorrect": snap["reported_uncorrect"],
        "end_to_end_error": snap["end_to_end_error"],
        "command_timeout": snap["command_timeout"],
        "load_cycle": snap["load_cycle"],
        "power_cycle": snap["power_cycle"],
        "grown_defects": snap["grown_defects"],
        "selftest_status": snap["selftest_status"],
        "raw_json": snap["raw_json"] is not None,
    }
    if args.json:
        payload = {
            "device": device_out,
            "snapshot": snapshot_out,
            "attributes": [
                {
                    "attr_id": a["attr_id"],
                    "name": a["name"],
                    "raw_value": a["raw_value"],
                    "normalized": a["normalized"],
                    "worst": a["worst"],
                    "failed": None if a["failed"] is None else bool(a["failed"]),
                }
                for a in attrs
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    flag = "" if dev["present"] else " [MISSING]"
    print(f"{dev['dev_node']}  {dev['model'] or '?'}  serial={_fmt(dev['serial'])}{flag}")
    print(f"  протокол: {_fmt(dev['protocol'])} ({_fmt(dev['transport'])})  "
          f"ёмкость: {_fmt_size(dev['capacity_bytes'])}")
    print(f"  снимок: {_fmt_time(snap['taken_at'])}  "
          f"вердикт: {snap['verdict'] or '?'} (score={_fmt(snap['score'])})")
    print(f"  SMART: {_fmt_passed(snap['smart_passed'])}  "
          f"selftest: {snap['selftest_status'] or '-'}")
    print(f"  temp={_fmt(snap['temperature'])}C  poh={_fmt(snap['power_on_hours'])}  "
          f"realloc={_fmt(snap['reallocated'])}  pending={_fmt(snap['pending'])}  "
          f"uncorr={_fmt(snap['uncorrectable'])}  runc={_fmt(snap['reported_uncorrect'])}")
    print(f"  e2e={_fmt(snap['end_to_end_error'])}  timeout={_fmt(snap['command_timeout'])}  "
          f"load_cycle={_fmt(snap['load_cycle'])}  power_cycle={_fmt(snap['power_cycle'])}  "
          f"grown={_fmt(snap['grown_defects'])}")
    for reason in reasons:
        print(f"  ! {reason}")
    if not attrs:
        print("\n  атрибуты не сохранены")
        return 0
    print(f"\n  {'id':>3}  {'атрибут':<34} {'знач':>4} {'худш':>5} {'raw':>14}  флаг")
    for a in attrs:
        failed = "FAILING" if a["failed"] else ""
        print(f"  {_fmt(a['attr_id']):>3}  {a['name']:<34} "
              f"{_fmt(a['normalized']):>4} {_fmt(a['worst']):>5} "
              f"{_fmt_attr_raw(a):>14}  {failed}")
    return 0


def cmd_history(cfg: Config, args: argparse.Namespace) -> int:
    conn = store.connect(cfg.db_path, read_only=True)
    dev = _resolve_device(conn, args.device)
    if not dev:
        return 1
    rows = store.get_history(conn, dev["id"], days=args.days, limit=args.limit)
    if args.json:
        out = []
        for r in rows:
            d = dict(r)
            d["reasons"] = analyzer.reasons_from_row(r)
            out.append(d)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"{dev['dev_node']} ({dev['serial']}): {len(rows)} снимков")
    print(f"{'taken_at':21} {'verdict':12} {'score':>5} {'temp':>5} {'poh':>8} "
          f"{'realloc':>8} {'pend':>6} {'uncorr':>7} {'runc':>6} {'selftest'}")
    for r in rows:
        print(f"{_fmt_time(r['taken_at']):21} {r['verdict'] or '?':12} {_fmt(r['score']):>5} "
              f"{_fmt(r['temperature']):>5} {_fmt(r['power_on_hours']):>8} "
              f"{_fmt(r['reallocated']):>8} {_fmt(r['pending']):>6} "
              f"{_fmt(r['uncorrectable']):>7} {_fmt(r['reported_uncorrect']):>6} "
              f"{r['selftest_status'] or '-'}")
    return 0


def cmd_predict(cfg: Config, args: argparse.Namespace) -> int:
    conn = store.connect(cfg.db_path, read_only=True)
    targets = forecast.targets_from_thresholds(cfg.thresholds)
    devices = store.list_devices(conn, present_only=True)
    out_all = []
    worst = Verdict.OK
    for dev in devices:
        series: dict[str, list[tuple[datetime, int]]] = {}
        for col in forecast.critical_columns():
            s = store.critical_series(conn, dev["id"], col)
            if s:
                series[col] = s
        trends = forecast.predict_device(series, targets=targets)
        last = store.get_last_snapshot(conn, dev["id"])
        if last:
            try:
                v = Verdict.from_str(last["verdict"] or "OK")
                if v > worst:
                    worst = v
            except KeyError:
                pass
        out_all.append((dev, trends))
        if not args.json:
            print(f"{dev['dev_node']} ({dev['serial']}):")
            if not trends:
                if any(len(s) >= 3 for s in series.values()):
                    print("  нет роста критичных атрибутов")
                else:
                    print("  мало снимков для прогноза (нужно >=3)")
            for t in trends:
                print(f"  {t.column}: {t.current}, скорость {t.slope_per_day:+.4f}/дн, "
                      f"r²={t.r2:.2f}, цель {_fmt(t.r_threshold)}, "
                      f"ETA {_fmt_eta(t.eta_days, t.eta_date)}")
    if args.json:
        payload = [
            {
                "dev_node": dev["dev_node"],
                "serial": dev["serial"],
                "trends": [
                    {
                        "column": t.column,
                        "current": t.current,
                        "slope_per_day": t.slope_per_day,
                        "r2": t.r2,
                        "target": t.r_threshold,
                        "eta_days": t.eta_days,
                        "eta_date": iso(t.eta_date) if t.eta_date else None,
                    }
                    for t in trends
                ],
            }
            for dev, trends in out_all
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_CODES[worst]


def cmd_report(cfg: Config, args: argparse.Namespace) -> int:
    conn = store.connect(cfg.db_path, read_only=True)
    targets = forecast.targets_from_thresholds(cfg.thresholds)
    rows = store.latest_snapshots(conn)
    events = store.recent_events(conn, limit=20)
    worst = Verdict.OK
    devices_out = []
    for r in rows:
        try:
            v = Verdict.from_str(r["verdict"] or "OK")
            if v > worst:
                worst = v
        except KeyError:
            pass
        dev = store.find_device(conn, r["serial"] or r["dev_node"])
        series = {}
        if dev:
            for col in forecast.critical_columns():
                s = store.critical_series(conn, dev["id"], col)
                if s:
                    series[col] = s
        trends = forecast.predict_device(series, targets=targets) if series else []
        devices_out.append({"row": r, "trends": trends})

    if args.json:
        payload = {
            "generated_at": iso(datetime.now(timezone.utc)),
            "devices": [
                {
                    "dev_node": d["row"]["dev_node"],
                    "model": d["row"]["model"],
                    "serial": d["row"]["serial"],
                    "present": bool(d["row"]["present"]),
                    "taken_at": d["row"]["taken_at"],
                    "verdict": d["row"]["verdict"],
                    "score": d["row"]["score"],
                    "reasons": analyzer.reasons_from_row(d["row"]),
                    "smart_passed": d["row"]["smart_passed"],
                    "temperature": d["row"]["temperature"],
                    "power_on_hours": d["row"]["power_on_hours"],
                    "reallocated": d["row"]["reallocated"],
                    "pending": d["row"]["pending"],
                    "uncorrectable": d["row"]["uncorrectable"],
                    "reported_uncorrect": d["row"]["reported_uncorrect"],
                    "selftest_status": d["row"]["selftest_status"],
                    "trends": [
                        {
                            "column": t.column,
                            "current": t.current,
                            "slope_per_day": t.slope_per_day,
                            "r2": t.r2,
                            "target": t.r_threshold,
                            "eta_days": t.eta_days,
                        }
                        for t in d["trends"]
                    ],
                }
                for d in devices_out
            ],
            "events": [dict(e) for e in events],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print("=== hddmon report ===")
        for d in devices_out:
            r = d["row"]
            flag = "" if r["present"] else " [MISSING]"
            print(f"\n{r['dev_node']}  {r['model']}  serial={_fmt(r['serial'])}{flag}")
            print(f"  вердикт: {r['verdict']} (score={_fmt(r['score'])})  taken={_fmt_time(r['taken_at'])}")
            print(f"  temp={_fmt(r['temperature'])}C  poh={_fmt(r['power_on_hours'])}  "
                  f"realloc={_fmt(r['reallocated'])}  pending={_fmt(r['pending'])}  "
                  f"uncorr={_fmt(r['uncorrectable'])}  runc={_fmt(r['reported_uncorrect'])}  "
                  f"selftest={r['selftest_status'] or '-'}")
            for reason in analyzer.reasons_from_row(r):
                print(f"  ! {reason}")
            for t in d["trends"]:
                print(f"  тренд {t.column}: {t.current}, {t.slope_per_day:+.4f}/дн, ETA {_fmt_eta(t.eta_days, t.eta_date)}")
        if events:
            print("\n=== последние события ===")
            for e in events:
                print(f"  {_fmt_time(e['at'])} [{e['severity']}] {e['type']}: {e['message']}")
    return EXIT_CODES[worst]


def cmd_devices(cfg: Config, args: argparse.Namespace) -> int:
    conn = store.connect(cfg.db_path, read_only=True)
    rows = store.list_devices(conn, present_only=args.present)
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0
    for r in rows:
        flag = "" if r["present"] else " [MISSING]"
        print(f"{r['dev_node']:8} {r['serial'] or '-':22} {r['model'] or '-':30} "
              f"{r['protocol'] or '-':5} first={_fmt_time(r['first_seen'])} "
              f"last={_fmt_time(r['last_seen'])}{flag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hddmon", description="Мониторинг и прогноз состояния HDD")
    parser.add_argument("--version", action="version", version=f"hddmon {__version__}")
    parser.add_argument("-c", "--config", help="путь к config.toml")
    parser.add_argument("--db", help="путь к SQLite БД (переопределяет конфиг)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="снять SMART со всех дисков")
    p_collect.add_argument("--store-raw", action="store_true", help="сохранить raw JSON smartctl")
    p_collect.add_argument("--json", action="store_true")
    p_collect.set_defaults(func=cmd_collect)

    p_status = sub.add_parser("status", help="текущий вердикт по всем дискам")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_smart = sub.add_parser("smart", help="полный SMART последнего снимка диска")
    p_smart.add_argument("device", help="serial, /dev/узел или model")
    p_smart.add_argument("--json", action="store_true")
    p_smart.set_defaults(func=cmd_smart)

    p_hist = sub.add_parser("history", help="история снимков по диску")
    p_hist.add_argument("device", help="serial, /dev/узел или model")
    p_hist.add_argument("-n", "--limit", type=int, default=50)
    p_hist.add_argument("--days", type=int, help="только за последние N дней")
    p_hist.add_argument("--json", action="store_true")
    p_hist.set_defaults(func=cmd_history)

    p_pred = sub.add_parser("predict", help="прогноз деградации (ETA)")
    p_pred.add_argument("--json", action="store_true")
    p_pred.set_defaults(func=cmd_predict)

    p_rep = sub.add_parser("report", help="сводный отчёт")
    p_rep.add_argument("--json", action="store_true")
    p_rep.set_defaults(func=cmd_report)

    p_dev = sub.add_parser("devices", help="известные диски")
    p_dev.add_argument("--present", action="store_true", help="только подключённые")
    p_dev.add_argument("--json", action="store_true")
    p_dev.set_defaults(func=cmd_devices)

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.db:
        cfg.db_path = args.db
    try:
        return args.func(cfg, args)
    except collector.CollectorError as e:
        print(f"collector error: {e}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
