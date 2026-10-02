# Policy 27 — Semantic Reskin Contract

Policy 27 is the current PSD → FairyGUI reskin architecture.

## Default unit

`FGUI Semantic Component → Legacy Visual Bundle → PSD Semantic Group / Target Visual Bundle`

The old component remains the runtime object. The target PSD group replaces its current visual skin.

## Hard invariants

- Preserve runtime identity: id, name, hierarchy contract and component type.
- Preserve Controller, Gear, Relation, Transition and instance/runtime bindings.
- Pair Component ↔ PSD Group before allocating visual leaves.
- Keep native Text/RichText objects and restyle them in place.
- Treat nested Components as separate semantic boundaries.
- Decorative leaves inside a matched PSD Group are implementation details, not business-level ADDs.
- A matched semantic bundle has exactly one default Target Visual Bundle owner unless a later policy introduces explicit state/page-level ownership proof.
- Static superseded target-state Graph/Image paint may retire.
- Other-state paint stays available to its Controller state.
- Current-state dynamic/runtime paint without proven target ownership blocks candidate generation.
- One PSD visual leaf may have only one render owner.
- A whole-bundle visual host may use GearDisplay/GearIcon, but hosts with GearXY/GearSize/GearLook/GearColor/GearAnimation-style dynamic properties must block unless state-specific ownership is proven.

## Compatibility boundary

`build_mapping()` still contains older/direct mapping heuristics because non-semantic and historical flows depend on it. Those heuristics are not authoritative for Policy 27 PSD ownership.

`normalize_psd_semantic_reskin()` clears legacy `owned_*` / `composite_*` ownership metadata before semantic allocation, retaining only correspondence evidence. Therefore the Policy 27 normalizer is the single authority for PSD Group / Visual Bundle ownership.

## Acceptance

A normal pure reskin should trend toward semantic `MODIFY`, not `KEEP + ADD`.

For every matched semantic group:

- decorative business-level ADD = 0;
- runtime contract unchanged;
- current target state has no old-skin/new-skin double ownership;
- unresolved/blocked items must be resolved before build;
- real FairyGUI Editor 6.1.4 verification remains mandatory.

See `HANDOFF.md` for current operational details.