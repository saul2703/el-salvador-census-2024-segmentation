# Product Context — El Salvador 2024 Census (`CPV2024SV`) Analytics & K-Means++ Workbench

## Purpose & Audience
An operational demographic, infrastructure, and territory-segmentation workbench built for public-policy planners, infrastructure analysts, and data science hiring managers evaluating the official **VII Population & VI Housing Census 2024 (BCR / ONEC)** of El Salvador (**10,510,745 total microdata records** across 5 relational tables).

## Surface Mode
**Operate** (with structured **Read** reference tabs). Analysts use the workbench to:
1. Reconcile the official **6,029,976** population across **262 residential districts** (`5,922,921`) and **14 collective penitentiary records** (`107,055` in code `150101`).
2. Filter, sort, and export K-Means++ district segments ($k=4$, 8 standardized features, 2D SVD PCA) and 44 restructured municipalities.
3. Inspect Top 5 vs. Bottom 5 district benchmarks alongside the validated CPV 2024 Star Schema SQL and Spanish-to-English translated survey dictionary.
