# Policy 27 — Semantic Reskin Default

This branch makes `Component -> Visual Bundle -> PSD Group` the default PSD reskin contract.

## Invariants
- Preserve FairyGUI runtime identity and logic contract.
- Pair semantic Component to PSD Group before allocating leaves.
- Treat Graph/Image/Loader paint under a matched component as one legacy visual bundle.
- Treat PSD visual leaves under the matched group as one target visual bundle.
- Native Text/RichText stays native and is restyled in place.
- Nested semantic components retain their own ownership boundary.
- Matched groups must not create business-level ADD objects for decorative leaves.
- Legacy target-state paint that is superseded by the PSD bundle retires; other-state paint is preserved for its controller state.
- Ambiguous state-driven paint blocks generation instead of producing old-skin/new-skin overlap.

## Acceptance
- Matched semantic components report MODIFY semantics.
- Matched group decorative ADD count is zero.
- Runtime IDs/names/controllers/gears/relations/transitions are preserved.
- Duplicate current-state visual ownership is rejected.
