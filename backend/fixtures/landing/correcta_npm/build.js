// Genera dist/ a partir de src/: copia los estáticos y completa el título con escape-html.
const fs = require("fs");
const path = require("path");
const escapeHtml = require("escape-html");

const src = path.join(__dirname, "src");
const dist = path.join(__dirname, "dist");
fs.mkdirSync(dist, { recursive: true });
for (const file of ["styles.css", "app.js"]) {
  fs.copyFileSync(path.join(src, file), path.join(dist, file));
}
const template = fs.readFileSync(path.join(src, "index.template.html"), "utf8");
fs.writeFileSync(path.join(dist, "index.html"), template.replace(/<title>.*<\/title>/, `<title>${escapeHtml("Huerta Viva — Verduras orgánicas a tu puerta")}</title>`));
console.log("build ok:", fs.readdirSync(dist).join(", "));
