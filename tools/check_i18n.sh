#!/usr/bin/env bash
# Сторож словарей. Проверяет две вещи, которые ломаются молча и живут месяцами:
#
#   1. РАСХОЖДЕНИЕ КЛЮЧЕЙ. Ключ добавили в один язык и забыли во втором —
#      на экране появляется сырой путь вроде «lim.norm» вместо текста.
#   2. КИРИЛЛИЦА В АНГЛИЙСКОМ СЛОВАРЕ. Английский — язык по умолчанию и
#      единственный, который видит человек не из России. «Monthly норма»
#      прожило в кабинете до первого осмотра глазами; тесты его не ловят,
#      потому что синтаксически всё верно.
#      Исключение — названия площадок (VK Музыка, Яндекс Музыка, Звук):
#      это имена собственные, их не переводят.
#
# Запуск: tools/check_i18n.sh   (ненулевой код возврата = сборка красная)
set -u
cd "$(dirname "$0")/.."
exec node - "$PWD/frontend/i18n.js" <<'JS'
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("const I18N = {");
const end = src.indexOf("const ERR_RU_TO_EN");
if (start < 0 || end < 0) { console.error("не нашёл объект I18N в i18n.js"); process.exit(2); }
const sb = {}; vm.createContext(sb);
vm.runInContext(src.slice(start, end) + "\n;globalThis.__I=I18N;", sb);
const I = sb.__I;

const keys = (o, p = "") => Object.keys(o).flatMap((k) => {
  const v = o[k], kk = p ? p + "." + k : k;
  return v && typeof v === "object" ? keys(v, kk) : [kk];
});
const en = keys(I.en), ru = keys(I.ru);
const missRu = en.filter((k) => !ru.includes(k));
const missEn = ru.filter((k) => !en.includes(k));

// Имена собственные: площадки, которые по-английски тоже пишутся кириллицей.
const BRANDS = /^(VK Музыка|Яндекс Музыка|Звук)$/;
const cyr = [];
(function walk(o, p) {
  for (const k of Object.keys(o)) {
    const v = o[k], kk = p ? p + "." + k : k;
    if (v && typeof v === "object") walk(v, kk);
    else if (typeof v === "string" && /[А-Яа-яЁё]/.test(v) && !BRANDS.test(v.trim())) {
      cyr.push(`  ${kk} = ${JSON.stringify(v).slice(0, 100)}`);
    }
  }
})(I.en, "");

let bad = false;
if (missRu.length) { bad = true; console.log("нет в RU:\n  " + missRu.join("\n  ")); }
if (missEn.length) { bad = true; console.log("нет в EN:\n  " + missEn.join("\n  ")); }
if (cyr.length) {
  bad = true;
  console.log("кириллица в АНГЛИЙСКОМ словаре (переведи или добавь в BRANDS):");
  console.log(cyr.join("\n"));
}
if (bad) process.exit(1);
console.log(`словари: EN и RU по ${en.length} ключей, английский без кириллицы`);
JS
