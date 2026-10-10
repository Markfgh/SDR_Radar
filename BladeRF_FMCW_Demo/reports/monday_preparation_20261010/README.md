# Preparazione collaudo — 10 ottobre 2026

**OFFLINE ONLY: nessuna radio aperta o TX RF reale.**

- 83 test PASS: 11 legacy, 55 SWW precedenti, 17 nuovi test bench.
- Ruff check/format PASS su 41 file SWW/test/script.
- Wrapper PowerShell: parsing e dry-run PASS.
- Inventario: 54 sorgenti, inclusi 51 Python, con SHA256 e definizioni.
- 101 manifest raw presenti nel progetto: tutti SIMULATED, nessuno HARDWARE.
- Template dry-run intenzionalmente BLOCKED per KPI/evidenze da compilare.

`test_results.json` e `tests.txt` sono le evidenze della regressione effettivamente
eseguita. Le fixture temporanee con etichetta HARDWARE sono mocks per verificare
il percorso reale: non sono misure hardware e non vengono consegnate come tali.
Le prove negative verificano arresto su USB, fase indipendente errata, permessi
mancanti, integrità e corruzione in resume. Stack e replay identici sono verificati
anche sul piano 40 MS/s, Bi 12 MHz / span 48 MHz, con ritardo frazionario.

## Audit del codice e riuso

| Area | Valutazione / intervento |
|---|---|
| Driver e demo legacy | Conservati byte per byte; SC16 senza metadata e dechirp UNSYNCED non provano coerenza. Riutilizzati driver/configurazione/API/chiusura e conversione RX. |
| Metadata | Riutilizzato backend 2.6.0 e ABI/header checks; timestamp RX non equivale a epoch TX/RX o fase LO. |
| RX selftest | Blocco metadata >=32768, RAM limitata e persistenza dopo stop. Correzione rate/banda/clipping; test su short buffer e ordine delle scritture. |
| Vecchi test loopback | Restano diagnostici: TX_NOW, treni periodici e finestre RX limitate non qualificano G1. Esclusi dal runner, senza riscriverli. |
| CAL single-RX | Proiezione scalare precedente invariata. Harness nuovo con template misurato, ritardo cieco, Gram/SNR/residuo/bias e prova indipendente. Modello a due percorsi, non separatore universale. |
| Matched filter / stacking / GLS | Riutilizzati. Aggiunta sola modalità measured_summed con trasferimento complesso da CAL reale e sottrazione del riferimento misurato; nessuna sintesi dell'eco nel profilo. |
| Metriche | Mainlobe tagliato dal bordo: width=null, non estrapolata. Picco ricercato sull'intero intervallo positivo configurato, non forzato vicino alla distanza nota. |
| Storage/replay | Raw/SHA256/source hash esistenti riutilizzati. Resume verifica ricevute; corruzione aggiorna STOPPED. Stack ricalcola la qualifica dai raw. |
| Scheduler/switch | FSM mock/manuale invariata; hardware commutato BLOCKED. Banco sommato non richiede nuovi switch. |
| Simulator/experiments/reporting | Strumenti offline invariati. Bench non chiama simulate e il test impedisce il fallback. |
| SAR/motion/GUI | Ispezionati e coperti dalle regressioni esistenti, nessuna modifica o nuova funzionalità. |
| Sessione/CLI | Targets simulati/motion rifiutati; permessi per fase, scadenza/hash, budget RF e consenso fisico. Default termina a RX. |

## Limiti

Qualifica limitata al singolo percorso noto stazionario e al range relativo al
riferimento, con trasferimento comune entro il modello. KPI, ritardo indipendente,
livelli RF e connessioni devono essere attestati prima della TX. Non certifica due
bersagli, range/epoch assoluti, multipath arbitrario, commutazione o SAR. Watchdog
non sostituisce lo stop fisico se USB/processo si bloccano.

Procedura e comandi: `../../../MONDAY_TEST_PLAN.md`.
