# Fonti e adattamenti verificati il 9 ottobre 2026

- PDF locale Prager et al., DOI **10.1109/TIM.2019.2937423**, 15 pagine, letto
  integralmente prima dell'implementazione. Pagine renderizzate per verificare
  Algorithm 1, eq.17-24, geometria/SAR e tabelle. Il PDF originale resta invariato.
- Roadmap locale v0.3 letta integralmente; i gate hardware non sono approvati dai test.
- Nuand RX metadata 2.6.0:
  https://www.nuand.com/libbladeRF-doc/v2.6.0/sync_rx_meta.html
- Nuand burst TX 2.6.0:
  https://www.nuand.com/libbladeRF-doc/v2.6.0/sync_tx_meta_bursts.html
- Header ufficiale versionato:
  https://raw.githubusercontent.com/Nuand/bladeRF/libbladeRF_v2.6.0/host/libraries/libbladeRF/include/libbladeRF.h
- Header installato `C:\Program Files\bladeRF\include\libbladeRF.h`, SHA256
  `483f163c43fc4bdc9f7dd985e7bb5448bc1c68a4aa463b4d552e7df408e6c3fa`.
  DLL letta senza aprire la radio: versione **2.6.0 (2.6.0)**.

## Punti da non trasferire implicitamente

1. Algorithm 1, passo 2, stampa `Bs >= Bi`; sezione IV, fig.6 e prove al 96/80/50%
   usano anche `Bs < Bi`. Non imponiamo l'ineguaglianza stampata come vincolo
   universale. Entrambi i casi sono configurabili e le maschere effettive sono salvate.
2. Gli esperimenti usano N/Bs/spacing diversi e la fig.19 ha indicazioni non del tutto
   uniformi con il testo. I risultati prodotti dichiarano i propri parametri; non
   vengono etichettati come riproduzione numerica esatta delle tabelle II-VIII.
3. Le modifiche AD9361 MCS, RFNoC e i due RX della E312 non esistono automaticamente
   sulla bladeRF 1.0/LMS6002D. Nessun registro/FPGA nuovo e stato inventato.
4. Il paper indica la rete esterna di switching per SDR single-channel (p.8).
   Questo non dimostra che una specifica sequenza bladeRF conservi la fase.
5. Full-complex deembedding CAL e GLS regolarizzato sono adattamenti espliciti
   dell'algoritmo ideale, per evitare guadagno infinito e false correzioni nei null.
6. La formula SAR e implementata con una convenzione di fase globale per pixel
   esplicita e dimensionale; vedere README, non dedurre coerenza da una sola immagine.
7. Le specifiche dei componenti nella roadmap non sono limiti approvati della catena
   reale: bilancio di potenza e qualifica RF restano decisioni di banco.
