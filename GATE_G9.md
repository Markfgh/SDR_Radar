# G9/G11 - integrazione e riproducibilita

Stato software: **PASS / TESTATO_OFFLINE**; integrazione con dati reali **BLOCKED**.

Raw int16, manifest append-only, config/source hash, SHA256 per blocco, replay
numericamente identico, report JSON/Markdown/PNG/NPZ, CLI e GUI PySide6 con sei tab.
Test fault injection: no DLL, timeout, overrun, switch errato, disk full, watchdog,
Ctrl-C, permesso incompleto, clipping, dati corrotti. Nessun fallback hardware -> fake.

La GUI e stata avviata e renderizzata offscreen con dati simulati. La catena legacy
e rimasta invariata e i suoi 11 test passano. Report/raw locali non vengono pushati.
La prova di throughput su disco/USB e l'integrazione GUI con SWW reale restano H1-H6.

```powershell
.\.venv\Scripts\python.exe scripts/validate_offline.py
.\.venv\Scripts\python.exe -m sww.cli gui --config configs/simulator.yaml
```

Evidenza: reports/offline_validation_20261009/ e MONDAY_TEST_PLAN.md.
