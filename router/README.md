# Model router

Claude Code Router now stores provider, model, routing, and client-key settings
in its own management database. Install the current CLI with Node.js 22+:

```bash
npm install -g @musistudio/claude-code-router
ccr ui
```

Open the management UI, add the provider/model, create a CCR client key, and
start the gateway. Point `ANTHROPIC_BASE_URL` at the displayed model gateway
(default `http://127.0.0.1:3456`) and put the CCR client key in
`ANTHROPIC_AUTH_TOKEN`.

See the [official CCR CLI guide](https://github.com/musistudio/claude-code-router/blob/main/docs/src/content/docs/en/guides/cli.md).

`_archive/legacy-config.example.json` is retained only as history for the older
JSON-based router configuration. Current Quick Start instructions do not read
it.
