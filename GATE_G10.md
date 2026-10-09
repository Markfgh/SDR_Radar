# G10 - motion compensation e SAR

Stato: **SIMULATO / TESTATO_OFFLINE**; SAR reale **BLOCKED**.

Compensazione eq.17-20 su coordinate note, test del segno di fase e dell'errore
dell'approssimazione geometrica fuori dal punto di riferimento. Demo end-to-end con
IQ grezzi di una piattaforma mobile, calibrazione e confronto con la scena statica.

Backprojection coerente e somma incoerente su posizioni sintetiche note; demo di
31 pose con raw indipendenti salvati, immagine e replay della stessa apertura.
Un test analitico separato verifica posizione del massimo e focalizzazione.
Nessuna posizione e stata attribuita a dati hardware. Mancano pose misurate e
coerenza inter-sweep per autorizzare SAR reale.

```powershell
.\.venv\Scripts\python.exe -m sww.cli motion-demo --config configs/simulator.yaml
.\.venv\Scripts\python.exe -m sww.cli sar-demo --config configs/simulator.yaml
```

Evidenza: reports/offline_validation_20261009/motion_metrics.json e sar_metrics.json.
