# Release Acceptance Suite

This directory contains the external acceptance checks for the V5.2 experimental release candidate.

Run from anywhere:

```bash
node /home/peet/Projects/AgenticLab-tools/release/acceptance.mjs
```

It verifies:

- clean payload revision and static hygiene;
- all payload adapter syntax and policy JSON;
- no tests/fixtures/local identifiers inside the clean payload;
- current canonical docs reference the published payload revision;
- C14 and the capability matrix exist;
- external AgenticLab-tools test suites pass.

The suite makes no payload changes and requires no network, services, or database.

Outputs are content-free: check names, pass/fail, short details. Exit code is `0` only if every check passes.
