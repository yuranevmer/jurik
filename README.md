# jurik

Tools that build a git repository (`build/laws/`) of the Constitution and codes of Ukraine
with their full legislative history, from zakon.rada.gov.ua (official texts) and
data.rada.gov.ua (bills, votes).

```
uv sync
uv run jurik --help
uv run pytest          # network tests are skipped; run with -m network to include them
uv run ruff check
```

The registry of codes lives in `codes.yaml`. Source data: © Верховна Рада України,
zakon.rada.gov.ua, data.rada.gov.ua.
