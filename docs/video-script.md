# Video script (about 5 minutes)

Recording: OBS Studio. Topic view: MQTT Explorer as user `explorer`.
Prepare: `docker compose up -d --build --wait`, browser tabs for the
registry (logged in as `anna.kierownik`), the field app (`jan.technik`),
the hub console (`admin`), PowerShell with `$h` set (see the runbook).

| Time | What | How |
|---|---|---|
| 0:00 | all services run | `docker compose ps`: every service `healthy` |
| 0:20 | 3.0 import | copy `zestawienie_B1.csv` and `zestawienie_B1_bledy.csv` to `data\inbox`; `docker compose logs -f importer`; assets in the registry UI; open `data\error\zestawienie_B1_bledy.rejected.csv` (column `powod`) |
| 1:00 | 4.0 work order | manager creates an order for `PI-B1-2-014`, technician sees it within seconds, records "needs repair"; order `zakonczone` in the registry |
| 1:50 | 4.0 conflict | technician clicks "Work offline" and edits notes, manager edits the same notes; "Go online" -> conflict badge; manager resolves in "Conflicts"; badge disappears |
| 2:30 | 5.0 device -> order | `scenario/damper-fault`; MQTT Explorer: `vigiles/raw/beta/...` then `vigiles/events/fault/...`; order in the registry and at the technician |
| 3:00 | 5.0 coincidence | `scenario/alarm` with 1 detector -> preliminary alarm; with 2 -> confirmed (registry "Incidents", hub console) |
| 3:20 | 5.0 failures | `docker compose stop registry` + faults -> hub console: outbox grows -> `start` -> drains; `docker compose kill hub-1` -> hub-2 continues; `stop mosquitto` -> gateway `/buffer` grows -> `start` -> empties |
| 4:20 | tracing | `docker compose logs --no-log-prefix \| Select-String "<correlation_id>"`: gateway -> hub -> registry -> field app |
| 4:40 | RBAC | log in to the registry as `jan.technik`, open "Conflicts" -> 403 |

Say once, on screen or aloud: Vigiles supervises maintenance; alarms in a
real building are handled by a fire alarm control panel compliant with
EN 54-2.
