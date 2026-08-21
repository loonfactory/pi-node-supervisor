# Bundled components

This repository versions the complete device runtime below `components/` so a
deployment never clones mutable upstream branches or depends on sibling working
directories.

- `capture-node` was imported with its Git history at `c577718`.
- `huintech-device` was imported with its Git history at `0daa962`.
- `pi-power-manager` had no Git metadata in its source directory, so its current
  source was recorded as the initial import `4474e6e`.

Component updates must be reviewed and committed in this repository before
deployment. Do not fetch or clone component source on the Raspberry Pi.
