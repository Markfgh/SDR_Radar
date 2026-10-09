# G2 - modello e simulatore

Stato software: **PASS / SIMULATO / TESTATO_OFFLINE**; nessuna approvazione RF.

Equazioni 1/5: chirp centrato complesso, ritardi frazionari Shannon con guardia DFT,
piu bersagli, PLL casuale per frequenza, drift, passband ampiezza/fase, group delay,
rumore, quantizzazione SC16. Seed e truth separati, origine SIMULATED in ogni capture.

Oracle indipendenti: frequenza istantanea analitica, somma sinc diretta,
convoluzione complessa diretta e DFT esplicita senza CZT.

Riproduzione da BladeRF_FMCW_Demo:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests_sww -p test_math.py -v
.\.venv\Scripts\python.exe -m sww.cli simulate --config configs/simulator.yaml --study
```

Evidenza: `reports/offline_validation_20261009/test_results.json` e `metrics.json`.
Limiti: modello puntiforme stop-and-go; nessuna prova di antenne/PLL/USB reali.
