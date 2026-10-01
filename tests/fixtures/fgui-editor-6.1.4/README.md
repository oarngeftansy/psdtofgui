# FairyGUI 6.1.4 dialect fixture

Derived from a project opened and saved with FairyGUI Editor 6.1.4, then reduced to
project-neutral format evidence.
Project: Minimal; publish target: Unity; package: Generated; component: Root 320x180.
The fixture is format evidence only. Production IDs are generated deterministically.

## GUI gate — 2026-08-19

The neutral `Minimal/Minimal.fairy` fixture was launched with FairyGUI Editor 6.1.4. The only
project window was titled `Minimal`. It was saved and closed, then launched again from the same
path, saved a second time, and closed. No repair, migration, or other modal window appeared.

The three tracked fixture files remained byte-identical across the round trip:

- `Minimal.fairy`: `273501ef00ee0a533aa8de2b383249eb93a9b69e431ff318837cc40921ef4d1b`
- `assets/Generated/package.xml`: `e374ff3c3a3c70613ee990e6e8bcb267ca984b6aaf0c4cec833171199bfd6534`
- `assets/Generated/components/Root.xml`:
  `97244573813d99e17263e6f0883cbb94fccd193d2df97bbead187a8a7c350c34`

The editor-created `.objs/` runtime cache is reproducible and excluded by the fixture-local
`.gitignore`.
