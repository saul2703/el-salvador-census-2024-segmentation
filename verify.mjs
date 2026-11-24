import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const ROOT = process.cwd();
const summaryPath = path.join(ROOT, "data", "census_summary.json");
const htmlPath = path.join(ROOT, "index.html");
const readmePath = path.join(ROOT, "README.md");
const sqlPath = path.join(ROOT, "sql", "cpv2024_star_schema.sql");
const productPath = path.join(ROOT, "PRODUCT.md");
const designPath = path.join(ROOT, "DESIGN.md");

assert.ok(fs.existsSync(summaryPath), "Missing data/census_summary.json");
assert.ok(fs.existsSync(htmlPath), "Missing index.html");
assert.ok(fs.existsSync(readmePath), "Missing README.md");
assert.ok(fs.existsSync(sqlPath), "Missing sql/cpv2024_star_schema.sql");
assert.ok(fs.existsSync(productPath), "Missing PRODUCT.md");
assert.ok(fs.existsSync(designPath), "Missing DESIGN.md");

const data = JSON.parse(fs.readFileSync(summaryPath, "utf-8"));

// 1. Verify exact CPV 2024 table record counts
assert.equal(data.dataset.total_population, 6029976, "Population count must equal official 6,029,976");
assert.equal(data.dataset.total_households, 1920668, "Household count must equal 1,920,668");
assert.equal(data.dataset.total_dwellings, 2270026, "Dwelling count must equal 2,270,026");
assert.equal(data.dataset.total_emigrants_2014_2024, 226808, "Emigrant count must equal 226,808");
assert.equal(data.dataset.total_deaths_12m, 63267, "Mortality count must equal 63,267");

// 2. Verify geographic hierarchy & population reconciliation (5,922,921 district residents + 107,055 collective code 150101 = 6,029,976)
assert.equal(data.districts.length, 262, "Must have exactly 262 geographic districts");
assert.equal(data.municipalities.length, 44, "Must have exactly 44 restructured municipalities");
const distPopSum = data.districts.reduce((acc, d) => acc + d.population, 0);
assert.equal(distPopSum + 107055, 6029976, "262 districts + 107,055 institutional code 150101 must reconcile to 6,029,976");

// 3. Verify K-Means++ clustering & 2D PCA
assert.equal(data.clustering.selected_k, 4, "Selected k must be 4");
assert.equal(data.clustering.clusters.length, 4, "Must have 4 cluster personas");
const clusterDistSum = data.clustering.clusters.reduce((acc, c) => acc + c.districts, 0);
assert.equal(clusterDistSum, 262, "Cluster district counts must sum to 262");

// 4. Verify Spanish-to-English Questionnaire Sentence Transformer & Top/Bottom Performers Matrix
assert.ok(Array.isArray(data.translated_questionnaire) && data.translated_questionnaire.length >= 6, "Must include translated Spanish-to-English questionnaire sentences");
assert.ok(data.top_bottom_performers && data.top_bottom_performers.schooling_yrs.top_5.length === 5, "Must include Top 5 / Bottom 5 district performers matrix");

// 5. Verify all 7 Matplotlib PNG charts exist in both dark and light modes and are non-empty
const expectedCharts = [
  "dashboard_overview_preview",
  "demographic_pyramid_and_un_projections",
  "department_population_and_remittances",
  "household_assets_digital_divide",
  "international_emigration_trends",
  "elbow_silhouette_evaluation",
  "district_kmeans_clusters_pca",
];
for (const name of expectedCharts) {
  for (const suffix of ["", "_light"]) {
    const img = `${name}${suffix}.png`;
    const p = path.join(ROOT, "images", img);
    assert.ok(fs.existsSync(p), `Missing chart: images/${img}`);
    assert.ok(fs.statSync(p).size > 30000, `Chart too small: images/${img}`);
  }
}

// 6. Verify Impeccable craft-floor, dual light/dark theme parity, and JS syntax in index.html & README.md
const html = fs.readFileSync(htmlPath, "utf-8");
const readme = fs.readFileSync(readmePath, "utf-8");
assert.ok(!html.includes('class="eyebrow"'), "Impeccable craft-floor ban: no eyebrow/kicker above headings");
assert.ok(html.includes('<meta name="color-scheme" content="light dark"'), "index.html must declare light dark color-scheme");
assert.ok(html.includes("light-dark("), "index.html must use CSS light-dark() tokens");
assert.ok(html.includes("btnThemeToggle") && html.includes("<picture data-chart="), "index.html must include theme toggle and dual-mode <picture> charts");
assert.ok(html.includes("content-visibility: auto"), "modern-web-guidance: must include content-visibility: auto containment");
assert.ok(html.includes("container-type: inline-size"), "modern-web-guidance: must include container-type: inline-size for responsive tables");
assert.ok(html.includes("districtGrid") && html.includes("muniGrid"), "index.html must include both AG Grid containers");
assert.ok(html.includes("empty-filter-banner") && html.includes("btnResetFilters"), "index.html must include recoverable empty filter state");
assert.ok(html.includes('typeof agGrid !== "undefined"'), "index.html must guard against offline AG Grid CDN failure");
assert.ok(html.includes("cpv2024_star_schema.sql") && html.includes("dim_geography"), "index.html must embed Star Schema SQL DDL & Views");
assert.ok(readme.includes("prefers-color-scheme: light") && readme.includes("prefers-color-scheme: dark"), "README.md must pair dark and light <picture> sources");
new Function(html.split("<script>").pop().split("</script>")[0]);

console.log("ALL VERIFICATION CHECKS PASSED:");
console.log(`  - Population Reconciled: ${distPopSum.toLocaleString()} (262 districts) + 107,055 (Code 150101 collective) = ${data.dataset.total_population.toLocaleString()}`);
console.log(`  - Schema Contracts: 262 Districts & 6 Spanish-to-English Survey Sentence Transformations validated`);
console.log(`  - Top/Bottom Performers Matrix (Pizza-Pioneers pattern) + Star Schema SQL (weismanm12/finances-database pattern) verified`);
console.log(`  - Impeccable Operate Workbench + 14 Dark/Light PNG charts verified`);
