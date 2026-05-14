# JSONL session fixtures

Synthetic Claude Code session logs for testing `cc_rig.baseline.jsonl`.

Each fixture follows the shape Claude Code writes to
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`:

- One JSON object per line, terminated by `\n`.
- Events with `type: "assistant"` carry a `message` block containing
  `role`, `model`, `usage`, and optionally `content` (a list of blocks).
- `usage` blocks carry `input_tokens`, `output_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`.
- We synthesize rather than anonymize so token totals + cache breaker
  counts are deterministic and assertions stay readable.

Three tiers, three sessions each. Numbers are intentionally small (a few
thousand tokens) so the math is easy to eyeball in test assertions.
