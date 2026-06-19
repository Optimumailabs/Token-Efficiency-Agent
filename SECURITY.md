# Security Policy

## Reporting a vulnerability

If you find a security issue, please do not open a public issue. Email the
maintainers at the address on the [Optimum AI](https://www.optimumai.in) site,
or open a private security advisory on the GitHub repository. We aim to respond
within a few working days.

## What to consider in scope

TEA reads prompt text and writes logs. The most relevant concerns are:

- **Log contents.** When logging is enabled, TEA writes the full original and
  optimised prompt text to disk by default. If your prompts contain secrets or
  personal data, treat the log directory as sensitive: restrict its
  permissions, exclude it from version control (the shipped `.gitignore` does
  this), and rotate or delete logs per your retention policy.
- **Custom compressors.** The optional `compress` transform runs a callable you
  supply, which may call an external model. TEA does not send prompt text
  anywhere on its own; the deterministic transforms are fully local.
- **Untrusted prompt input.** The transforms are pure text operations and do
  not execute prompt content. They are safe to run on untrusted input.

## What is not a vulnerability

- Token counts being approximate when `tiktoken` is not installed. This is
  documented behaviour and flagged in the output.
- The lexical relevance proxy occasionally keeping a chunk that a true
  attention signal would drop. This is a deliberate safety bias, not a defect.

## Supported versions

The latest released version on the `main` branch is supported. Older versions
are not maintained.
