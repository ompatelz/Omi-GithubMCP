# MCP integration smoke test

RepoPilot uses MCP stdio transport. Copy `mcp-config.example.json` into your
MCP-compatible client configuration, replace the repository path, and provide
`GITHUB_TOKEN` through the client's secret/environment mechanism. Do not put a
real token in the configuration file.

Automated tests use the official MCP Python client's in-process transport to
verify tool discovery, schemas, normalized results, and structured errors with
mocked GitHub HTTP. They intentionally do not make live GitHub calls.

For a manual live smoke test:

1. Connect the client using the example configuration.
2. Confirm all RepoPilot tools appear with descriptions and input schemas.
3. List a directory, then retrieve one known text file.
4. List and inspect issues, then inspect a pull request and its files.
5. Only with explicit intent, create a clearly marked test issue and add one
   comment; confirm both on GitHub.
6. Delete or close the test resource manually if desired; RepoPilot has no
   destructive cleanup tool.
