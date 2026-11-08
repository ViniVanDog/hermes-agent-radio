**Português (Brasil)** · [English](en/security-and-privacy.md)

# Leia isto primeiro: rádio não é privado

Tudo neste guia roda sobre rádios portáteis FM analógicos baratos. **Eles
nunca foram projetados para privacidade ou segurança, e nada neste
repositório muda isso.** Projete o seu agente como se cada palavra que ele
ouve ou fala fosse transmitida para estranhos e gravada, porque é.

## O que o rádio não oferece

| Você pode supor | A realidade |
|---|---|
| "Só os meus rádios nos ouvem." | FM analógico não tem criptografia. Qualquer receptor ou scanner no alcance (muitas vezes vários quilômetros, mais a partir de lugares altos) ouve os dois lados. Um SDR de R$ 150 pode gravar o canal 24 horas por dia. |
| "O tom CTCSS/DCS mantém os outros de fora." | Um tom só decide quando o **seu** rádio abre o squelch. Todo mundo na frequência continua ouvindo você. O tom vai junto em toda transmissão, qualquer scanner o decodifica, e qualquer pessoa pode configurar o mesmo tom e **falar com o seu agente**. É um filtro de ruído, não uma senha. |
| "A função *scramble* do rádio criptografa." | O embaralhamento por inversão de voz é trivialmente reversível e não é criptografia. Em muitas faixas ele também não é permitido. |
| "É só eu criptografar." | Nas faixas de radioamador a maioria dos países proíbe ocultar o sentido de uma transmissão. As faixas de uso livre (FRS, PMR446 e similares) exigem equipamento homologado e sem modificações. Planeje como se criptografia não fosse uma opção. |
| "Uma senha falada ou um 'sim' confirma que sou eu." | Uma senha falada é transmitida e pode ser gravada e **reproduzida**. Um "sim" falado não prova nada sobre quem o disse. |

## Não use os canais de fábrica

Toda unidade do mesmo modelo sai com a **mesma** lista de canais padrão.
Rádios da classe BF-888/BF-777S são vendidos às centenas de milhares, então
essas frequências são movimentadas. Outros donos, rádios de brinquedo,
obras e lojas estão todos nelas, e o seu agente vai ouvir esse tráfego e
responder.

- Escolha uma frequência que seja **legal para você** (categoria de
  licença, faixa, potência, homologação) e quieta onde você está. Escute-a
  por alguns dias antes; o [scanner](10-scanner-dashboard.md) ajuda.
- Configure um tom CTCSS/DCS **diferente do padrão** no rádio da base e nos
  portáteis. Ele reduz ruído e acionamentos acidentais, mas não é segurança.
- Alguns padrões de fábrica não são legais para transmitir em muitos
  países. Programe o rádio você mesmo (o [capítulo 9](09-chirp-programming.md)
  mostra como ler de volta e conferir).
- Gravar ou divulgar o tráfego de rádio de outras pessoas é restrito em
  alguns países. Use a gravação do scanner só nos seus próprios canais.

## Canais brasileiros do tipo FRS

