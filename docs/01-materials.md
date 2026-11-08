**Português (Brasil)** · [English](en/01-materials.md)

# 1. Materiais

Tudo aqui é material comum de hobby. Os valores são os da nossa montagem
que funciona. Quando um valor é só um ponto de partida que você deve
ajustar, a tabela avisa.

## Lado do rádio

| Qtd | Peça | Observações |
|---|---|---|
| 1 | Rádio portátil Baofeng com conector Kenwood de 2 pinos (K1) | Usamos um **BF-777S** (programa com o protocolo do BF-888). Rádios da classe UV-5R/BF-888 têm a mesma pinagem K1. |
| 1 | Plugue ou cabo K1 | Mais fácil: sacrificar um fone/headset barato de Baofeng e cortar o cabo. Ou comprar plugues avulsos de 2,5 mm + 3,5 mm. Headsets baratos costumam deixar sem ligação os condutores de dados de programação e de +V; você só precisa deles para [programar](09-chirp-programming.md). |
| 1 | Um segundo rádio no mesmo canal | Para testar: você fala nele e o Baofeng da base recebe. Programe **os dois** fora dos canais de fábrica ([por quê](security-and-privacy.md)). |

## Placa de interface

| Qtd | Peça | Onde vai | Por quê |
|---|---|---|---|
| 1 | Microcontrolador com Wi-Fi | — | Veja [escolhendo a placa](02-choosing-a-board.md). |
| 1 | Eletrolítico de 2,2 µF | RX: S+ do alto-falante → ADC | Bloqueia o DC do rádio; deixa passar o áudio. |
| 1 | 33 kΩ | RX: em série com o pino do ADC | Limita a corrente; define o nível. **Ajuste este** se o RX saturar ou ficar baixo demais. |
| 2 | 27 kΩ | RX: divisor de polarização 3,3 V / GND | Centra a entrada do ADC em ~1,65 V. Mantenha os dois iguais. |
| 1 | 33 kΩ | TX: pino do DAC/PWM → nó de TX | Perna de cima do atenuador. |
| 1 | 680 Ω | TX: nó de TX → GND | Perna de baixo; atenuação de ~1:50 até nível de microfone. |
| 1 | Eletrolítico de 2,2 µF | TX: nó de TX → M+ do microfone | Acoplamento. A polaridade importa: **o + vai para o rádio** (a entrada de microfone tem polarização DC positiva). |
| 1 | BC548 (ou qualquer NPN pequeno: 2N3904, BC547, 2N2222) | PTT | Leva o PT ao terra. **Nunca ligue o PT direto num GPIO.** |
| 1 | 2,2 kΩ | GPIO → base do NPN | Resistor de base. |
| 1 | Protoboard ou placa perfurada, jumpers | — | Nossa montagem usa uma protoboard de 30 linhas. |

Opcional, para placas só com PWM (como o ESP32-C3): um pequeno passa-baixas
RC na saída PWM, depois da rede 33k/680R, se você ouvir chiado da
portadora. Nossa montagem com C3 usa uma portadora de 156,25 kHz na mesma
rede 33k/680R/2,2 µF da montagem com DAC, sem filtro extra.

Opcional, para [programação](09-chirp-programming.md): um jumper do anel
do plugue de 2,5 mm (saída de dados do rádio) até um pino RX de UART. Os
níveis precisam ser compatíveis com 3,3 V. Confira os seus com um
multímetro antes de ligar.

## Lado do computador

- **Um computador com Python 3.10+ e acesso USB ao ESP32**, para compilar
  e gravar o firmware. Se usar uma VM, passe o dispositivo USB para ela.
- **Uma máquina ou VM Linux com o Hermes Agent e acesso de rede ao ESP32.**
  O Linux é necessário para o áudio ALSA (`alsa-utils` e módulo de kernel
  `snd-aloop`), não para o sistema do PC físico. Também usamos `tmux` e
  `ffmpeg` (veja [áudio no host](05-host-audio.md)). Rodamos o Hermes numa
  VM libvirt para isolá-lo do host.
- Contas/chaves dos provedores de LLM e STT que você configurar no Hermes.
  O Edge TTS (padrão do Hermes) não precisa de chave.

## Ferramentas

- Multímetro: confira a polarização (~1,65 V), a polaridade e a pinagem do plugue.
- Ferro de solda, se usar placa perfurada ou cortar o cabo de um headset.
- Opcional: um osciloscópio agiliza o ajuste do nível de TX, mas o
  diagnóstico serial do [capítulo 4](04-firmware.md) basta.
