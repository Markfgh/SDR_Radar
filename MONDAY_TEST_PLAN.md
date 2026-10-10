# Collaudo hardware — lunedì 12 ottobre 2026

Preparato il **10 ottobre 2026**, bladeRF 1.0, Windows x64, libbladeRF 2.6.0.
**Nessuna TX o misura hardware eseguita durante la preparazione.** Obiettivo:
qualificare la calibrazione per frequency stacking di Prager su un percorso
noto indipendente e stazionario. La normalizzazione del riferimento non prova coerenza.

## Banco e ambito

```text
                      +----------------------> antenna TX
bladeRF TX -> splitter
                      +-> attenuatore 30 dB --+
                                             combiner -> bladeRF RX
antenna RX ----------------------------------+
```

Usare inizialmente il banco disponibile, senza nuovi switch. Per le sole CAL
`reference_pre/post`, **a TX disabilitata**, sostituire l'antenna RX con un carico
50 ohm al ramo eco del combiner. Preservare ramo diretto, cavi, gain e rate.
Ripristinare l'antenna RX per `fixed/aba/sweep`. L'antenna TX eventualmente ancora
collegata richiede autorizzazione esplicita anche durante le CAL.

I 30 dB non proteggono dal segnale sul ramo antenna RX. Registrare livelli peggiori
ai **due ingressi** del combiner, isolamento, accoppiamento TX/RX, perdite e potenza
totale incidente al RX. Per la somma coerente peggiore, prima delle perdite comuni:
`Ptot_mW=(sqrt(Pdiretto_mW)+sqrt(Pantenna_mW))²`, oppure una misura strumentale
che limiti entrambi i contributi. Considerare LO leakage e transitori. dBFS e gain
API non sono dBm. Allegare specifica del RX effettivo, livelli e margine approvato.
Il programma verifica le attestazioni; non misura collegamenti o dBm al posto dell'operatore.

L'utente ha autorizzato la scelta della banda disponibile sulla scheda. Piano
iniziale: centri **2414/2426/2438/2450 MHz**, Bi=Bs=12 MHz, intervallo RF
**2408–2456 MHz**, span 48 MHz, fs=40 MS/s, chirp 25,6 µs, ampiezza digitale <=0,05.
Verificare copertura di tutti i componenti. Il programma controlla rate, banda e
centri applicati. Non ampliare prima del primo PASS. Sono impostazioni, non misure RF.

Preparare un riflettore isolato con geometria misurata oppure una seconda linea
con ritardo misurato indipendentemente dalla bladeRF. `relative_delay_s` significa
**ritardo eco meno ritardo riferimento**, inclusi i rami/cavi. Con antenne distinte
usare `(d_TX_bersaglio+d_bersaglio_RX)/c`, con i ritardi dei cavi, senza assumere 2R/c.
L'asse finale `c*ritardo_relativo/2` è una distanza monostatica equivalente relativa.
Non ricavare il ritardo noto dal picco radar da verificare.

La qualifica implementata riguarda **un singolo eco noto, separabile e stazionario**.
Multipath, dispersione differenziale, eco coincidente o movimento possono dare FAIL.
Un PASS non certifica due bersagli, range assoluto, scene arbitrarie, switch CAL/RADAR,
epoch assoluta TX/RX o SAR. Nessuna nuova funzione SAR o modifica GUI in questo intervento.

## Comando unico e arresto

Tutti i comandi partono dalla stessa PowerShell:

```powershell
cd C:\py\SDRPlusPlus-master\SDR_Radar\BladeRF_FMCW_Demo
```

Runner `python -m sww.bench`, anche via `scripts\monday_test.ps1`. Ordine obbligatorio:

```text
offline -> probe -> rx -> reference_pre -> fixed -> aba -> sweep
        -> reference_post -> validate -> stack
```

`--through` termina alla fase indicata. Exit **0**: fasi richieste completate;
**1**: FAIL/abort; **2**: BLOCKED/autorizzazione mancante. Exit 0 dopo RX non approva
la fase. `sequence.json` registra primo errore, hash, risultati e ambito del PASS.
Nessun bypass di gate e nessun backend simulato.

Ogni fase TX richiede contemporaneamente:

1. `--confirm-tx` e il suo nome in `--authorize-tx` nell'invocazione corrente;
2. permesso locale con scadenza e hash sessione, valido per quella fase;
3. digitazione di `TX <session_id> <stage>` dopo controllo fisico dei collegamenti.

