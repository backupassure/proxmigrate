const fs = require("fs");
const path = require("path");

const dest = path.join(__dirname, "..", "static", "xterm");
fs.mkdirSync(dest, { recursive: true });

const files = [
  ["node_modules/@xterm/xterm/css/xterm.css", "xterm.css"],
  ["node_modules/@xterm/xterm/lib/xterm.js", "xterm.js"],
  ["node_modules/@xterm/addon-fit/lib/addon-fit.js", "addon-fit.js"],
];

for (const [src, name] of files) {
  const srcPath = path.join(__dirname, "..", src);
  const destPath = path.join(dest, name);
  fs.copyFileSync(srcPath, destPath);
  console.log(`  ${name} -> static/xterm/`);
}
