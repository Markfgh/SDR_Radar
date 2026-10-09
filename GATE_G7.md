# G7 - GLS e tecniche di soppressione

Stato software: **PASS / TESTATO_OFFLINE**; confronto hardware **BLOCKED**.

GLS ideale eq.22-24 e versione regolarizzata con maschera/null e gain cap. Overlap,
pesi dei passi, finestre del chirp e del target GLS, frequenze nonuniformi e bande
escluse configurabili separatamente. Test su null e ricostruzione ideale.

Esperimenti a span uguale (96 MHz) riportano PSF, PSL, PGL, larghezza -6dB e
numero di passi. La finestra Hamming abbassa PSL ma allarga il picco. Alcune
configurazioni Tukey/nonuniformi non migliorano ogni metrica: il report le mantiene.
Non e una riproduzione delle prestazioni USRP o delle tabelle del paper.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests_sww -p test_calibration_gls.py -v
.\.venv\Scripts\python.exe -m sww.cli simulate --config configs/simulator.yaml --study
```

Evidenza: `reports/offline_validation_20261009/experiments.json`, test_results.json.
