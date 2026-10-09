# bladeRF FMCW Demo

This is a real-hardware, experimental Nuand bladeRF FMCW demo. It detects the
connected radio, configures live RX, generates a low-amplitude SC16_Q11 chirp,
supports guarded full-duplex TX/RX, plots real I/Q and experimental range FFTs,
captures a relative leakage background, and saves real acquisitions.

TX is disabled on launch. The program will not enable it unless the operator
checks the RF authorization/antenna/safe-separation confirmation and accepts a
second explicit dialog.

## Setup

From this folder, create the isolated environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The code loads the installed 64-bit Nuand DLL from
`C:\Program Files\bladeRF\x64\bladeRF.dll`. If it is installed elsewhere,
set `BLADERF_DLL` to the full compatible DLL path before launching.

## Verify device detection first

```powershell
.\.venv\Scripts\python.exe main.py --probe
```

This opens the actual device, reports the board, serial, FPGA size and status,
firmware, FPGA, libbladeRF versions, and the hardware-reported RX ranges. Any
libbladeRF API error is shown verbatim and the command exits; it does not
silently use a simulation or a fallback device.

Run the explicit RX-only hardware smoke test (it configures 2.4 GHz / 20 MS/s /
15 MHz / 20 dB and receives one 8192-sample block):

```powershell
.\.venv\Scripts\python.exe rx_smoke_test.py
```

## Use the GUI

```powershell
.\.venv\Scripts\python.exe main.py
```

1. Click **CONNECT** and verify the board, FPGA, and all reported RX/TX ranges.
2. Enter a start frequency and bandwidth supported by hardware. The program
   tunes RX/TX to the calculated LO centre frequency.
3. Use **START RX** first to inspect genuine I/Q and saturation without RF
   emission.
4. Only after confirming legal transmission, antenna compatibility, and safe
   antenna separation, tick the confirmation then use **START TX/RX**. A second
   dialog is required before RF is enabled.
5. **CALIBRATE LEAKAGE** stores a real raw spectrum as a relative background.
   **SHOW RAW** switches displays; **SAVE** writes I/Q, configuration, and the
   current profile under `captures`.
6. Use **STOP** before changing hardware or unplugging the radio.

RX tuning or hardware capability reporting does not establish antenna
compatibility or legal transmit authorization.

## TX level and RX ADC monitor

- **TX Hardware Gain [dB]** is a slider constrained to the gain range queried
  from the connected device. It is a hardware gain setting, **not calibrated
  output power in dBm**. The selected gain is applied when TX starts; changing
  it during TX calls `bladerf_set_gain(TX0)` safely between TX transfers.
- **TX Digital Amplitude [%]** is a separate 1–100% waveform scale. It defaults
  to 5% (`0.05` full scale) and is applied to the generated chirp before SC16_Q11
  conversion. Changing it during TX atomically updates the repeated waveform.
- RX and TX gains are reset to the hardware-reported minimum on connection.
- The RX ADC monitor is computed from real, pre-DSP SC16_Q11 I/Q samples.
  `0 dBFS` equals a full-scale *individual I or Q component* (`|2048|`);
  RMS is calculated over all I/Q components; clipping is the percentage at or
  above 99% full scale. Headroom is digital peak margin only.
- Green is below −12 dBFS, yellow is −12 to −3 dBFS, red is above −3 dBFS, and
  any near-full-scale component is **CRITICAL**. Eight consecutive monitor
  updates (about one second) with clipping automatically disable TX while
  preserving RX for diagnosis.

The monitor is not RF power in dBm and is not guaranteed hardware protection:
analogue compression or damaging RF input can occur before ADC clipping.

## Technical notes

- The wrapper is derived from the locally installed Nuand `libbladeRF.h` for
  libbladeRF 2.6.0. It uses capability queries, `bladerf_sync_config`,
  `bladerf_enable_module`, `bladerf_sync_rx`, and `bladerf_sync_tx`.
- Samples are decoded as interleaved signed `int16` I/Q and normalized by 2048,
  exactly as specified for `BLADERF_FORMAT_SC16_Q11`.
- The header exposes timestamp and metadata APIs, and the interface exposes the
  documented coarse `bladerf_get_timestamp` call. This version uses ordinary
  SC16_Q11 synchronous transfers rather than scheduled metadata transfers, so
  the UI explicitly labels every range result **UNSYNCED**. Do not treat target
  peaks as validated absolute distance measurements.
- The TX chirp chart is a **generated reference waveform**, not a measured RF
  output. FFT zero padding is not used to claim extra range resolution.
- Automated tests use synthetic signals only to validate deterministic waveform
  and DSP mathematics. Live GUI plots and saved captures always come from the
  connected radio.
