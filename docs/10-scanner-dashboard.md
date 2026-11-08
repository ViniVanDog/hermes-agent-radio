**Português (Brasil)** · [English](en/10-scanner-dashboard.md)

# 10. Painel e scanner no canal 1

O `dashboard.py` é um pequeno aplicativo web só com a biblioteca padrão
(porta 8787), com duas abas. A interface é em inglês; os nomes de botões
abaixo aparecem como na tela.

- **Hermes**: a última transcrição (STT) e a última resposta (TTS) em letra
  grande, o estado de RX/TX, um histórico de eventos ao vivo, o
  [uso de API](11-usage-meter.md) por dia e um botão **Clear context**
  (limpar contexto). O botão envia SIGUSR1 ao Hermes, que começa uma sessão
  nova no próximo ponto seguro.
- **Scanner**: controla o `radio_scanner.py` no PC que tem o ESP32-C3 na
  USB. Ele transforma o rádio da base num scanner lento, só de recepção,
  com áudio ao vivo no navegador, um espectrograma de áudio de 0–8 kHz,
  uma linha do tempo de frequências e gravação opcional em WAV dos trechos
  com voz.

```bash
python3 -m baofeng_bridge.web.dashboard --bind 0.0.0.0 --port 8787 [--scanner-url http://<host-pc>:8788]
```

Use o scanner para achar um **canal quieto e legal** antes de pôr o agente
no ar ([segurança e privacidade](security-and-privacy.md)). Gravar o
tráfego de outras pessoas é restrito em alguns países, então use a
gravação só nos seus próprios canais.

## Como o scanner funciona

O BF-777S não tem VFO, e a porta de programação dele não consegue mudar o
canal selecionado. Então o scanner **reprograma o canal 1** e você deixa o
seletor no 1:

1. Na primeira partida ele lê a imagem completa de 992 bytes do rádio e a
   salva, sem permitir alteração, como o **perfil Hermes**
   (`diagnostics/scanner/hermes.bin`). Ele se recusa a substituir esse
   arquivo em silêncio por uma imagem diferente.
2. Para cada frequência ele reescreve os 16 bytes do canal 1 (frequência
   de RX, **TX inibido**, FM estreito, sem tons, sem scramble), envia o
   conjunto completo de blocos suportado pelo CHIRP (48 × 8 bytes) numa
   sessão de programação nova, depois lê os 992 bytes de volta em outra
   sessão e compara.
3. Ele escuta. Um VAD WebRTC roda **localmente** no áudio de 16 kHz, com um
   piso de RMS, janela de votos e 600 ms de acomodação, para marcar *voz
   provável*. Não há transcrição, e nada vai para o Hermes.
4. Sem voz, ele segue depois do tempo de escuta. Com voz, espera um
   período quieto de tolerância, ou o limite por frequência.
5. Ao parar, ele envia o perfil Hermes e exige que os 992 bytes batam antes
   de reativar o TX (`a`) e o repasse de áudio ao Hermes.

Travas de segurança:

- Durante a varredura o C3 fica no modo **só recepção** (`r`, gravado na
  NVS e mantido entre resets). Ele rejeita TX por UDP e PTT.
- O `udp_relay.py` para de repassar ao Hermes enquanto o estado do scanner
  diz varrendo ou recuperando, **mesmo se o controlador tiver caído**. Um
  arquivo de estado ilegível falha fechado.
- Os canais 2–16 e as configurações são regravados com os valores salvos.
  Se a memória do rádio fora do canal 1 mudou, a restauração automática é
  bloqueada.
- Erros de comunicação (ACK faltando, resposta curta, cabeçalho ruim) são
  repetidos até 3 vezes com uma sessão nova. Uma identidade de rádio
  inesperada, um perfil diferente ou uma leitura de volta que não bate
  abortam a operação. Uma restauração que falhou mantém o TX inibido até
  você resolver (**Use Hermes profile** tenta de novo).
- Cada resintonia é uma **gravação persistente na memória**, com pelo menos
  15 s entre elas. Não se conhece um número de ciclos de EEPROM para este
  rádio. O modo de repetição e as varreduras da faixa inteira multiplicam
  as gravações, então pense antes de usá-los.

### Presets

| Preset | Conteúdo |
|---|---|
| Baofeng factory | As 16 frequências da imagem de fábrica da nossa unidade. Edite `FACTORY_FREQUENCIES` no `radio_scanner.py` para a sua. |
| Brazil (Anatel) general use | 26 portadoras: 462,5625–462,7125 e 467,5625–467,7125 MHz, a cada 12,5 kHz (Ato Anatel 14448/2017, §15, Tabela XII). Troque pela lista de uso livre do seu país. |
| Full BF-777S band | 400–470 MHz em passos de 12,5 ou 25 kHz. 5.601 pontos a 12,5 kHz significam **mais de 23 h** de escuta por volta. A interface mostra a estimativa antes de começar. |
| Custom | Até 256 frequências na grade de 12,5 kHz dentro de 400–470 MHz. |

Os padrões são 5 s de escuta, 3 s de tolerância depois da voz, 120 s no
máximo por frequência e piso de RMS de 250, sem repetição.

## Áudio ao vivo e gravação

O áudio ao vivo usa Web Audio no navegador (sem permissão de microfone).
Os quadros têm um cabeçalho de 16 bytes (`<dIB3x`: segundos de época,
frequência em Hz, flags) e 640 bytes de PCM. A reprodução é limitada a
600 ms de atraso acumulado, com no máximo 8 ouvintes. *Record voice
segments to the host disk* (gravar trechos com voz no disco do host) salva
clipes WAV (0,5 s antes da voz, 60 s no máximo, divididos nas trocas de
frequência) com arquivos JSON ao lado, em `diagnostics/scanner/recordings/`
no host. A cota padrão é 1024 MB, e a gravação pausa quando a cota é
atingida ou o disco tem menos de 32 MB livres. Nada é apagado
automaticamente.

## Instalação

```bash
# PC host (ESP32-C3 na USB)
.build-venv/bin/pip install -r requirements-scanner.txt
python3 tools/flash.py --target c3 --port /dev/ttyUSB0
.build-venv/bin/python -m baofeng_bridge.scanner.radio_scanner --bind <host-ip-on-vm-network> \
    --allow-client <vm-ip> --allow-client <host-ip-on-vm-network> \
    [--hermes-profile diagnostics/chirp/<your-backup>.bin]
# ou como serviços: baofeng-scanner + baofeng-relay (systemd/, capítulo 5)

# VM do Hermes
BAOFENG_SCANNER_URL=http://<host-ip-on-vm-network>:8788 python3 -m baofeng_bridge.web.dashboard --bind 0.0.0.0
# ou: systemctl --user enable --now baofeng-dashboard
```

Só os endereços de cliente listados podem chamar a API do scanner, e o
painel rejeita comandos do scanner vindos de origens de navegador
estranhas. Mesmo assim, **mantenha os dois numa rede privada**: o painel
não tem login.

Estado, resultados, logs, backups de antes da varredura e o perfil Hermes
ficam em `diagnostics/scanner/`. Leve esse diretório junto quando mudar a
instalação de lugar, e nunca apague o estado de recuperação para
"destravar" o Hermes; restaure pelo controlador.

```text
Radio ch.1 → C3 → host udp_relay.py ─┬─ copy → radio_scanner.py (VAD, recording)
                                      └─ Hermes VM (only in the Hermes profile)
VM dashboard.py ⇄ HTTP proxy ⇄ host radio_scanner.py → USB → C3 → radio UART
```
