# SWW Prager - moduli e convenzioni

Entry point: `python -m sww.cli --help`, da `BladeRF_FMCW_Demo`.
La demo `main.py` e i suoi controlli TX/RX non sono stati modificati. I nuovi
backend riusano `BladeRFInterface`, conversione RX e monitor ADC legacy.

## Modello matematico

Tutte le grandezze sono SI; `exp(+j2*pi*f*t)` e il segnale analitico positivo.
Il chirp e `A(t)*exp(j*pi*(Bi/Tp)*(t-Tp/2)^2)`, indicizzato da zero. La traslazione
comune del tempo di chirp si cancella in `Z*conj(V)`. Un bersaglio monostatico e
`a*w(t-tau)*exp(-j2*pi*fc*tau)*H(fc+f)*exp(j*phi)`, con `tau=2R/c`.
Il riferimento cablato usa il proprio ritardo **one-way** `tau_ref`; non moltiplicarlo
di nuovo per due. Geometrie bistatiche/media diversi richiedono un modello dedicato.

Il simulatore applica ritardi Shannon mediante DFT con guardia 16x, limita il supporto
del record e applica passband, ritardo di gruppo, dispersione, PLL casuale e AWGN.
Il test contro una somma sinc indipendente fissa il limite dell'approssimazione
finita; non e un simulatore elettromagnetico di antenne, multipath esteso o Doppler
intra-chirp. La quantizzazione SC16 e parte del dataset, con clipping per componente.

## Algorithm 1

1. Correlazione lineare complessa `d_n=z_n * conj(v_n[-t])`, non la vecchia Range FFT
   dechirp. `matched_filter()` restituisce lag da `1-Lv` a `Lz-1`, senza alias circolare.
2. La DFT e forward non normalizzata; IFFT usa `1/L`. Per `Lfft >= Lz+Lv-1` il prodotto
   `Z*conj(V)` e esattamente lo spettro della correlazione lineare.
3. Una griglia RF comune ha `df=fs/Lfft`, riferimento RF al centro dello span completo,
   `nout` sufficiente a contenere tutte le bande piu zero padding. Le DFT locali
   vengono valutate via CZT alle frequenze effettive della griglia: e la stessa
   trasformata della sequenza zero-padded, senza roll/arrotondamenti dei centri.
4. Maschera rettangolare semiaperta `[fc-Bs/2,fc+Bs/2)`; gli spettri sono sommati
   complessamente. In overlap, `overlap_normalize=false` riproduce la somma del paper;
   `true` divide per somma dei pesi validi. `step_weights` e esplicito, non SNR inventato.
   `excluded_bands_hz` maschera bande RF; i gap rimangono nulli.
5. Il profilo e `fftshift(ifft(ifftshift(D)))*nout/Lfft`, con normalizzazione di ciascun
   sottimpulso all'energia del template. Questa scala preserva l'ampiezza dopo padding;
   la somma dei passi non viene normalizzata implicitamente a uno.
6. `tau[m]=(m-nout/2)/(nout*df)`, `R=c*tau/2`; il profilo mantiene
   `exp(-j2*pi*f_ref*tau)`. Periodo non ambiguo `c/(2df)`. Range negativo non significa
   distanza fisica negativa: e il dominio ritardi firmato/circolare della IFFT.
   Risoluzione ideale `c/(2B_span)` e passo del grafico sono grandezze differenti.

I test di stacking confrontano le frequenze contigue, nonuniformi, overlap e gap
con una somma DFT diretta indipendente, senza usare CZT/stacking come oracle.

## Calibrazione e GLS

`H_pre=C_pre/(X*a_ref*exp(-j2*pi*f_RF*tau_ref))`, idem POST. Interpolazione delle
ampiezze e della differenza di fase principale, con controlli SNR/supporto/deriva.
Si divide l'eco per H e si comprime con il template digitale. Questo e l'adattamento
di calibrazione complessa dell'opzione phase-only descritta dopo Algorithm 1.
Presuppone trasferimento RF comune e trasferimento relativo CAL/RADAR noto;
un'antenna/switch/cavo diverso non e calibrato per magia. L'interpolazione degli
endpoint non osserva salti reversibili fra le due CAL.

Il single-RX sommato stima `a_hat=<v,y>/<v,v>` e sottrae la copia stimata del
riferimento, poi usa la correzione scalare. Gli echi introducono
`epsilon=sum <v,echo_i>/<v,v>`; per `|epsilon|<1` il bound di fase e
`asin(|epsilon|)`. `contamination_study` misura bias e fase/amplitudine di un eco
indipendente. A ritardo coincidente la separazione non e identificabile; per altri
ritardi il report non presume automaticamente identificabilita pratica.
La correzione scalare non elimina arbitraria dispersione RF.

GLS_RAW_PAPER (`gls_mode=paper`) applica M/W sul supporto valido e normalizza al
bin di DC sintetico. GLS_STABLE (`stable`) usa `M*conj(W)/(|W|^2+lambda*max|W|^2)`,
maschera i null e limita il guadagno dopo normalizzazione. Nessuna inversione
ricostruisce frequenze assenti. `range_window` modella M; `pulse_window` pesa A(t),
e sono tecniche diverse. L'esperimento include rettangolare, Tukey, Hamming,
nonuniformita e overlap separatamente. Miglioramenti non monotoni sono riportati.

