# G6 - Algorithm 1

Stato software: **PASS / TESTATO_OFFLINE**. Coerenza hardware: **BLOCKED G1**.

Matched filter complesso lineare, griglia RF comune, CZT alle frequenze effettive,
maschere Bs, somma complessa, IFFT con asse range e scala del padding espliciti.
Calibrazione complessa con deembedding di ritardo/copia di riferimento e controllo
deriva/SNR. Confronto indipendente con DFT diretta per centri contigui, overlap,
nonuniformi e gap. Test negativi su segno, salto nascosto, reset e ritardo CAL errato.

Caso deterministico: due bersagli 20/24 m; 12 MHz singolo non risolutivo, SWW
96 MHz corretto e ideale risolutivi. Picchi 19.8564/24.1391 m; la tolleranza
simulata e 0.4 m. Le prestazioni non vengono estese alla scheda reale.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests_sww -p test_stacking.py -v
```

Evidenza: `reports/offline_validation_20261009/metrics.json`, `comparison.png`, suite.
