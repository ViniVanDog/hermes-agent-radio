**Português (Brasil)** · [English](en/02-choosing-a-board.md)

# 2. Escolhendo a placa

Montamos isto com o que estava na bancada: primeiro um **ESP32 DevKitC V4**
(ESP32 clássico), depois um **ESP32-C3-MINI-1**. Os dois funcionam e os
dois firmwares estão neste repositório. Nenhum deles é especial. O circuito
analógico do [capítulo 3](03-wiring.md) e os dois protocolos UDP do
[capítulo 4](04-firmware.md) são a interface de verdade. Outra placa exige
portar o firmware; o lado do host não se importa com qual placa manda os
pacotes.

## O que qualquer placa precisa fazer

| Requisito | Por quê | ESP32 clássico | ESP32-C3 |
|---|---|---|---|
| Wi-Fi 2,4 GHz + UDP | O áudio vai para o PC em pacotes UDP de 20 ms | ✓ | ✓ |
| ADC amostrado a **16 kHz** constantes, com o Wi-Fi ligado | Áudio de RX. Precisa de DMA ou timer de hardware; um laço de `analogRead()` em software oscila com a carga do Wi-Fi | ADC1 por DMA a 20 kHz, mantendo 4 de cada 5 amostras | igual |
| Um canal de ADC que funcione com o Wi-Fi ligado | No ESP32 clássico, **o ADC2 fica inutilizável enquanto o Wi-Fi roda** | Só ADC1 (GPIO32–39) | ADC1 (GPIO0–4) |
| Uma saída de TX quase analógica, atualizada a 16 kHz | Voz na entrada de microfone do rádio | DAC de 8 bits (GPIO25) | Sem DAC: PWM de 8 bits, portadora de 156,25 kHz |
| Um GPIO livre | PTT por um transistor NPN | ✓ | ✓ |
| Opcional: uma UART cujo TX possa ser invertido | [Programação do rádio](09-chirp-programming.md) pelo mesmo NPN | não implementado | ✓ UART1, TX invertido |
| Lógica de 3,3 V, serial USB | Gravação, diagnóstico, ponte de programação | ✓ | ✓ |

Se você portar para outro MCU (RP2040 W, ESP32-S3, …), mantenha:

- PCM de 16 kHz, 16 bits com sinal, mono, 320 amostras por pacote;
- PTT **desligado** no boot, antes do Wi-Fi subir, e liberado em todo caminho de erro;
- o watchdog do PTT: sem áudio de TX por 2 s, ou 60 s no total, libera o PTT.

## As duas placas que usamos

### ESP32-C3-MINI-1 (montagem atual, `firmware/BaofengRxC3`)

| GPIO | Função |
|---|---|
| GPIO3 | Entrada de áudio de RX (ADC1_CH3), vinda do circuito de condicionamento do S+ |
| GPIO1 | Saída de áudio de TX: PWM de 8 bits, portadora de 156,25 kHz, na rede 33k/680R/2,2 µF |
| GPIO10 | Base do NPN (via 2,2k): PTT, **e** TX invertido da UART durante a programação |
| GPIO4 | RX da UART de programação, do anel de 2,5 mm do rádio (opcional) |

### ESP32 DevKitC V4 (primeira montagem, `firmware/BaofengRx`)

| GPIO | Função |
|---|---|
| GPIO34 | Entrada de áudio de RX (ADC1_CH6) |
| GPIO25 | Saída de áudio de TX: DAC1, atualizado por um timer de hardware de 16 kHz |
| GPIO26 | Base do NPN (via 2,2k): PTT |

No ESP32 clássico, o I2S0 pertence ao driver de ADC com DMA do RX. O TX
escreve no DAC1 diretamente, a partir de outro timer, então as duas
direções nunca disputam um periférico.

Compile qualquer uma com o `tools/flash.py` ([capítulo 4](04-firmware.md)):
`--target c3` para o C3, ou sem `--target` para o ESP32 clássico.
