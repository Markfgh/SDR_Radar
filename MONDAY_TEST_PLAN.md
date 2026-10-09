# Collaudo bladeRF 1.0 - lunedi 12 ottobre 2026

Preparazione software del 9 ottobre 2026. **Nessuna TX reale eseguita durante lo sviluppo.**
I valori nei file `configs/` sono scenari/candidati, non banda RF approvata.
I risultati offline si trovano in `BladeRF_FMCW_Demo/reports/offline_validation_20261009/`.

## 1. Prerequisiti e decisioni prima del banco

Operare sul PC Windows x64 che accede realmente alla USB della bladeRF. Verificare
con l'operatore: modello/revisione, firmware/FPGA caricati, cavi/carichi 50 ohm,
schema effettivo, antenna scollegata per la prima TX, attenuazione complessiva,
limite RF analogico del RX da fonte specifica e livello massimo incidente stimato
o misurato. Il guadagno libbladeRF e i dBFS non sono una misura in dBm.

Servono una linea/bersaglio a ritardo noto **indipendente dalla CAL**, una misura
del ritardo di riferimento, strumenti per livello RF e, per la sequenza commutata,
modello, banda, isolamento, terminazioni, logica e ripetibilita degli switch.
Il software non sceglie pinout o controller non documentati.

Prima di approvare G1 concordare e registrare: RMS e percentile della fase
ammessi, errore di range, SNR minimo del riferimento, dispersione dopo A-B-A,
durata sweep, deriva CAL pre/post, tolleranza sui ritardi e margine analogico.
Queste soglie hardware **non sono dedotte dalle soglie dei test simulati**.
In assenza di soglie/evidenze il gate resta `BLOCKED`, anche con grafici plausibili.

## 2. Ambiente e prove offline (M0-M5)

Da PowerShell:

```powershell
cd C:\py\SDRPlusPlus-master\SDR_Radar\BladeRF_FMCW_Demo
.\.venv\Scripts\python.exe -m pip install -r requirements-sww.txt
.\.venv\Scripts\python.exe scripts/validate_offline.py --report reports/offline_check_monday
.\.venv\Scripts\python.exe -m sww.cli simulate --config configs/simulator.yaml --study
.\.venv\Scripts\python.exe -m sww.cli plan --config configs/loopback_safe.yaml --dry-run
.\.venv\Scripts\python.exe -m sww.cli gui --config configs/simulator.yaml
```

L'installazione si esegue solo se necessaria; il virtualenv esistente e gia pronto.
Per ripetere l'intera preparazione, inclusi i nove confronti di replay radar/motion/SAR,
eseguire `.\.venv\Scripts\python.exe scripts/prepare_offline.py`: nessuna radio viene aperta.
`validate_offline.py` esegue esplicitamente le due directory di test: la sola
discovery dalla root della vecchia demo potrebbe non raccogliere i test legacy.
Ogni comando CLI stampa la cartella del report. `--report` richiede una cartella
nuova, per preservare le sessioni precedenti. Il report della suite puo essere
aggiornato esplicitamente se si riutilizza il suo percorso.

PASS offline: tutti i test, due bersagli a 20/24 m risolti con SWW corretto da
96 MHz, singolo chirp da 12 MHz non risolutivo, residuo di fase entro il criterio
simulato. Questi numeri sono un esperimento matematico, non una promessa hardware.

## 3. H0 - identificazione (TX OFF)

```powershell
.\.venv\Scripts\python.exe -m sww.cli probe
# Comando legacy, separatamente se serve un confronto:
.\.venv\Scripts\python.exe main.py --probe
```

Salvare `device.json`: firmware, FPGA, DLL, revisione esposta dall'API, range RX/TX,
hash dell'header e layout ctypes. Verificare che board/FPGA corrispondano alla
bladeRF 1.0 reale. Il nuovo probe forza TX OFF e non configura un trasmettitore.
Un range riportato dall'API non certifica banda utile, componenti o legalita RF.
Errore di apertura/API => FAIL/BLOCKED, fermare le prove dipendenti.

## 4. H1 - RX separato (TX OFF)

Modificare la configurazione candidata secondo il banco. Il comando usa il
guadagno RX minimo riportato dalla scheda, registra rate/banda effettivi e
conserva gli SC16 originali:

