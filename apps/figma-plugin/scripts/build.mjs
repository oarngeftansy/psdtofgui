import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build as esbuild } from "esbuild";
import { buildManifest, normalizeServerOrigin, validatePluginAccessToken, validatePluginId } from "./build-manifest.mjs";

const allowPrivateHttp = process.env.FGUI_ALLOW_PRIVATE_HTTP_ORIGIN === "1";
const origin = normalizeServerOrigin(process.env.FGUI_SERVER_ORIGIN, { allowPrivateHttp });
const pluginId = validatePluginId(process.env.FIGMA_PLUGIN_ID);
const accessToken = validatePluginAccessToken(process.env.FGUI_PLUGIN_ACCESS_TOKEN);
const manifest = buildManifest(origin, pluginId, { allowPrivateHttp });
const normalizeLineEndings = (value) => value.replace(/\r\n?/gu, "\n");
const uiTemplate = normalizeLineEndings(
  await readFile(new URL("../src/ui.html", import.meta.url), "utf8"),
);
const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));
const distDir = process.env.FGUI_PLUGIN_DIST_DIR
  ? resolve(process.env.FGUI_PLUGIN_DIST_DIR)
  : fileURLToPath(new URL("../dist/", import.meta.url));

async function viteFallbackBundle({ root, entry, define, name }) {
  const { build: viteBuild } = await import("../../web-console/node_modules/vite/dist/node/index.js");
  const result = await viteBuild({
    root,
    configFile: false,
    logLevel: "silent",
    define,
    build: {
      write: false,
      minify: false,
      target: "es2022",
      lib: { entry, formats: ["iife"], name },
    },
  });
  const output = Array.isArray(result) ? result.flatMap((item) => item.output) : result.output;
  const chunk = output.find((item) => item.type === "chunk" && item.isEntry);
  if (!chunk || chunk.type !== "chunk") throw new Error("Vite fallback bundle was not generated");
  return chunk.code;
}

async function bundle(config, fallback) {
  if (/[\\/]\.worktrees[\\/]/u.test(repositoryRoot)) {
    return viteFallbackBundle(fallback);
  }
  try {
    const result = await esbuild(config);
    const javascript = result.outputFiles.find((file) => file.path.endsWith(".js"))?.text;
    if (!javascript) throw new Error("esbuild bundle was not generated");
    return javascript;
  } catch (error) {
    if (!(error instanceof Error) || !/access is denied/iu.test(error.message)) throw error;
    return viteFallbackBundle(fallback);
  }
}

await mkdir(distDir, { recursive: true });
await writeFile(join(distDir, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
const uiJavaScript = await bundle({
  absWorkingDir: repositoryRoot,
  entryPoints: [join(repositoryRoot, "apps", "web-console", "src", "figma", "plugin-entry.tsx")],
  outfile: "plugin-ui.js",
  write: false,
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  legalComments: "none",
  define: {
    __FGUI_SERVER_ORIGIN__: JSON.stringify(origin),
    __FGUI_PLUGIN_ACCESS_TOKEN__: JSON.stringify(accessToken),
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
}, {
  root: fileURLToPath(new URL("../../web-console/", import.meta.url)),
  entry: fileURLToPath(new URL("../../web-console/src/figma/plugin-entry.tsx", import.meta.url)),
  name: "FigmaToFairyGUIPluginUI",
  define: {
    __FGUI_SERVER_ORIGIN__: JSON.stringify(origin),
    __FGUI_PLUGIN_ACCESS_TOKEN__: JSON.stringify(accessToken),
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});
const uiCss = normalizeLineEndings(
  await readFile(new URL("../../web-console/src/styles.css", import.meta.url), "utf8"),
);
const uiHtml = uiTemplate
  .replace("__PLUGIN_UI_CSS__", uiCss.replace(/<\/style/giu, "<\\/style"))
  .replace("__PLUGIN_UI_JS__", uiJavaScript.replace(/<\/script/giu, "<\\/script"));
await writeFile(join(distDir, "ui.html"), uiHtml);
const mainJavaScript = await bundle({
  absWorkingDir: repositoryRoot,
  entryPoints: [join(repositoryRoot, "apps", "figma-plugin", "src", "code.ts")],
  outfile: "code.js",
  write: false,
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  define: {
    __FGUI_SERVER_ORIGIN__: JSON.stringify(origin),
    __FIGMA_PLUGIN_ID__: JSON.stringify(pluginId),
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
}, {
  root: fileURLToPath(new URL("../", import.meta.url)),
  entry: fileURLToPath(new URL("../src/code.ts", import.meta.url)),
  name: "FigmaToFairyGUIPluginMain",
  define: {
    __FGUI_SERVER_ORIGIN__: JSON.stringify(origin),
    __FIGMA_PLUGIN_ID__: JSON.stringify(pluginId),
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});
await writeFile(join(distDir, "code.js"), mainJavaScript);
