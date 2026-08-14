# Discovery API reference

This file is a pointer for Claude (and humans) — the authoritative content lives
next to it:

    context/discoverAPI.md

Read that file whenever you need to:

- Look up the exact shape of a discovery-feed item.
- Understand the WAF caveats (`User-Agent`, `Accept`, `Accept-Language` — note: oed-cli does **not** send a default `Referer`; see §6 of `context/discoverAPI.md`).
- Verify the failing-input behaviour of the discovery endpoints
  (e.g. unsupported community → `[]`, HEAD → `501`, `/discovery` → `404`).
