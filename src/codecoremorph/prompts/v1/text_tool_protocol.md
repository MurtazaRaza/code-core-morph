## Calling tools

You can call the tools listed below. To call one, reply with exactly one fenced JSON block and
nothing after it:

```json
{"tool": "<tool name>", "arguments": {<the tool's arguments>}}
```

The arguments must match the tool's JSON Schema. Call one tool per reply. The result comes
back in the next message, starting with `Tool result`.

### Tools

${tools}
