**Português (Brasil)** · [English](en/languages.md)

# Idiomas

A documentação existe em português do Brasil e em inglês: `docs/` e
`docs/en/`, com os mesmos nomes de arquivo. O português do Brasil é a referência. Se
você mudar uma página, mude a gêmea no mesmo commit; o [teste de documentação](../tests/docs/test_docs.py) falha
quando uma página não tem gêmea, quando as duas diferem em títulos ou
blocos de código, ou quando um link está quebrado.

O rádio fala o idioma de `BAOFENG_LANG` (`en` ou `pt-BR`, no
`baofeng.env`). Essa única configuração escolhe as regras de texto falado,
a nota de passagem de contexto, as respostas das ferramentas de exemplo e
o idioma padrão do STT local.
