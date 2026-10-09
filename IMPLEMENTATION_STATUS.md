# Stato implementazione - 9 ottobre 2026

Il software offline SWW e implementato in `BladeRF_FMCW_Demo/sww/`, sul branch
`feature/prager-sww`, dalla baseline `82fe36b7f13e709b54d474f233f0fe608f054ffc`.
La demo esistente, i suoi entrypoint e le protezioni TX non sono stati modificati.
**Nessuna TX reale, nessun push, nessun gate hardware approvato.**

## Funzioni ed evidenze

| Funzione | Stato | Evidenza / limite |
|---|---|---|
| Chirp, echo multipli/frazionari, rumore, PLL, RF non ideale | SIMULATO / TESTATO_OFFLINE PASS | Oracle analitico/DTFT/sinc, seed ripetibile |
| Matched filtering complesso e Algorithm 1 | TESTATO_OFFLINE PASS | DFT diretta indipendente; contigue/nonuniformi/overlap/gap |
| CAL complessa, ampiezza/fase/group delay | SIMULATO / TESTATO_OFFLINE PASS | Eco indipendente, casi negativi; common-transfer ipotesi dichiarata |
| Riferimento sommato sul singolo RX | SIMULATO / TESTATO_OFFLINE, LIMITATO | Bias quantificato; eco coincidente non identificabile; nessuna equivalenza con 2 RX |
| GLS ideale e regolarizzato | TESTATO_OFFLINE PASS | Null/mask/gain cap, PSF e ablation a banda uguale |
| Finestre, overlap, pesi, frequenze nonuniformi/bande escluse | TESTATO_OFFLINE PASS | Tecniche distinte; compromessi conservati nel report |
| Raw I/Q, metadati, replay, report | TESTATO_OFFLINE PASS | Raw byte-stabile, output identici, SHA256/source hash, corruzione rifiutata |
| Controller mock/manuale e FSM CAL_PRE/RADAR/CAL_POST | TESTATO_OFFLINE PASS | No retune interno, readback obbligatorio, fault/abort |
| Probe e RX legacy/metadata | IMPLEMENTATO, TESTATO_MOCK | Hardware da collaudare H0-H2 |
| TX metadata/burst, loopback, fase, A-B-A | IMPLEMENTATO, TESTATO_MOCK | Solo con permesso esplicito cablato; nessuna TX eseguita |
| Acquisizione SWW coerente hardware | BLOCKED | Epoch, G1, trasferimenti e switch fisici non qualificati |
| CLI e GUI diagnostica separata | TESTATO_OFFLINE PASS | Sei tab, Qt offscreen e CLI end-to-end; hardware mostra raw/PHASE_UNVERIFIED |
| Motion compensation e SAR | SIMULATO / TESTATO_OFFLINE PASS | Geometria nota, 31 pose raw e replay; reale BLOCKED |
| Demo legacy | TESTATO_OFFLINE PASS | 11 test baseline e finali; funzionamento RF riferito dall'utente, non UWB validato |

La verifica finale ha superato **66 test (11 legacy + 55 SWW)** e nove confronti
di replay identici su tutti i campi NPZ, inclusa l'apertura SAR. Ruff check e
format --check passano sui nuovi moduli. La suite e i risultati effettivi sono in
`BladeRF_FMCW_Demo/reports/offline_validation_20261009/test_results.json`.
`TESTATO_MOCK` indica test dell'API/guardrail con una DLL simulata, non prova USB.

## Risultato misurato in simulazione

Scenario `configs/simulator.yaml`: 8 sottochirp da 12 MHz, span sintetico 96 MHz,
40 MS/s, chirp 25.6 us, bersagli a 20 e 24 m, errori PLL casuali, ripple RF,
ritardo RF 17 ns, CAL 7 ns, AWGN e quantizzazione SC16.

La coppia e risolta nello SWW corretto/ideale, non nel singolo chirp. Picchi
19.8564 e 24.1391 m (errore circa 0.14 m); valle circa 17 dB. Il residuo spettrale
corretto e circa 0.012 rad contro circa 1.85 rad senza calibrazione. Consultare
`metrics.json` per i valori non arrotondati del run finale.
Risoluzione teorica c/(2B): 12.49 m singola, 1.56 m sintetica; larghezza PSF -6 dB
circa 15.01/1.88 m. Queste due definizioni di larghezza non vanno confuse.

Un profilo non corretto puo avere due picchi plausibili spostati di circa 1.3 m:
il test verifica distanze note e fase, oltre all'aspetto grafico. Calibrare la fase
del riferimento da solo non assegna un PASS all'eco.

Contaminazione single-RX: con eco coincidente pari al 50% del riferimento, lo
scenario di fase scelto introduce circa 0.229 rad di bias e un errore di ampiezza
nell'eco indipendente. `experiments.json` contiene 28 combinazioni ritardo/rapporto,
bound di fase e residui. La separazione pratica in un dato reale resta da dimostrare.

## Riproduzione

```powershell
cd C:\py\SDRPlusPlus-master\SDR_Radar\BladeRF_FMCW_Demo
.\.venv\Scripts\python.exe -m pip install -r requirements-sww.txt
.\.venv\Scripts\python.exe scripts/validate_offline.py
.\.venv\Scripts\python.exe scripts/prepare_offline.py
.\.venv\Scripts\python.exe -m sww.cli simulate --config configs/simulator.yaml --study
.\.venv\Scripts\python.exe -m sww.cli gui --config configs/simulator.yaml
```

Ogni run ha una nuova directory, raw completi e report riproducibile. Il comando
di replay e incluso nel summary del run. Motion/SAR e preflight sono descritti
nel [piano di lunedi](MONDAY_TEST_PLAN.md). Contratti e convenzioni DSP sono in
[sww/README.md](BladeRF_FMCW_Demo/sww/README.md); fonti e incongruenze del paper in
[SOURCES.md](BladeRF_FMCW_Demo/sww/SOURCES.md).

## Problemi aperti e gate hardware

- G0/G1: congelare banco RF, banda, limiti analogici, switch/strumenti e KPI numerici.
- G3/G4: misurare capacita metadata della specifica FPGA/firmware, epoch TX/RX,
  continuita, ritardo digitale, retuning, settling e throughput disco/USB.
- G5: dimostrare correzione su eco/linea indipendente e calibrare differenze dei rami.
  Una discontinuita reversibile fra CAL_pre/CAL_post resta non osservabile dagli endpoint.
- G8: bersagli fisici noti e confronto reale due target. Nessun dato reale disponibile.
- G9/G11: GUI SWW hardware e installazione pulita su un secondo ambiente da verificare.
- G10: posizioni reali e fase inter-sweep mancanti. GPR/snow del paper e fuori collaudo.
- TX-only strumentale e commutazione fisica restano bloccati; i comandi loopback
  preparati non sostituiscono il preflight e il consenso per la sessione RF.
- La calibrazione sommata e scalare; dispersione/echo non separabili possono renderla
  inadeguata. Il confronto quantitativo non conclude che gli switch siano indispensabili.
- La conversione SC16 legacy usa `np.clip` su complessi: i moduli nuovi hanno un adapter
  con clipping per componente testato. Il legacy resta invariato per non cambiare
  silenziosamente la demo; la conversione RX e il driver vengono riutilizzati.

I file `GATE_G*.md` registrano separatamente PASS offline e BLOCKED hardware.
Nessuna voce simulata viene presentata come misura reale della bladeRF.
