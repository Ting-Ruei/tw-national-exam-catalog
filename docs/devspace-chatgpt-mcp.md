# DevSpace ChatGPT MCP Setup

This project can expose a narrow local workspace to subscription ChatGPT through DevSpace, a self-hosted MCP server.

## What This Solves

DevSpace lets ChatGPT connect to an approved local project folder and use MCP tools to read files, search code, make scoped edits, run commands, and inspect changes. This is separate from Review UI LLM tasks:

- DevSpace: ChatGPT helps develop this repo.
- Review UI AI audit: models inspect parsed exam candidates.

Do not mix the two permission models.

## Security Boundary

DevSpace is powerful local access. The shell tool runs as your macOS user, so treat a connected ChatGPT session like a trusted coding collaborator.

Rules for this repo:

- Allow only `/Users/tim/tw-national-exam-catalog`, not `$HOME` or `/`.
- Keep `~/.devspace-*`, `.env.devspace`, and owner tokens private.
- Do not expose textbook folders or unrelated private projects through DevSpace.
- Use a public HTTPS reverse proxy with access control when possible.

## Local Script

Start with:

```bash
bash scripts/start_devspace_chatgpt_mcp_screen.sh
```

The screen wrapper starts a detached `screen` session named `tw-national-exam-devspace`, writes logs to `tmp/devspace/devspace.log`, and runs `scripts/start_devspace_chatgpt_mcp.sh` inside it.

The underlying script:

- uses local port `7676`;
- allowlists only this repository by default;
- stores its owner token outside the repo at `~/.devspace-tw-national-exam-catalog/owner_token`;
- starts `npx @waishnav/devspace serve`;
- prints the local MCP URL.

Stop it with:

```bash
bash scripts/stop_devspace_chatgpt_mcp.sh
```

Show the owner password only on your own machine:

```bash
cat ~/.devspace-tw-national-exam-catalog/owner_token
```

Local endpoint:

```text
http://127.0.0.1:7676/mcp
```

This local endpoint is useful for smoke testing, but ChatGPT needs a public HTTPS URL.

## Public HTTPS URL

DevSpace does not create the tunnel. Point your tunnel or reverse proxy to:

```text
http://127.0.0.1:7676
```

Then set:

```bash
export DEVSPACE_PUBLIC_BASE_URL="https://your-devspace-domain.example.com"
bash scripts/start_devspace_chatgpt_mcp_screen.sh
```

The MCP endpoint entered in ChatGPT should be:

```text
https://your-devspace-domain.example.com/mcp
```

`DEVSPACE_PUBLIC_BASE_URL` must be the origin only, without `/mcp`.

## Optional `.env.devspace`

Create a local `.env.devspace` if you want stable settings:

```dotenv
DEVSPACE_PUBLIC_BASE_URL=https://your-devspace-domain.example.com
DEVSPACE_ALLOWED_ROOTS=/Users/tim/tw-national-exam-catalog
DEVSPACE_PORT=7676
DEVSPACE_HOST=127.0.0.1
DEVSPACE_TOOL_MODE=minimal
DEVSPACE_TOOL_NAMING=short
DEVSPACE_WIDGETS=full
```

`.env.devspace` is gitignored.

## ChatGPT Steps

1. Start the HTTPS tunnel to `http://127.0.0.1:7676`.
2. Start DevSpace with `bash scripts/start_devspace_chatgpt_mcp_screen.sh`.
3. In ChatGPT, add/connect an MCP server using `https://your-devspace-domain.example.com/mcp`.
4. When the approval page appears, enter the owner password printed by the script.
5. Ask ChatGPT to open:

```text
/Users/tim/tw-national-exam-catalog
```

6. Ask it to read `AGENTS.md` before making changes.

## Verification

Check local runtime:

```bash
npx @waishnav/devspace doctor
```

Check the local MCP endpoint responds:

```bash
curl -i http://127.0.0.1:7676/mcp
```

An MCP endpoint may reject plain browser/curl requests depending on transport or auth state; the important part is that DevSpace is running and ChatGPT can complete OAuth approval through the public URL.

## Sources

- DevSpace README: <https://github.com/Waishnav/devspace>
- Setup guide: <https://github.com/Waishnav/devspace/blob/main/docs/setup.md>
- Security model: <https://github.com/Waishnav/devspace/blob/main/docs/security.md>
- ChatGPT coding workflow: <https://github.com/Waishnav/devspace/blob/main/docs/chatgpt-coding-workflow.md>