La libertà di scelta banda non attiva TX. Il vecchio permesso di loopback chiuso
non autorizza antenne: non usare `configs/permit_TEMPLATE.json` per questo runner.
Count/status/timestamp invalidi, clipping, impulsi mancanti/tagliati, SNR basso,
fit non identificabile, residuo/fase/ritardo fuori soglia, corruzione o errore disco
fermano le dipendenze. Eccezione/timeout/Ctrl-C disabilitano TX e chiudono la radio.
Watchdog TX <=60 s, timeout API finiti, RAM <=64 MiB per visita. Tenere disponibile
un arresto RF fisico: USB bloccata o processo terminato dal sistema non consentono
una garanzia di spegnimento tramite software.

## Sessione e permessi, senza radio

Eseguire le copie una sola volta, senza sovrascrivere una sessione compilata:

```powershell
New-Item -ItemType Directory -Force captures\monday\permits | Out-Null
Copy-Item configs\monday_session_TEMPLATE.json captures\monday\session.json
foreach ($stage in @('reference_pre','fixed','aba','sweep','reference_post')) {
    Copy-Item configs\monday_permit_TEMPLATE.json "captures\monday\permits\$stage.json"
}
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --through stack --dry-run
```

Dry-run: nessuna DLL/USB; stampa hash, ordine e primo prerequisito mancante. Il suo
successo prova la leggibilità del piano, non KPI completi o autorizzazione RF.
I `null` nei criteri vanno compilati con soglie approvate prima della TX: non usare
quelle delle fixture offline e non adattarle dopo avere visto i risultati.

| Campo `criteria` | Verifica |
|---|---|
| `detection_peak_fraction`, `min_detection_confidence` | Individuazione esatta degli impulsi e correlazione normalizzata col chirp |
| `max_reference_shape_error` | Errore L2 forma CAL dopo sola compensazione scalare |
| `max_gram_condition` | Condizionamento Gram riferimento/eco |
| `min_reference_fit_snr_db`, `min_echo_fit_snr_db` | SNR dei coefficienti stimati, inclusa correlazione template; non SNR ADC per campione |
| `max_model_residual_fraction` | Norma residuo modello a due percorsi / norma I/Q |
| `max_phase_rms_rad`, `max_phase_p95_rad` | RMS e percentile 95 del residuo dell'eco indipendente, radianti |
| `max_delay_error_s` | Errore ammesso della stima cieca del ritardo |
| `max_projection_phase_bound_rad` | Massimo bound di contaminazione `asin(epsilon)` |
| `min_coverage_fraction` | Frazione minima di banda realmente supportata |
| `max_range_error_m`, `max_width_ratio` | Errore picco sintetico e rapporto larghezza SWW/singolo, <1 |
| `min_repeats` | Numero minimo ripetizioni/cicli, intero >=2 |

Compilare `criteria_approved_by`, `criteria_evidence` e `independent_path` con prova
e incertezza. L'incertezza deve essere inferiore al budget di ritardo e rispettare
`2*pi*span*incertezza <= max_phase_rms_rad`. Una geometria troppo incerta non consente
la verifica quantitativa sulla banda scelta. Il percorso noto deve stare entro la
ricerca configurata `0..2*max_range_m/c`.

Dopo il congelamento della sessione, copiare il `session_sha256` del dry-run nei
permessi. Ogni permesso deve indicare soltanto la propria fase in `authorized_stages`,
operatore, scadenza UTC con fuso, banda finita, minimo gain TX letto dal probe,
livelli/margine analogico, evidenze e settling misurato in `(0,1]` s. La durata
<=60 s include la conferma operatore. Burst necessari con il piano iniziale:
**12/20/30/40/12** per reference_pre/fixed/aba/sweep/reference_post. Budget cumulativo
per fase, non per frequenza. Non aumentare limiti senza riesaminare il banco.
Per CAL: `rx_echo_branch_terminated=true`; per fixed/aba/sweep:
`independent_path_present=true`. Con antenne TX collegate usare `summed_antennas`
e compilare le verifiche specifiche di TX, entrambi i rami RX e isolamento.

## Test 1 — identificazione e I/Q con timestamp, TX OFF