```powershell
.\.venv\Scripts\python.exe -m sww.cli rx-selftest --config configs/loopback_safe.yaml --blocks 100
.\.venv\Scripts\python.exe -m sww.cli rx-selftest --config configs/loopback_safe.yaml --blocks 1000
```

Esaminare `rx_checks.json` per potenza digitale, headroom e clipping. La modalita
legacy non puo dimostrare continuita campione per campione: relativo gate BLOCKED.
Il livello analogico sicuro va verificato indipendentemente dal clipping ADC.

Automazione H0/H1, senza alcun comando TX:

```powershell
.\scripts\monday_preflight.ps1 -Config configs/loopback_safe.yaml -Blocks 100
```

## 5. H2 - metadata e sincronizzazione

```powershell
.\.venv\Scripts\python.exe -m sww.cli rx-selftest --config configs/loopback_safe.yaml --blocks 100 --metadata
.\.venv\Scripts\python.exe -m sww.cli rx-selftest --config configs/loopback_safe.yaml --blocks 1000 --metadata
```

PASS di continuita: per ogni buffer `actual_count == requested_count`, status zero,
timestamp successivo uguale al primo timestamp piu il numero di campioni validi.
Una discontinuita interrompe la sessione, conserva il prefisso valido e salva
l'errore. Questo non dimostra la relazione temporale TX/RX ne la fase dei LO.

Il backend metadata e separato, opt-in, richiede DLL 2.6.0 e header compatibile.
L'header locale include `SC16_Q11_PACKED` prima di `SC16_Q11_META`: quest'ultimo
vale **2** in questa versione. Non copiare costanti da versioni diverse.
Controllo ABI nativo opzionale in un Developer PowerShell con compilatore MSVC:

```powershell
cl /I"C:\Program Files\bladeRF\include" scripts\metadata_abi.c
.\metadata_abi.exe
```

Atteso x64: size 56; offset timestamp/flags/status/actual_count/reserved
0/8/12/16/20; format 2. Non e stato compilato in preparazione: compilatore non
presente nella PATH. Il layout ctypes e stato verificato con mock e header.

Per abilitare acquisizioni pianificate occorre misurare separatamente le epoch
TX/RX, il ritardo digitale e la semantica dei burst. `scheduled_burst()` espone
timestamp TX e flag corretti, coperti da mock; **non assume epoch condivise**.
`get_timestamp()` non assegna automaticamente un tempo al primo campione RX.

## 6. H3/H4 - prima TX cablata, fase fissa e A-B-A (autorizzazione necessaria)

Questa sezione e una procedura futura da eseguire con l'operatore presente.
La preparazione non ha autorizzato alcuna emissione.

1. Usare esclusivamente un loopback chiuso/terminato, senza irradiazione.
2. Verificare bilancio di potenza analogica, livelli massimi, banda, durata e
   attenuazione complessiva; ottenere l'autorizzazione della singola sessione.
3. Copiare `configs/permit_TEMPLATE.json` in `captures/permit_local.json` e compilare
   manualmente tutti i campi con valori/evidenze reali. Non usare il template vuoto
   come permesso. `tx_gain_db` deve coincidere con il minimo misurato dal probe.
4. Eseguire una sessione breve; esaminare grezzi, clipping, timestamp e template
   detection prima di aumentare numero di acquisizioni.

Comandi preparati, **da non eseguire prima dei quattro passi precedenti**:

```powershell
.\.venv\Scripts\python.exe -m sww.cli loopback --backend hardware --config configs/loopback_safe.yaml --confirm-tx --permit captures/permit_local.json --repeats 5
.\.venv\Scripts\python.exe -m sww.cli phase-stability --backend hardware --config configs/loopback_safe.yaml --confirm-tx --permit captures/permit_local.json --repeats 20
.\.venv\Scripts\python.exe -m sww.cli frequency-sweep --backend hardware --config configs/aba.yaml --aba --confirm-tx --permit captures/permit_local.json --repeats 10
```

Il massimo numero di burst deve coprire anche il ritorno A (3*repeats in A-B-A).
Budget software: durata <=60 s, burst <=1000, ampiezza <=5%, TX gain minimo;
il permesso deve imporre limiti anche inferiori se richiesti dal banco.
Thread watchdog, timeout API finiti, Ctrl-C, errore, clipping o perdita stream
arrestano TX; una chiusura forzata del processo non sostituisce l'arresto RF fisico.

