[Português (Brasil)](../languages.md) · **English**

# Languages

The docs exist in Brazilian Portuguese and English: `docs/` and
`docs/en/`, with the same file names. Brazilian Portuguese is the reference. When you
change a page, change its twin in the same commit; [documentation test](../../tests/docs/test_docs.py) fails when
a page has no twin, when the two differ in headings or code blocks, or when
a link is broken.

The radio itself speaks whatever `BAOFENG_LANG` says (`en` or `pt-BR`, in
`baofeng.env`). That one setting picks the spoken-text rules, the context
handoff note, the example tools' replies and the local STT default.
