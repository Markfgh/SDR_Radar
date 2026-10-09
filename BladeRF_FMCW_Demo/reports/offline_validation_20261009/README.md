# Evidenze offline del 9 ottobre 2026

**66 test PASS**: 11 legacy e 55 SWW. Nessun test hardware/RF eseguito.
`test_results.json` contiene stato, durata ed errori per ciascun test; `tests.txt`
contiene il log completo. Ruff check e format --check: PASS sui nuovi file.

Il run completo, inclusi tutti gli I/Q grezzi e i risultati NPZ, e locale in:
`../run_final_20261009/`. E escluso da git. Questa directory conserva solo evidenze
sintetiche reviewable, senza pubblicare raw o seriali hardware.

- `baseline.json`: commit, ambiente, test iniziali, DLL letta senza aprire USB.
- `metrics.json` / `comparison.png`: caso singolo/ideale/corretto/non corretto.
- `experiments.json`: 28 prove di contaminazione single-RX e sei varianti GLS/overlap/finestre.
- `replay_verification.json`: nove confronti PASS, array-identici su ogni campo NPZ
  (quattro profili radar, quattro motion, una ricostruzione SAR).
- `motion_metrics.json`: compensazione end-to-end su pose sintetiche note.
- `sar_metrics.json` / `sar.png`: 31 pose sintetiche, confronto coerente/incoerente;
  il massimo cade sul pixel del target. L'errore numerico quasi nullo deriva dal
  target presente nella griglia, non da una precisione fisica dimostrata.
- `gui.png`: GUI PySide6 verificata visivamente offscreen.
- `environment-lock.txt`: freeze del virtualenv usato (include strumenti PDF e dev).

Riprodurre tutto senza DLL/radio, da BladeRF_FMCW_Demo:

```powershell
.\.venv\Scripts\python.exe scripts/prepare_offline.py
```

Il comando crea una nuova directory, esegue la suite e i replay e confronta tutti
i campi numerici con `array_equal`. Non esegue probe, RX o TX reale.
I gate hardware restano BLOCKED; vedere il
[piano di lunedi](../../../MONDAY_TEST_PLAN.md).