Aqui, “FRS brasileiro” significa **Equipamento de Radiocomunicação de Uso
Geral** da Anatel, não uma autorização FRS dos EUA. O [Ato 14.448/2017,
seção 15, Tabela XII](https://informacoes.anatel.gov.br/legislacao/atos-de-certificacao-de-produtos/2017/1139-ato-14448) lista 26 canais:
462,5625–462,7125 MHz e 467,5625–467,7125 MHz, em passos de 12,5 kHz em
cada grupo; máximo de 500 mW ERP e 12,5 kHz de largura de faixa ocupada.
Use a canalização brasileira, não números importados ou frequências
arbitrárias entre canais.

Use equipamento homologado pela Anatel para a operação pretendida.
Programar um BF-777S/BF-888 nesses canais ou escolher potência “Low” não
comprova conformidade. A seção 15 também restringe transmissões
unidirecionais e proíbe interconexão às redes de serviços de telecomunicações
de interesse coletivo. Verifique se a ligação rádio-agente pretendida é
permitida antes de transmitir; a lista de canais, sozinha, não autoriza
esta ponte.

## RX e TX separados: uma pequena barreira para curiosos

Quando a homologação e as regras de operação permitirem, escolha **canais
diferentes de RX e TX** de forma independente entre os canais permitidos
e quietos. Evite um offset convencional de repetidora ou um pareamento
previsível dos números de canal. Assim, ouvir uma frequência não revela a
outra pela convenção usual de offset. Isso pode dificultar a escuta casual
ou tentativas de responder; não torna o par impossível de adivinhar.

A e B abaixo representam os canais escolhidos:

| Rádio | RX (recebe) | TX (transmite) |
|---|---|---|
| Base ligada ao Hermes | A | B |
| Cada portátil | B | A |

A operação continua half-duplex: fale, solte o PTT e então escute. Esses
portáteis não ouvem uns aos outros diretamente. As duas frequências devem
ser permitidas para transmissão pelo respectivo rádio; confira a atividade
nas duas antes de usar. Combine o tom CTCSS/DCS de cada transmissor com o
do receptor correspondente.

No CHIRP, se o driver do modelo oferecer suporte, use `Frequency` para RX,
`Duplex = split` e `Offset` para a **frequência absoluta de TX**. Inverta
RX/TX nos portáteis, depois leia de volta e confira os dois sentidos. O
[auxiliar de backup do capítulo 9](09-chirp-programming.md) só lê a memória;
ele não aplica essas configurações.

Um scanner ou SDR pode descobrir as duas frequências. Quem encontrar a
frequência de RX da base e o tom ainda pode falar com o agente. Mantenha
todas as restrições de dados e ferramentas abaixo: canais separados
oferecem obscuridade, não criptografia nem autenticação.

## Privacidade de dados: suponha que a vizinhança inteira escuta

Tudo o que o agente diz é público. Tudo o que é dito a ele também é, e
ainda é enviado aos seus provedores de STT e LLM e guardado em disco.

**Nunca deixe uma ferramenta devolver, nem o agente falar:**

- dados pessoais de qualquer pessoa: nomes completos com contexto,
  endereços, telefones, e-mails, informações de saúde ou financeiras,
  registros de pedidos/clientes;
- credenciais, códigos e tokens: senhas, PINs, códigos de uso único,
  códigos de devolução ou retirada, chaves de API, senhas de Wi-Fi;
- segurança operacional: "a loja está vazia", estado do alarme, horários,
  onde ficam objetos de valor ou veículos, quando não há ninguém em casa.

O que fazer no lugar:

- Escreva ferramentas que devolvam o **mínimo**: contagens, status,
  primeiros nomes ou iniciais, "sim/não". Faça a *ferramenta* recusar campos
  sensíveis. Não conte com o prompt para o modelo se segurar. Prompts
  vazam; filtros no código não.
- Mantenha segredos totalmente fora do alcance do agente, para que ele não
  possa ser convencido a lê-los em voz alta.
- Saiba para onde os dados vão. O áudio vai para o seu provedor de STT (a
  menos que você rode [STT local](08-stt-and-tts.md)), e as transcrições
  vão para o seu provedor de LLM. No disco, transcrições e respostas se
  acumulam em `~/.hermes/state.db` e
  `~/.local/state/baofeng-radio/events.jsonl`, e as gravações opcionais do
  scanner em `diagnostics/`. Faça rotação ou apague-os num prazo compatível
  com as suas obrigações (no Brasil, pense na LGPD).

## Controle de acesso: qualquer pessoa no canal é um usuário

**Não existe autenticação** num canal de rádio. Qualquer pessoa com um
rádio, a sua frequência e o seu tom pode comandar o agente, e qualquer
pessoa com uma gravação sua pode reproduzi-la. Isso inclui injeção de
prompt por voz: "ignore as suas instruções e…".

### Comece somente leitura

Comece com ferramentas que só **consultam**. Um agente somente leitura que
sofre abuso pode, no pior caso, dizer algo que não deveria, e as regras de
dados acima limitam isso. O [enxugamento de custo](07-hermes-cost-trim.md)
já remove as ferramentas de terminal, arquivo e visão. Mantenha-as
desligadas no rádio: um agente com shell é um shell remoto para todo mundo
no alcance.

### Se adicionar ações, torne o abuso sem graça

Para qualquer coisa que muda o mundo (impressões, mensagens, pedidos,
portas, dispositivos, dinheiro):

| Controle | Como |
|---|---|
| **Lista de permitidos** | Exponha só ações específicas e estreitas (`print_label(preset, copies)`), nunca genéricas (`run`, `http_request`, `send_message(to, text)`). |
| **Limites rígidos na ferramenta** | Limite os argumentos no código: cópias ≤ 10, valores ≤ X, destinatários de uma lista fixa. |
| **Limites de uso** | Por ação: ex. no máximo 5 a cada 10 minutos e 50 por dia. Ao estourar, recuse com uma frase. Veja `examples/mcp_stub/server.py`. |
| **Intervalos e janelas de horário** | Permita ações arriscadas só no horário de trabalho, com uma pausa entre elas. |
| **Confirmação** | Pergunte antes de qualquer coisa irreversível e leia de volta exatamente o que vai acontecer. Isso protege contra **entender errado**, não contra impostores. |
| **Nada irreversível ou de alto valor pelo rádio** | Apagar, pagar, destrancar, enviar para destinatários arbitrários: faça isso em algum lugar com autenticação de verdade. |
| **Log de auditoria** | Faça a ferramenta, não o modelo, registrar todo pedido de ação (hora, argumentos, transcrição) num arquivo que você revisa. |
| **Botão de desligar** | `systemctl --user stop hermes-radio`, ou pare o `baofeng-tx` para silenciá-lo. Saiba como antes de precisar. |

Espere que o limite de uso dispare no dia em que alguém descobrir o seu
canal. É para isso que ele existe.

### Mantenha a infraestrutura privada

- O painel e a API do scanner **não têm login**. Prenda-os numa rede
  privada (uma bridge de VM ou a rede local), nunca na internet.
- O ESP32 aceita pacotes de TX da rede local. Qualquer pessoa nessa rede
  que consiga enviar UDP para a porta 7778 pode transmitir pelo seu rádio.
  Mantenha-o numa rede que você controla, de preferência um SSID ou VLAN
  separado.
- Rode o Hermes numa VM ou como um usuário dedicado, só com as credenciais
  de que as ferramentas dele precisam.

## Lista de conferência antes de ir ao ar

- [ ] Frequência e potência legais para mim; não é um padrão de fábrica; escutei antes
- [ ] Tom CTCSS/DCS diferente do padrão em todos os rádios (entendido: não é segurança)
- [ ] Ferramentas somente leitura, ou toda ação tem lista de permitidos, limites rígidos, limite de uso e log de auditoria
- [ ] Nenhuma ferramenta pode devolver dados pessoais, credenciais ou códigos
- [ ] Ferramentas de terminal/arquivo desligadas no perfil do rádio
- [ ] Painel, API do scanner e ESP32 só numa rede privada
- [ ] Plano de retenção para `state.db`, `events.jsonl` e gravações
- [ ] Todo mundo que usa o canal sabe que ele é público
