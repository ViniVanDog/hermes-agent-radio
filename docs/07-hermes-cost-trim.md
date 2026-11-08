**Português (Brasil)** · [English](en/07-hermes-cost-trim.md)

# 7. Mantendo o Hermes barato no rádio

Toda chamada do Hermes à LLM leva o prompt de sistema, **todo esquema de
ferramenta** e a **conversa inteira**. Um agente de rádio fica ligado o dia
todo e responde perguntas curtas, então esses custos fixos dominam. As
mudanças abaixo vêm de medir a nossa própria instalação com o
[medidor de uso](11-usage-meter.md).

## 1. Enxugue a config (`apply_radio_config.py`)

Rode com o **Python do próprio Hermes** (ele precisa do `ruamel.yaml`, que
preserva os seus comentários):

```bash
<hermes-venv>/bin/python hermes_patches/apply_radio_config.py --dry-run     # mostra as mudanças
<hermes-venv>/bin/python hermes_patches/apply_radio_config.py [--keep skill-a skill-b]
systemctl --user restart hermes-radio
```

Ele faz backup do `config.yaml` antes, restaura o backup se o resultado não
for válido, e é idempotente.

| Configuração | Efeito que medimos |
|---|---|
| `auxiliary.background_review.enabled: false` | Desliga a autorrevisão do Hermes depois de cada turno. Ela foi **113 das nossas primeiras 590 chamadas e 28% dos tokens de entrada**. |
| `auxiliary.title_generation.model_upgrade_enabled: false` | Mantém os títulos de sessão locais e instantâneos, sem chamada extra ao modelo por sessão. |
| `platform_toolsets.cli: [skills]` | Remove os esquemas das ferramentas de arquivo, terminal e visão: **~3k tokens por chamada**, mais as chamadas de "aprovação" que elas provocam. As ferramentas MCP que você registrar não são afetadas. |
| `skills.disabled: <tudo menos --keep>` | A lista `<available_skills>` tinha **~6k caracteres por chamada** de skills que ninguém usa pelo rádio. |

> **Bônus de segurança.** Sem as ferramentas de terminal e de arquivo,
> ninguém no canal consegue convencer o agente a rodar comandos na máquina
> dele. Qualquer pessoa com um rádio e o seu tom de squelch alcança o
> agente, então trate o canal como entrada não confiável ([segurança e privacidade](security-and-privacy.md)).

## 2. Não envie áudio que não é fala

- O **tom CTCSS/DCS** do rádio impede que a maior parte do tráfego alheio abra o squelch.
  É um filtro de ruído, não controle de acesso ([por quê](security-and-privacy.md)).
- O **gate de RX** ignora rajadas com menos de 0,8 s de áudio real
  (cliques do PTT, caudas de squelch). Elas nunca chegam ao STT.
- O **filtro de entrada** descarta os avisos de voz do próprio rádio
  depois do STT, antes da LLM.

## 3. Mantenha a conversa curta

O Hermes não comprime o histórico de uma sessão interativa, então a
entrada de cada chamada cresce ao longo do dia. Dois limites:

- **Reset por ociosidade** (`HERMES_RADIO_IDLE_RESET_MIN`, padrão 20):
  depois de 20 minutos quietos, a próxima transmissão começa uma sessão
  nova. O botão *Clear context* do painel faz o mesmo sob demanda.
- **Virada por tokens** (`HERMES_RADIO_CONTEXT_MAX_TOKENS`, padrão 20.000):
  entre turnos, quando o último prompt passou do limite, começa uma sessão
  nova levando só o último pedido, a resposta final na íntegra (para que um
  "sim" logo depois ainda faça sentido) e os resultados de ferramenta mais
  recentes que caibam em 8.000 caracteres. **Sem chamada a modelo de
  resumo.** Nunca interrompe um turno em andamento. A sessão antiga fica no
  histórico do Hermes.

## 4. Fale sem pensar

Alertas, lembretes, hora certa e afins não precisam de modelo. Escreva-os
na fila de avisos e o Hermes os lê em voz alta pelo TTS no próximo momento
ocioso, por **zero tokens** ([capítulo 12](12-adding-your-own-tools.md)).
Só a resposta do operador, se houver, vira um turno da LLM.

## 5. Ferramentas pequenas, resultados pequenos

Se você adicionar ferramentas ([capítulo 12](12-adding-your-own-tools.md)):

- descrições de uma linha, porque os esquemas são reenviados em toda chamada;
- devolva poucas linhas de texto simples, não JSON, porque os resultados ficam no histórico;
- limite listas (ex.: 8 itens) e ofereça um argumento de busca em vez de
  "listar tudo".

## 6. Meça

```bash
python3 -m radio_bridge.usage 7
```

Isso mostra chamadas, entrada sem cache/com cache, saída e tokens por
chamada por dia, separados por tarefa do Hermes (conversation,
background_review, title_generation, …). Confira antes e depois de cada mudança.
