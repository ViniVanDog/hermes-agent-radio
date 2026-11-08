**Português (Brasil)** · [English](en/09-chirp-programming.md)

# 9. Lendo a memória do rádio (CHIRP) pelo ESP32-C3

O firmware do C3 pode virar uma ponte de programação USB ↔ rádio, para você
fazer backup dos canais do rádio sem um cabo de programação separado. O
[scanner](10-scanner-dashboard.md) usa a mesma ponte para resintonizar o canal 1.

Verificado num BF-777S que fala o protocolo do BF-888: o rádio respondeu
`P3107`, e duas implementações independentes (o driver do CHIRP e uma
implementação crua) devolveram a mesma imagem de 992 bytes.

## Ligações

Tudo isto já está ligado se você montou o jumper opcional de programação
do [capítulo 3](03-wiring.md):

| Contato do rádio | C3 |
|---|---|
| Anel do 2,5 mm (saída de dados do rádio) | GPIO4, RX da UART1 (níveis de 3,3 V) |
| Corpo do 3,5 mm (PT / entrada de dados do rádio) | Coletor do BC548 (GPIO10 → 2,2k → base) |
| Corpo do 2,5 mm (terra) | GND / emissor do BC548 |

O GPIO10 tem duas funções que nunca se sobrepõem: PTT na operação normal e
TX da UART1 durante a programação. O TX da UART é **invertido no firmware**
porque o transistor o inverte de novo. Com a programação em repouso o
GPIO10 fica em LOW, então o transistor fica desligado e o PT liberado.
Nunca segure o PTT durante o handshake.

## O que a ponte faz

Comando serial `b` (ou `q`, a variante de 1 s do scanner):

1. recusa se houver uma transmissão ativa ou na fila;
2. pausa a captura de áudio e suspende a tarefa de TX;
3. entrega o GPIO10 (TX) e o GPIO4 (RX) à UART1 a **9600 8N1**;
4. imprime `CHIRP READY`. Daí em diante o console USB (ainda a **115200**) leva bytes crus.

Depois de 15 s sem dados (1 s com `q`), ou 2 minutos no total, ela envia o
byte de saída do rádio, devolve o GPIO10 como PTT liberado, descarta
pacotes de TX velhos e retoma o áudio. Ela imprime
`CHIRP CLOSED; audio resumed; PTT OFF.` mais registros de bordas dos GPIOs
para diagnóstico.

Essa mensagem confirma que o **C3** fez a limpeza. Ela não prova que o
rádio saiu do modo de programação (veja abaixo).

## Baixe um backup

Feche todos os monitores seriais antes.

```bash
# com o driver do CHIRP (CHIRP instalado via pipx): salva .img, .bin, .csv e um log
~/.local/share/pipx/venvs/chirp/bin/python tools/chirp_download.py --port /dev/ttyUSB0

# implementação crua independente, só pyserial
.build-venv/bin/pip install pyserial
.build-venv/bin/python tools/chirp_download.py --raw --port /dev/ttyUSB0
```

Os arquivos vão para `diagnostics/chirp/`, ignorado pelo git. O auxiliar só
permite identificação, leitura de blocos, ACKs e saída. **Ele não consegue
gravar no rádio.** Ele lida com o reset do CH340 ao abrir a porta
reenviando `b`, tenta de novo a cada 2 s por até 60 s quando recebe
`CHIRP BUSY`, e espera o `CHIRP CLOSED` antes de fechar a porta.

O aplicativo CHIRP de desktop não consegue usar a porta diretamente.
Precisaria de um proxy que envie `b` e mantenha o lado do host a 115200.

## O protocolo (família BF-888)

```text
host → radio  02            wait 100 ms
host → radio  "PROGRAM"     (50 52 4f 47 52 41 4d)
radio → host  06            ACK
host → radio  02
radio → host  "P3107" + version bytes (50 33 31 30 37 f7 00 00)
host → radio  06
radio → host  06
read:  52 hi lo 08  →  57 hi lo 08 + 8 data bytes, then ACK; addresses 0x0000–0x03D8
exit:  45 ("E")
```

Quando deu certo, o LED do rádio piscou verde por cerca de um segundo. As
tentativas que falharam não tiveram essa piscada e não devolveram ACK. É
uma pista física útil, não uma medição.

## Se o rádio parecer preso no modo de programação

O byte de saída não recebe resposta nesse protocolo. Se o rádio continuar
estranho, envie `E` pela ponte (sem `02`/`PROGRAM`), deixe a ponte fechar e
confira o rádio. Se ainda estiver preso, **desligue e ligue o rádio**:
resetar o C3 não reseta um rádio alimentado por bateria.

## Notas honestas

- A nossa primeira versão da ponte não teve resposta do rádio com nenhuma
  das implementações. A versão seguinte acrescentou o registro de bordas no
  GPIO10/GPIO4, e então os dois downloads funcionaram. A configuração de
  interrupção desse registro também ativa o buffer de entrada de cada pino
  monitorado, inclusive o pino de TX da UART, então a mudança não foi só de
  log. **A causa das falhas iniciais nunca foi isolada.** Mantenha a versão
  com registro como está, a menos que faça uma comparação controlada.
- O `--slow-handshake` (10 ms entre os bytes de `PROGRAM`) não ajudou. As
  leituras bem-sucedidas usaram a temporização normal.
- Os registros de bordas mostram atividade nos pinos do C3, não a qualidade
  do sinal no conector do rádio.
