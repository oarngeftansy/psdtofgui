# New-Project Writer Test Acceptance

This report is generated from the closed machine result. Each case has one local evidence card screenshot.

| Case | Status |
| --- | --- |
| TC-01 | PASS |
| TC-02 | PASS |
| AC-01 | PASS |
| TC-03 | PASS |
| TC-04 | PASS |
| TC-05 | PASS |

## TC-01 — PASS

### Purpose

Build the generic neutral Writer fixture and reopen its published ZIP.

### Prerequisites

- Canonical generic Plan v2, config, and declared asset manifest are tracked.

### Steps

- Load through CLI loaders.
- Build through Writer.
- Reopen through archive validator.

### Expected

- A valid archive has the exact deterministic member list.

### Actual

- publishedZipFilename=098b03d879e9a30814ec93407fc91e5cfa30bb48fa61d905334a2e1b1a97418e.zip
- archiveMembers=GenericWriterFixture/GenericWriterFixture.fairy,GenericWriterFixture/assets/Generated/components/root-6e07d820.xml,GenericWriterFixture/assets/Generated/package.xml,GenericWriterFixture/assets/Generated/resources/generic-pixel-7fb786de.png
- archiveValidatorClean=true
- archiveReopen=true

Screenshot: [TC-01 evidence](evidence/new-project-writer/tc-01.png)

Screenshot SHA-256: `8ba5606ea62f8d796857e692362c640b4c3bdb3cc3967cf8e3cfbc2f1588ef1c`

## TC-02 — PASS

### Purpose

Prove deterministic archive bytes for the generic Writer fixture.

### Prerequisites

- The generic fixture is valid for new-project generation.

### Steps

- Build twice into isolated output directories.
- Compare both published archives.

### Expected

- Both SHA-256 values match and archive bytes are equal.

### Actual

- firstSha256=bf62cd2789a7d0a44336e28a4c257d4fe91bee34c7673738bdf3f0662217c4ca
- secondSha256=bf62cd2789a7d0a44336e28a4c257d4fe91bee34c7673738bdf3f0662217c4ca
- byteEquality=true

Screenshot: [TC-02 evidence](evidence/new-project-writer/tc-02.png)

Screenshot SHA-256: `926ac0290626b56d6c7441ff610ba77cead90ae1ee18a9c4498fc83f6e91c494`

## AC-01 — PASS

### Purpose

Record the fresh neutral FairyGUI Editor acceptance transcript.

### Prerequisites

- The tracked fresh FairyGUI Editor 6.1.4 transcript is available.
- Only GenericWriterFixture is the real Editor representative.

### Steps

- Validate the exact returned titles and two save rounds.
- Validate delayed close observation, final window count, modal result, and hash parity.

### Expected

- Two saved rounds return GenericWriterFixture, no modal is observed, and final windows are zero.
- The four declared files have equal pre/post SHA-256 values; the state screenshot API is unsupported.

### Actual

- freshTranscriptValid=true
- editorVersion=6.1.4
- returnedWindowTitle=GenericWriterFixture
- saveRounds=open-save-close,reopen-save-close
- delayedCloseObservation=true
- finalWindowCount=0
- modalObserved=false
- stateScreenshot=unsupported(0x80004002)
- fileHashParity=4/4
- transcript=2026-08-20-fgui-6.1.4-new-project-editor-transcript.json

Screenshot: [AC-01 evidence](evidence/new-project-writer/ac-01.png)

Screenshot SHA-256: `bf424a143eff515ff5092ee4b3b76a7dc9a648a643e135f47b48b9577f6c7435`

Durable transcript: [AC-01 durable transcript](2026-08-20-fgui-6.1.4-new-project-editor-transcript.json)

Transcript file SHA-256: `bb74d97ef5039e471184ef3efeb3932e8e4843d5e856e56c88c2aec181ac92d8` (GenericWriterFixture.fairy)
Transcript file SHA-256: `0a90a6b2c18f6443344ec5462511bc12c27c748c079bcbc078dbf841c2d334d4` (assets/Generated/package.xml)
Transcript file SHA-256: `6a804a0c74f0f6ccd8fb1c8b60b0296b05cf62f1b7bbdb3236f4a3a781f08a6a` (assets/Generated/components/root-6e07d820.xml)
Transcript file SHA-256: `4ff6ab670a58c14270e034e2090d9a432caa263a14e0a25785386b0c12f880b5` (assets/Generated/resources/generic-pixel-7fb786de.png)

## TC-03 — PASS

### Purpose

Reject an asset manifest path traversal before archive publication.

### Prerequisites

- The generic fixture asset manifest is copied into isolated test storage.

### Steps

- Inject ../escape.png.
- Invoke the public build CLI.
- Inspect output publication.

### Expected

- ASSET_DIRECTORY is rejected and no ZIP is published.

### Actual

- rejection=ASSET_DIRECTORY
- zipPublished=false

Screenshot: [TC-03 evidence](evidence/new-project-writer/tc-03.png)

Screenshot SHA-256: `f0adb4794b0b62ca5f071b2e988d77e3a32d53d86477fddf1d0823bbf5198c85`

## TC-04 — PASS

### Purpose

Reject an oversized sparse asset before any Pillow probe or archive publication.

### Prerequisites

- The generic fixture asset manifest is copied into isolated test storage.

### Steps

- Create a sparse asset larger than MAX_ASSET_PAYLOAD_BYTES.
- Invoke the public build CLI.

### Expected

- ASSET_DIRECTORY is rejected, Pillow is not probed, and no ZIP is published.

### Actual

- rejection=ASSET_DIRECTORY
- maxAssetPayloadBytes=67108864
- sparseDeclaredBytes=67108865
- oversizedAssetReadCalled=false
- rejectedBeforeFullRead=true
- zipPublished=false

Screenshot: [TC-04 evidence](evidence/new-project-writer/tc-04.png)

Screenshot SHA-256: `07a58fb2320f62b6ee52a872991f6e319157da6fe275e96bc8a26e8f172fcaab`

## TC-05 — PASS

### Purpose

Fail closed for a demand-side component mapping without a generatable definition.

### Prerequisites

- The village fixture remains a generic mapping regression input.

### Steps

- Compile mapping to UIR.
- Compile UIR to Plan.
- Run the production special-case scan.

### Expected

- fgui.component.definition_missing blocks publication without a Writer special case.

### Actual

- diagnostic=fgui.component.definition_missing
- productionSpecialCaseScan=true
- cliPublishAttempt=true
- rejection=PLAN
- zipPublished=false

Screenshot: [TC-05 evidence](evidence/new-project-writer/tc-05.png)

Screenshot SHA-256: `baeee72c93514ae7ddcdced64894a3db3a1e5df947ddf042528f7a3ad7a0ace9`