Banco iniziale con antenne, TX OFF, USB e FPGA pronti:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/preflight01 --through rx
```

Esegue suite offline, probe e 100 blocchi RX metadata. Per queste sole prove i KPI
possono ancora essere vuoti. `probe/device.json`: board, seriale, firmware/FPGA,
DLL, range, hash/layout header. Richiesti DLL/header 2.6.0 compatibili: format enum
**2**, struct x64 size **56**, offset timestamp/flags/status/count/reserved
**0/8/12/16/20**. Controllo nativo aggiuntivo se MSVC disponibile, TX OFF:

```powershell
cl /I"C:\Program Files\bladeRF\include" scripts\metadata_abi.c
.\metadata_abi.exe
```

Attesi SC16 int16 interleaved, **32768 campioni complessi per blocco**, timestamp
del primo campione, actual_count=32768, status zero e timestamp successivo uguale
al precedente + actual_count. La coda oltre actual_count non viene letta/salvata.
Il reader usa RAM limitata e salva dopo stop RX, senza JSON durante l'acquisizione.
PASS: continuità/count/status/rate/centri corretti e nessun componente con modulo
>=2028. `rx_checks.json` include potenza digitale, headroom e ADC. FAIL blocca TX;
nessun fallback a formato legacy senza timestamp. Metadata prova continuità RX,
non fase LO o epoch condivisa TX/RX; `get_timestamp()` da solo non basta.
Output: `rx/capture/iq/*.npy`, `manifest.json`, `rx_checks.json`. Prefisso e buffer
fallito sono conservati quando salvabili. DLL/ABI => verificare x64/versioni;
open => driver/USB/accesso esclusivo; FPGA assente => caricamento; overrun => USB3,
contesa CPU, buffer/rate; clipping TX OFF => interferenti/livelli esterni.
Correggere e riprovare in un nuovo run.

## Test 4 — estrazione del riferimento complesso

Precede 2/3 per fornire il template misurato. TX OFF: terminare ramo eco RX a 50 ohm,
ramo diretto invariato. Servono ora KPI e percorso indipendente pianificato;
altrimenti il collaudo qualificato è BLOCKED. Sessione congelata, run nuovo:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/session01 --through reference_pre --confirm-tx --authorize-tx reference_pre --permits captures/monday/permits
```

Ripete offline/probe/RX, poi tre burst per frequenza. Ogni burst: **un solo chirp**
e due campioni complessi finali zero. RX continuo tra burst a frequenza fissa;
retune/restart espliciti tra visite. Concatena buffer contigui prima di correlare,
recupera impulsi a cavallo dei buffer e rifiuta quelli tagliati, senza zero-fill.
Registra fase grezza e indice RX. PASS: numero esatto impulsi, confidence nei limiti,
forma ripetibile entro `max_reference_shape_error`, integrità RX e livelli validi.
È PASS del riferimento, non G1. Media CAL da soli I/Q misurati allineati scalarmente;
il chirp ideale serve a detection/matched filter, non sostituisce CAL.
Output `reference_pre/`: capture, `tx_events.json`, `tx_waveform.npy` con hash,
`reference_metrics.json`, `measured_reference_transfer.npz` (I/Q e H=R/X complessi),
`reference_transfer.png`. Attesi forma stabile e assenza di null profondi; fase
fra retune può variare. FAIL => controllare SNR, attenuazione, livelli, cavi,
settling e USB; nessun aumento automatico della potenza.

## Test 2 e 5 — fase fissa e separabilità sul singolo RX

TX OFF: ripristinare antenna RX e percorso noto, cavi/gain/rate invariati;
confermare i collegamenti al prompt:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/session01 --resume --through fixed --confirm-tx --authorize-tx fixed --permits captures/monday/permits
```

Venti burst alla prima frequenza, senza retune/restart interno. Pubblicare la fase
grezza anche se instabile. Un salto comune può essere correggibile: il criterio
critico è la fase dell'eco indipendente rispetto al riferimento.
Ricerca cieca del ritardo positivo, senza passare il ritardo noto al fit.
Due coefficienti complessi su template diretto misurato e copia ritardata;
quantità: correlazione `rho`, condizionamento Gram `(1+rho)/(1-rho)`, inflazione
varianza `1/(1-rho²)`, SNR coefficienti, residuo e bias della semplice proiezione.
Per `epsilon=|a_proiezione/a_joint-1|<1`, bound `asin(epsilon)`. Rho prossimo a 1
implica separazione non identificabile: STOP, nessun secondo RX inventato.
Il bias è condizionato al modello; residuo elevato invalida la stima.
Confrontare il ritardo cieco con quello indipendente. Fase da verificare:
`arg(a_eco/a_ref)+2*pi*fc*ritardo_noto`; una sola costante del riflettore/coupler
stimata qui viene congelata per A-B-A e sweep. Non si adatta una fase per frequenza,
né ritardo noto o pendenza di fase per ottenere PASS.
PASS: ripetizioni sufficienti, condition/SNR/residuo nei KPI, errore ritardo <=
incertezza + `max_delay_error_s`, RMS/P95 fase entro soglie. Output `fixed/`: capture,
`observations.json`, `phase_metrics.json`, `phase_and_separation.png`. Attesi
ritardo stabile e fase corretta raccolta attorno alla costante. Risultati calcolabili
e grafico restano diagnostici anche su KPI FAIL. Eco assente => SNR FAIL;
coincidente => condition FAIL; multipath/dispersione => residuo FAIL; lag al bordo
=> fit non affidabile; fase instabile => geometria, timing, clock e rami RF.
Nessuna A-B-A senza PASS.

## Test 3 — retune A–B–A

Banco identico a fixed, antenne e bersaglio immobili:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/session01 --resume --through aba --confirm-tx --authorize-tx aba --permits captures/monday/permits
```

Dieci cicli **2414 -> 2426 -> 2414 MHz**, un burst per visita. Il driver riavvia
tra visite: si qualifica questa sequenza, non la sola PLL con stream invariato.
Settling misurato nel permesso; non si presume LO lock. PASS: gate precedenti
con costante congelata da fixed, più differenza circolare fra le due A di ogni
ciclo <= `max_phase_p95_rad`. Differenze A grezze diagnostiche, possono essere
non nulle se recuperate sull'eco. Output `aba/`: capture, observations, phase_metrics
con `raw_A_return_statistics` e `corrected_A_return_delta_rad`, grafico fase.
Atteso: salti grezzi corretti sull'eco. FAIL => STOP prima sweep; verificare settling,
centri TX/RX, stabilità riferimenti, fase di ramo, sample-index e movimento.

## Test 6 — calibrazione complessa per sottochirp

Banco invariato; dieci impulsi per ciascuno dei quattro centri:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/session01 --resume --through sweep --confirm-tx --authorize-tx sweep --permits captures/monday/permits
```

Acquisizione per frequenza, poi raggruppamento per indice ripetizione: non sono
dieci sweep rapidi interlacciati. Scena stazionaria per l'intera sessione.
Output `sweep/` come fixed. Tutti i sottochirp devono superare i KPI; nessuno viene
rimpiazzato/omesso per ottenere uno stack completo.
TX OFF: terminare di nuovo ramo eco RX, preservando gli altri elementi:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/session01 --resume --through reference_post --confirm-tx --authorize-tx reference_post --permits captures/monday/permits
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/session01 --resume --through validate
```

`validate` non apre radio. Rilegge raw/hash, origine HARDWARE, codice/sessione e
prove indipendenti. Controlla shape complessa CAL pre/post dopo sola compensazione
scalare e bound di contaminazione <= `max_projection_phase_bound_rad`.
Output CAL in `reference_post/`, risultati complessivi in `sequence.json`.
Endpoint da soli non osservano derive che si annullano fra CAL; l'eco noto simultaneo
verifica la correzione nelle acquisizioni effettivamente esaminate.
Per sottochirp sottrarre dagli I/Q ricevuti il riferimento misurato moltiplicato
per coefficiente stimato; dividere il residuo per il trasferimento complesso
`a_ref*R(f)/X(f)` solo sui bin supportati. Corregge ampiezza/fase/forma nell'ipotesi
di trasferimento comune verificata dal modello. Gli echi del profilo non vengono
generati dal ritardo stimato; non è una normalizzazione scalare universale.
PASS: KPI precedenti, CAL stabile, epsilon<1 e contaminazione entro budget.
Ambito `STATIONARY_SINGLE_KNOWN_PATH_REFERENCE_RELATIVE_ONLY`. Percorso mancante,
incertezza eccessiva o residui non modellati => BLOCKED/FAIL, nessun G1 generale.

## Test 7 e 8 — stacking reale e risoluzione

Elaborazione offline, nessun collegamento RF richiesto, TX OFF:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/session01 --resume --through stack
```

Ripete qualifica dai raw verificati, senza fidarsi di un JSON PASS modificato.
Riusa Algorithm 1 esistente: griglia RF/CZT sui centri reali, somma complessa,
GLS configurato, IFFT. Nessun simulatore nel percorso bench. SIMULATED, cattura
incompleta o hash errato sono rifiutati; buchi spettrali mascherati. Raw immutati,
eventuale ricodifica del crop SC16 misurata nel report.
Confronta primo sottochirp e sintesi degli stessi I/Q, stessa finestra/scena.
`c/(2B)` teorico per 12/48 MHz: circa **12,49/3,12 m**; non confonderlo con larghezze
misurate -6 dB. Zero-padding non migliora risoluzione fisica. Misura larghezze reali,
errore picco, PSL e copertura. L'eco deve essere abbastanza distante dal riferimento
da osservare entrambi i lati della mainlobe del singolo chirp.
PASS per ripetizione: copertura >= `min_coverage_fraction`, errore sintetico <=
`max_range_error_m`, larghezze finite, rapporto SWW/singolo <= `max_width_ratio`.
Output `stack/wide_NNN.npz`, `single_NNN.npz`, `resolution_metrics.json`,
`qualification.json`, `single_vs_synthetic.png`. NPZ: spettro complesso, supporto,
asse e profilo misurati; coefficienti in observations, trasferimenti nei CAL NPZ.
Atteso picco alla distanza relativa nota e mainlobe più stretta. FAIL => controllare
fase, CAL, null/buchi, alias, clipping, lag e modello; non aggiustare lo spettro.
NPZ e metriche calcolabili salvati prima dei KPI. **Due bersagli: NOT_TESTED**,
serve distinta prova fisica; range assoluto/epoch TX-RX restano UNVERIFIED.

## Sequenza consecutiva autorizzata e ripresa

Dopo compilazione e verifica permessi, comando completo su run nuovo:

```powershell
.\.venv\Scripts\python.exe -m sww.bench --session captures/monday/session.json --run captures/monday/full01 --through stack --confirm-tx --authorize-tx reference_pre fixed aba sweep reference_post --permits captures/monday/permits
```

Si ferma per conferme dei cambi fisici e al primo FAIL/BLOCKED. Preferire invocazioni
progressive per le prime misure. Equivalente PowerShell, TX OFF:

```powershell
.\scripts\monday_test.ps1 -Session captures/monday/session.json -Run captures/monday/preflight02 -Through rx
```

Non sostituire con `sww.cli loopback/phase-stability/frequency-sweep`: restano
diagnostici per loopback chiuso, senza qualifica indipendente. Il vecchio
`sww.cli stacking --backend hardware` non acquisisce questo banco.
`--resume` riusa solo PASS con evidenze invariate. Nuove fasi TX richiedono ancora
flag/permesso/conferma. BLOCKED prima della cartella prova è riprendibile dopo
compilazione permesso. FAIL con cartella/raw creati richiede **un nuovo run**,
preservando il precedente. Cambiare codice/sessione richiede un nuovo run:
preflight con KPI vuoti e sessione finale vanno tenuti distinti. Nessun retry TX
automatico. Stack già PASS conservato, senza sovrascriverlo.

## Verifica offline e fonti

```powershell
.\.venv\Scripts\python.exe scripts\validate_offline.py --report reports/run_monday_offline_check
.\.venv\Scripts\python.exe -m sww.bench --session configs/monday_session_TEMPLATE.json --through stack --dry-run
```

Nuove prove: boundary buffer, gap/count/status/clipping, permesso antenne/scadenza,
stop prima TX, errori USB, fase/ritardo indipendenti errati, separazione degenerata,
stop prima sweep, stack/replay di fixture e rifiuto simulati/corrotti. Fixture solo
test, non risultati hardware. Regressioni legacy/SWW incluse. Evidenze in
`BladeRF_FMCW_Demo/reports/run_monday_preparation_20261010/`.
Fonti: PDF locale Prager et al., *Ultrawideband Synthesis for High-Range-Resolution
Software-Defined Radar*, Algorithm 1, eq. 5 e 8–16;
[Nuand RX metadata 2.6.0](https://www.nuand.com/libbladeRF-doc/v2.6.0/sync_rx_meta.html)
per timestamp primo campione, actual_count e overrun;
[Nuand TX metadata 2.6.0](https://www.nuand.com/libbladeRF-doc/v2.6.0/sync_tx_meta_bursts.html)
per burst/TX_NOW e zeri finali. Le API non garantiscono la fase LO della scheda.
