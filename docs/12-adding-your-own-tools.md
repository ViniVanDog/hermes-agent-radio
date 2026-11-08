**Português (Brasil)** · [English](en/12-adding-your-own-tools.md)

# 12. Adicionando suas próprias ferramentas

Este repositório para em "um agente com quem você fala por rádio". O que
ele *faz* fica por sua conta. Há dois padrões, cada um com um exemplo
executável em `examples/`.

> **Qualquer pessoa no canal pode chamar as suas ferramentas.** Comece
> somente leitura. Qualquer ferramenta que muda algo precisa de limites
> rígidos e de limite de uso **no código**, mais um log de auditoria. Nunca
> deixe uma ferramenta devolver dados pessoais, credenciais ou códigos. Leia
> [segurança e privacidade](security-and-privacy.md) antes de adicionar
> qualquer coisa.

## A. Ferramentas que o agente chama: MCP

O Hermes carrega ferramentas dos servidores MCP listados em
`~/.hermes/config.yaml`. O `examples/mcp_stub/server.py` é um servidor
propositalmente minúsculo, com duas ferramentas, `radio_time` e
`radio_note`. Nenhuma fala com serviço externo.

```bash
<hermes-venv>/bin/pip install mcp            # se o venv do Hermes não tiver
```

```yaml
mcp_servers:
  radio_demo:
    command: /path/to/hermes-venv/bin/python
    args: ["/home/you/hermes-agent-radio/examples/mcp_stub/server.py"]
    timeout: 30
```

Reinicie o `hermes-radio`. As ferramentas aparecem como
`mcp_radio_demo_radio_time` e `mcp_radio_demo_radio_note`. Pergunte "que
horas são?" pelo rádio.

Regras de projeto que importam mais no rádio do que em qualquer outro lugar:

| Regra | Por quê |
|---|---|
| **Descrições de ferramenta de uma linha** | Todo esquema é reenviado em toda chamada à LLM ([capítulo 7](07-hermes-cost-trim.md)). |
| **Resultados em texto simples, poucas linhas** | Os resultados ficam na conversa, e a resposta montada com eles é falada. JSON vira uma sopa de pontuação lida em voz alta. |
| **Limite listas (≈8 itens) e ofereça um argumento de busca** | "Listar tudo" estoura o contexto e a paciência de quem ouve. |
| **Aceite entrada aproximada** | O STT erra a grafia de nomes. Compare com tolerância e, quando vários baterem, devolva "Qual deles: A ou B?" em vez de adivinhar. |
| **Argumentos em lote** | `do_thing(items=[…])` é melhor que N chamadas: menos idas e voltas, menos silêncio no ar. |
| **Valide e falhe numa frase** | O agente vai repetir a sua mensagem de erro ao operador palavra por palavra. |
| **Limite o uso de toda ação no código** | O `radio_note` usa `RateLimit(5, 600)`: cinco notas a cada dez minutos, seja quem for que peça. Falhas de validação não gastam a cota. |
| **Pergunte antes de ações irreversíveis; não pergunte antes das inofensivas** | Um "sim" falado protege contra **entender errado**, não contra impostores: qualquer um pode dizê-lo. Mantenha ações irreversíveis ou de alto valor totalmente fora do rádio. |
| **Devolva o mínimo** | Contagens, status, iniciais. Filtre campos sensíveis na ferramenta, não no prompt. |
| **Defina um timeout compatível com a chamada real mais lenta** | `timeout:` em `mcp_servers`. Num timeout, confira se a ação aconteceu antes de tentar de novo. |

Os dois exemplos respondem em `BAOFENG_LANG` (`en` ou `pt-BR`). Mantenha
toda frase que uma ferramenta pode devolver numa tabela por idioma, como o
`MESSAGES` do exemplo de MCP: o agente as repete quase ao pé da letra,
então elas já devem soar bem no idioma do operador.

O `examples/mcp_stub/test_server.py` mostra como testar o corpo das
ferramentas sem o Hermes.

## B. O que o agente diz sem ser chamado: avisos

Para alertas e lembretes, **não** acorde a LLM. Coloque um arquivo JSON na
fila, e o [patch de avisos](06-hermes-integration.md) o fala no próximo
momento ocioso só pelo TTS, por zero tokens:

```bash
python3 examples/announcer_stub/announcer.py --say "Aviso de teste."
python3 examples/announcer_stub/announcer.py --loop        # exemplo: hora certa a cada hora, das 9 às 18
systemctl --user enable --now baofeng-announcer             # o mesmo, como serviço
```

```json
{"kind": "announce", "text": "Combustível do gerador abaixo de vinte por cento.", "at": "2026-11-06T13:00:00+00:00"}
```

Regras que o leitor impõe:

- ele só fala quando o gate de RX está fechado, nada está gravando, nenhum
  turno ou TTS está em andamento, e tudo isso se mantém há 5 s;
- arquivos com mais de 10 minutos são descartados sem serem falados, então
  um reinício nunca repete uma fila acumulada;
- arquivos que começam com `.` são ignorados, então escreva `.nome` e
  renomeie depois (o exemplo faz isso).

Troque o `due_announcements()` pelas suas próprias regras: um limite de
sensor, uma agenda, um trabalho concluído. Se o operador responder ao
aviso, essa resposta é um turno normal da LLM.