## Metriche e validita

PSF dalla risposta di riferimento a ritardo zero; larghezza a -6 dB di ampiezza con
crossing lineari, PSL esterno ai primi minimi locali. PGL usa finestre +/-15% attorno
ai multipli di `c/(2*median(step))`: e una definizione diagnostica esplicita, non
l'estrazione identica delle tabelle del paper per ogni possibile piano nonuniforme.
L'esperimento due target richiede due massimi distinti entro 0.4 m dalle distanze
note, entrambi >=-6 dB del massimo e valle >=3 dB. Il residuo spettrale confronta
solo bin validi con ideale >10% del picco: RMS <0.03 rad nel test deterministico.
Queste sono tolleranze **solo di simulazione**, fissate per questo esperimento.

Un profilo con CAL valida e `SIMULATED_ENDPOINTS_ONLY`. Solo il confronto con
echi ideali indipendenti puo assegnare `SIMULATED_VERIFIED`; nessuna funzione
assegna `COHERENT_UWB` a dati reali durante questa preparazione. I test introducono
salti nascosti, segno errato, reset digitale e ritardo CAL errato per rilevare falsi PASS.

## Motion/SAR

Eq.17-20: `dR=norm(p_n-p_target)-norm(p_0-p_target)`; moltiplicazione dell'eco
compresso per `exp(+j4*pi*f_RF*dR/c)`. Il segno positivo deriva dal coniugato del
matched filter modificato. Le posizioni sono ingressi espliciti, mai inferiti da IQ.
La validita per bersagli fuori dal punto di riferimento dipende dall'approssimazione
eq.20; `scene_approximation_error()` ne quantifica l'errore geometrico.

Backprojection monostatica offline: interpolare il profilo complesso a
`R=norm(pixel-position)` e sommare dopo `exp(+j4*pi*f_ref*R/c)`; il benchmark
incoerente somma le magnitudini. L'uscita conserva una fase globale per pixel
diversa dalla convenzione ground-range residua eq.28-30 del paper, con identica
magnitudine focalizzata. L'esponente deve essere adimensionale: l'implementazione
usa metri, Hz e c, senza trasferire letteralmente ambiguita tipografiche del testo.
`phase_verified=True` e obbligatorio per il focussing coerente; nelle demo indica
esclusivamente la geometria/fase sintetica nota, non un gate hardware approvato.

## Contratti dei moduli

| Modulo | Input -> output e unita | Errori / evidenza offline | Prova hardware necessaria |
|---|---|---|---|
| config/types | YAML SI -> dataclass/hash; record raw int16 | NaN/limiti/schema/forme; test_math | Limiti e valori applicati reali |
| waveforms/simulation | piano/seed/pose -> chirp/IQ/truth | supporto/ritardi/SC16; test_math, test_stacking | Forma d'onda e risposta RF |
| matched_filter/stacking | IQ complessi -> D(f), d(t), R in m | DFT diretta, alias, fase, gap; test_stacking | Epoch, CAL/eco e distanze note |
| calibration/gls | CAL/template/maschere -> H, filtro, validita | null/SNR/drift/bias; test_calibration_gls | Trasferimenti dei rami, eco indipendente |
| timing/hardware | header/API -> metadata/raw/actual_count | ABI/status/mock; test_timing_sar_gui, test_hardware_mock_cli | DLL+FPGA reali, epoch e burst |
| acquisition/scheduler/rf_switch | piano/controller -> sequenza/log | fault, watchdog, disk, Ctrl-C; test_storage_safety | Switch scelti, readback, mute |
| storage | raw/config -> manifest/hash/replay | corruzione, traversal, no overwrite | Performance disco a rate reale |
| diagnostics/metrics/reporting | raw/profili -> SI, dBFS, rad, JSON/PNG | fase circolare, PSF/target; suite e CLI | KPI approvati e ripetizioni |
| motion/sar/offline_demos/sar_replay | pose/scene -> profili/immagini | segno, errore posizione, focus; test_timing_sar_gui | Pose e fase inter-sweep misurate |
| cli/gui/experiments | comandi/capture -> report/6 tab | dry-run senza DLL, GUI offscreen, replay | Integrazione con dataset reali |

Storage: cartelle append-only; `.npy` int16 interleaved I/Q, timestamp FPGA solo se
misurato (i timestamp simulati sono etichettati), metadati del backend, config/hash
e stato finale. `manifest.tmp -> manifest.json` e atomico, con retry finiti per lock
transitori Windows. I dati non vengono sostituiti da soli plot o magnitudini.

## Riproduzione rapida

```powershell
.\.venv\Scripts\python.exe scripts/validate_offline.py
.\.venv\Scripts\python.exe -m sww.cli simulate --config configs/simulator.yaml --study
.\.venv\Scripts\python.exe -m sww.cli gui --config configs/simulator.yaml
```

Le prove reali sono descritte in `../../MONDAY_TEST_PLAN.md`. Fonti primarie e
incongruenze del paper sono registrate in `SOURCES.md`.
