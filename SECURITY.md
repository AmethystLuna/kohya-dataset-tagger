# Security Policy

## Reporting a vulnerability

Use GitHub's [private vulnerability reporting](https://github.com/AmethystLuna/kohya-dataset-tagger/security/advisories/new)
rather than a public issue. Please include what you did, what happened, and what you expected —
a reproduction against a throwaway dataset directory is worth more than a description.

This is a spare-time project: expect an initial answer within about a week, not within hours. If the
report is valid you will be credited in the advisory and in `CHANGELOG.md` unless you ask not to be.

## Supported versions

Only the latest commit on `main` (currently the `0.1.x` line) is supported. There is no backport
branch; the fix ships as a new commit and you update with `git pull`.

## What this tool's threat model actually is

Read this before reporting, because most of these are deliberate:

- **It is a local tool.** The service binds to `127.0.0.1` and has no authentication, no accounts and
  no sessions. It is meant for one person on one machine.
- **The root allowlist is a typo guard, not a security boundary.** `core/paths.py` stops mistyped
  paths and out-of-bounds traversal; it does not defend against another user on the same machine.
  `POST /api/roots` and `DELETE /api/roots`, and the model-directory endpoints, exist to change that
  allowlist at runtime.
- **Changing `--host` to a non-loopback address hands read/write access to those endpoints to
  anyone who can reach the port.** That is documented behavior, not a vulnerability. Do not do it
  on a network you do not control.
- **Tagger models are code-adjacent files.** An `.onnx` file you downloaded from somewhere is loaded by
  onnxruntime; treat a model from an untrusted source the way you would treat any binary.

## In scope

- Escaping the allowlist: any way to read or write outside the configured roots through an API
  endpoint, including symlink, `..`, UNC, drive-relative or case tricks.
- Writing the **dataset's image files**, or writing outside the dataset directory, through the
  caption, batch, crop or export endpoints.
- Deleting or truncating a caption in a way that leaves no `.bak` and no error (silent data loss).
- Serving files outside the web root, or executing anything from a request body.
- Making the downloader fetch an arbitrary URL supplied by a request (as opposed to the fixed
  endpoints in `KOHYA_TAGGER_HF_ENDPOINT` / the built-in list).

## Out of scope

- The absence of authentication, and everything listed under the threat model above.
- Anyone who can already run code as you on your machine — they can read the dataset directly.
- Denial of service by pointing the tool at a directory with millions of files.
- The content of the captions or images themselves.
