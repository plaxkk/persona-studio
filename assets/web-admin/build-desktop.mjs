import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve, dirname } from "node:path";
import { zipSync } from "fflate";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const names = [
  "manifest.json",
  "background.js",
  "desktop.js",
  "popup.js",
  "popup.html",
  "popup.css",
  "README.md",
];
const files = Object.fromEntries(
  names.map((name) => [
    name,
    new Uint8Array(readFileSync(resolve(root, "browser-extension", name))),
  ]),
);
mkdirSync(resolve(root, ".vercel-dist/downloads"), { recursive: true });
writeFileSync(
  resolve(root, ".vercel-dist/downloads/persona-studio-browser.zip"),
  zipSync(files),
);