Questi comandi fanno **diagnostica cablata con burst TX_NOW**, RX concorrente e
correlazione del template. Salvano waveform TX, RX grezzi, configurazioni applicate,
metadata, fasi, lag, confidence, ADC, statistiche ed errori. Non attestano un'origine
temporale comune o fase corretta su un eco indipendente. Gli stream possono essere
riavviati tra frequenze: l'effetto deve essere misurato, non ignorato.
`G1_PHASE` resta BLOCKED. `tx-only` e bloccato perche mancherebbe il monitor RX;
la verifica TX separata con analizzatore/carico richiede una procedura strumentale.

## 7. H5 - scegliere il metodo single-RX con una prova indipendente

**Somma riferimento+eco:** registrare riferimento solo, eco noto solo e somma,
alla stessa frequenza e configurazione. Variare rapporto eco/riferimento e ritardo.
Confrontare la fase dell'eco corretto con il suo valore indipendente noto.
La proiezione del riferimento e implementata, ma l'eco sovrapposto introduce bias
complesso. Un riferimento normalizzato a fase zero non prova che l'eco sia corretto.
In assenza di separazione osservabile, la contaminazione non e identificabile dal
solo RX: segnare FAIL/BLOCKED, non correggere arbitrariamente il profilo.

**Due switch:** il software dispone di controller mock e manuale con readback
indipendente obbligatorio. Il controller fisico, la strategia di mute e la continuita
degli stream restano BLOCKED fino alla qualifica. Dopo scelta del modello verificare
CAL_pre -> RADAR -> CAL_post senza retune, cambio di gain/rate, reset digitale o
restart stream nella terna. Misurare trasferimenti dei due rami e ritardi: il solo
ramo CAL non calibra automaticamente switch/antenne del ramo RADAR.
Un salto di fase che avviene e si annulla tra CAL_pre e CAL_post non e osservabile
dai soli endpoint; serve sempre il bersaglio/linea indipendente.

## 8. H6/H7 - stacking reale e confronto con singolo chirp

Prima richiedere evidenze G1-H5 e approvare soglie/topologia/banda. Il comando di
acquisizione coerente reale e intenzionalmente bloccato finche manca tale qualifica:

```powershell
.\.venv\Scripts\python.exe -m sww.cli stacking --backend hardware --config configs/radar_bench.yaml
```

Non usare le catture diagnostiche TX_NOW come se fossero terne CAL/RADAR sincronizzate.
La funzione offline accetta terne complete solo con frequenze effettive uguali al
piano, nessuna perdita/clipping, origine digitale qualificata e tempi CAL/RADAR
misurati. Salvare evidenze e trasferimento di ramo prima di integrare il controller
reale nella FSM. Il flag di metadata da solo non approva G1.

Passare da 2-4 passi a 8-16 solo dopo i gate precedenti. Usare una linea a ritardo
noto e poi due bersagli noti. Confrontare range, errore, PSF -6 dB, PSL/PGL,
copertura, SNR e residuo di fase con un singolo chirp e con una simulazione alla
**stessa banda effettiva**. Overlap, finestra e GLS sono variabili distinte.
I buchi spettrali non sono ricostruiti con dati inventati.

## 9. Replay, report e SAR

```powershell
# Sostituire RUN con il percorso stampato dal comando simulate:
.\.venv\Scripts\python.exe -m sww.cli replay --capture reports/RUN/capture
.\.venv\Scripts\python.exe -m sww.cli motion-demo --config configs/simulator.yaml
.\.venv\Scripts\python.exe -m sww.cli sar-demo --config configs/simulator.yaml
# Per una cattura sar-demo:
.\.venv\Scripts\python.exe -m sww.cli sar-replay --capture reports/RUN_SAR/capture
```

Conservare ogni `manifest.json`, raw `iq/*.npy`, configurazione, hash e report.
Il replay verifica SHA256 di ogni blocco e l'hash del codice di elaborazione;
per una versione differente ripristinare il codice della cattura prima del replay.
La GUI mostra grezzi reali senza inventare profili coerenti.
Seriali, IQ e permessi locali restano esclusi dal push automatico (nessun push previsto).

SAR e motion compensation sono SIMULATI/TESTATI_OFFLINE. SAR reale richiede
posizioni misurate delle antenne, geometria corretta e fase inter-sweep qualificata:
il gate G10 resta BLOCKED.
