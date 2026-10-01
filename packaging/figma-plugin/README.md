# Figma to FairyGUI plugin installation

This release ZIP contains only the Figma plugin files. Keep the release archive and its adjacent `checksums.sha256` together until the release owner has verified the SHA-256 value.

## Pilot: Figma development import

1. Extract `Figma-to-FairyGUI-plugin.zip` to a clean local folder.
2. In a Figma file, select **Plugins > Development > Import plugin from manifest**.
3. Select the extracted `manifest.json`, then run the plugin against a current selection.

The plugin is already configured for the approved internal HTTPS service. Designers do not enter a server address, pairing code, or access token.

The plugin opens directly in the new-project Writer. Enter one project name, generate the
candidate, inspect the image, component/interface, Package/resource, and unified-check tabs,
apply only adjustments offered by the service, acknowledge any current warnings, then use the
single **Confirm and download ZIP** decision. The approved candidate can be downloaded again
without rerunning conversion. FairyGUI is fixed to 6.1.4 and the output is a new standalone
project; no template or existing FairyGUI project is required at startup. **Update existing
project** remains a separate overflow-menu action.

## Rollout: private organization plugin

After the pilot passes, the Figma publisher opens **Plugins > Manage plugins > Development > Publish**, selects **Organization** as the publishing destination, and publishes the same plugin ID to the intended organization. Confirm Figma displays exactly one restricted HTTPS network domain before publishing. Do not publish this plugin to the Community.

For each update, rebuild with the approved origin, plugin ID, and deployment access token; verify the release SHA-256; then publish the updated private organization plugin.
