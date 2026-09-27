import { readdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const outputDirectory = fileURLToPath(
  new URL("../../src/nebula3_mcp/ui/", import.meta.url),
);
const names = (await readdir(outputDirectory)).sort();
if (names.length !== 1 || names[0] !== "query-result.html") {
  throw new Error(`Unexpected UI build outputs: ${names.join(", ")}`);
}

const outputUrl = new URL("query-result.html", `file://${outputDirectory}/`);
const generatedHtml = await readFile(outputUrl, "utf8");
const html = generatedHtml.replace(/[ \t]+$/gmu, "");
if (html !== generatedHtml) {
  await writeFile(outputUrl, html, "utf8");
}
const forbidden = ["/Users/", "sourceMappingURL"];
for (const marker of forbidden) {
  if (html.includes(marker)) {
    throw new Error(`UI build contains forbidden marker: ${marker}`);
  }
}
if (/<script\b[^>]*\bsrc\s*=/iu.test(html) || /<link\b[^>]*\bhref\s*=/iu.test(html)) {
  throw new Error("UI build contains an external script or stylesheet reference");
}
if (!html.includes("default-src 'none'")) {
  throw new Error("UI build is missing its deny-by-default CSP");
}

const allowedUrlLiterals = new Set([
  "http://www.w3.org/1999/xlink",
  "http://www.w3.org/2000/svg",
  "http://www.w3.org/2000/xmlns/",
  "https://github.com/vega/vega-lite/issues/2415",
  "https://vega.github.io/editor/",
  "https://vega.github.io/schema/vega/v6.json",
]);
const urlLiterals = html.match(/https?:\/\/[^\s"'`<>()]+/gu) ?? [];
for (const url of new Set(urlLiterals)) {
  if (!allowedUrlLiterals.has(url)) {
    throw new Error(`UI build contains an unexpected URL literal: ${url}`);
  }
}
