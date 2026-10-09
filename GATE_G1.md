# G1/G3/G4/G5 - qualifica hardware

Stato: **BLOCKED**. Nessuna TX reale e nessun gate hardware approvato.

Preparati: probe, RX raw legacy/metadata, verifica ABI/header, TX burst metadata
con mock, loopback esplicitamente armato, diagnostica fase fissa e A-B-A, log di
errore, watchdog e condizioni di stop. FSM CAL_PRE/RADAR/CAL_POST verificata solo
con backend mock. Nessuna API fisica per switch non scelti.

Mancano: firmware/FPGA effettivi, continuita su USB, epoch TX/RX, ritardo digitale,
passband utile, livelli analogici, trasferimenti di ramo, switch qualificati,
criteri KPI approvati ed eco indipendente a ritardo noto.
Normalizzare il riferimento non sblocca questo gate. Le catture diagnostiche TX_NOW
non sono terne coerenti. Prossimo passo: MONDAY_TEST_PLAN.md, H0-H5.
