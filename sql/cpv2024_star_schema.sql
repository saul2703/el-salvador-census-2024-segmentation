-- =============================================================================
-- El Salvador VII Population & VI Housing Census 2024 (CPV 2024)
-- Relational Star Schema DDL & Analytical Views
-- Official Data Source: Banco Central de Reserva (BCR / ONEC)
--   https://poblacion.bcr.gob.sv/pages/teg-base-de-datos-y-tabulados
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS cpv2024_analytics;

-- 1. DIMENSION TABLE: GEOGRAPHY (14 Departments -> 44 Municipalities -> 262 Districts)
CREATE TABLE cpv2024_analytics.dim_geography (
    dist_code         CHAR(6)      PRIMARY KEY, -- DISTODESC (e.g., '050301' = Antiguo Cuscatlán)
    district_name     VARCHAR(100) NOT NULL,
    muni_code         CHAR(4)      NOT NULL,    -- MUNICDESC (e.g., '0503' = La Libertad Este)
    municipality_name VARCHAR(100) NOT NULL,
    dept_code         CHAR(2)      NOT NULL,    -- DEPTODESC ('01'..'14', '15' = Institutional)
    department_name   VARCHAR(80)  NOT NULL,
    is_institutional  BOOLEAN      NOT NULL DEFAULT FALSE
);

-- 2. DIMENSION TABLE: DWELLINGS (Base de Datos de Viviendas — 2,270,026 rows)
CREATE TABLE cpv2024_analytics.dim_dwellings (
    cod_viv                 CHAR(14) PRIMARY KEY,
    cod_prop                CHAR(12) NOT NULL,
    dist_code               CHAR(6)  NOT NULL REFERENCES cpv2024_analytics.dim_geography(dist_code),
    dwelling_category_code  SMALLINT NOT NULL, -- V01_VIV (1=Private, 2=Collective, 3=Homeless)
    collective_subtype_code SMALLINT,          -- V01_2_VIV_COLECTIVA (3=Penitentiary/Detention)
    occupancy_status_code   SMALLINT NOT NULL, -- V02_VIV_OCUPACION (1=Occupied, 2=Unoccupied)
    wall_material_code      SMALLINT,          -- V03_VIV_PARED (1=Concrete/Brick, 99=Unknown)
    floor_material_code     SMALLINT,          -- V05_VIV_PISO (7=Earth/Dirt Floor, 9=Unknown)
    sanitation_type_code    SMALLINT,          -- V08_VIV_TIPO_SANIT (1..3=Flush Sewer/Septic)
    piped_water_code        SMALLINT,          -- V11_VIV_AGUA (1=Inside Dwelling, 2=Yard, 3=None)
    electricity_source_code SMALLINT,          -- V13_VIV_FUENTE_ENERGIA (1=Grid, 2=Solar, 7=None)
    cooking_fuel_code       SMALLINT,          -- V14_VIV_COMBUSTIBLE_COCINA (1=LPG, 2=Elec, 3=Firewood)
    area_code               SMALLINT NOT NULL, -- AREA (1=Urban, 2=Rural)
    ghs_dug_l1              SMALLINT NOT NULL  -- GHS_DUG_L1 (1=Rural, 2=Semi-dense, 3=Cities)
);

-- 3. DIMENSION TABLE: HOUSEHOLDS (Base de Datos de Hogares — 1,920,668 rows)
CREATE TABLE cpv2024_analytics.dim_households (
    cod_hog               CHAR(16) PRIMARY KEY,
    cod_viv               CHAR(14) NOT NULL REFERENCES cpv2024_analytics.dim_dwellings(cod_viv),
    dist_code             CHAR(6)  NOT NULL REFERENCES cpv2024_analytics.dim_geography(dist_code),
    bedrooms_count        SMALLINT NOT NULL, -- H01_HOG_DORMITORIOS (0..15)
    household_members     SMALLINT NOT NULL, -- P01_PERSONAS
    asset_count_0_to_15   SMALLINT NOT NULL, -- Sum of H02_1..H02_15 where value = 1
    has_internet          BOOLEAN,           -- H02_11_HOG_INTERNET (1=TRUE, 2=FALSE, 9=NULL)
    has_computer          BOOLEAN,           -- H02_8_HOG_COMPU
    has_automobile        BOOLEAN,           -- H02_13_HOG_AUTO
    has_air_conditioning  BOOLEAN,           -- H02_5_HOG_AC
    receives_remittances  BOOLEAN,           -- E02_EMI_AYUDA (1=TRUE, 2=FALSE)
    has_emigrant_2014_24  BOOLEAN,           -- E01_EMI_EMIGRACION (1=TRUE, 2=FALSE)
    family_structure_code SMALLINT NOT NULL  -- HOGAR_TIPO (1=Single, 2=Couple, 3=SingleParent, 4=Nuclear, 5=Extended)
);

-- 4. FACT TABLE: POPULATION (Base de Datos de Población — 6,029,976 rows)
CREATE TABLE cpv2024_analytics.fact_population (
    cod_per              CHAR(21) PRIMARY KEY,
    cod_hog              CHAR(16) NOT NULL,
    dist_code            CHAR(6)  NOT NULL REFERENCES cpv2024_analytics.dim_geography(dist_code),
    relationship_code    SMALLINT NOT NULL, -- P02_1_PARENTESCO (1=Head, 14=Collective Resident)
    sex_code             SMALLINT NOT NULL, -- P02_2_SEXO (1=Male, 2=Female)
    age_years            SMALLINT NOT NULL, -- P02_3_EDAD (0..125)
    age_group_5yr        SMALLINT NOT NULL, -- GRUPO_ETARIO (0='0-4' .. 19='95+')
    highest_edu_level    SMALLINT,          -- P10_GRADO_APROBADO (1..12)
    schooling_years      SMALLINT,          -- P10_2_ANIOS_ESTUDIOS (0..23)
    is_literate          BOOLEAN,           -- P12_ANALFABETISMO (1=TRUE, 2=FALSE)
    speaks_english       BOOLEAN,           -- P12_3_A_ENG (1=TRUE, 2=FALSE)
    has_severe_disab     BOOLEAN,           -- P13A_DISCAPACIDAD (1=TRUE, 2=FALSE)
    uses_smartphone      BOOLEAN,           -- P14_4_USO_TIC_SMARTPHONE
    uses_internet        BOOLEAN,           -- P14_6_USO_TIC_INTERNET
    econ_active_code     SMALLINT,          -- PET (1=Active PEA, 2=Inactive PEI)
    employment_code      SMALLINT,          -- PEA_OCUP (1=Employed, 2=Unemployed)
    live_births_total    SMALLINT,          -- P24_3_NAC_TOTAL
    area_code            SMALLINT NOT NULL  -- AREA (1=Urban, 2=Rural)
);

-- 5. FACT TABLE: INTERNATIONAL EMIGRATION (Base de Datos de Emigración — 226,808 rows)
CREATE TABLE cpv2024_analytics.fact_international_emigration (
    cod_emig                 CHAR(18) PRIMARY KEY,
    cod_hog                  CHAR(16) NOT NULL,
    dist_code                CHAR(6)  NOT NULL REFERENCES cpv2024_analytics.dim_geography(dist_code),
    sex_code                 SMALLINT NOT NULL, -- E01_3_EMI_SEXO (1=Male, 2=Female)
    current_age_years        SMALLINT,          -- E01_4_EMI_EDAD (0..125)
    departure_year           SMALLINT,          -- E01_5_EMI_ANO (2014..2024)
    destination_country_code SMALLINT,          -- E01_6_EMI_PAIS (1=USA, 4=Spain, 5=Italy, 6=Mexico...)
    emigration_reason_code   SMALLINT           -- E01_7_EMI_CAUSA (1=Job Search, 3=Family, 6=Insecurity...)
);

-- 6. FACT TABLE: MORTALITY LAST 12 MONTHS (Base de Datos de Mortalidad — 63,267 rows)
CREATE TABLE cpv2024_analytics.fact_mortality (
    cod_mort            CHAR(18) PRIMARY KEY,
    cod_hog             CHAR(16) NOT NULL,
    dist_code           CHAR(6)  NOT NULL REFERENCES cpv2024_analytics.dim_geography(dist_code),
    sex_code            SMALLINT NOT NULL, -- M01_3_MORT_SEXO_FALLECIDOS
    age_at_death        SMALLINT,          -- M01_4_MORT_EDAD_FALLECIDOS
    is_external_cause   BOOLEAN,           -- M01_5_MORT_CAUSA_FALL (Accident/Homicide/Suicide)
    maternal_death_code SMALLINT           -- M01_6_MORT_DURANTE_FALLECIDOS
);

-- =============================================================================
-- ANALYTICAL VIEW 1: 262-DISTRICT K-MEANS++ FEATURE VECTOR (EXCLUDES CODE 150101)
-- =============================================================================
CREATE OR REPLACE VIEW cpv2024_analytics.vw_district_kmeans_features AS
SELECT
    g.dist_code,
    g.district_name,
    g.muni_code,
    g.municipality_name,
    g.dept_code,
    g.department_name,
    COUNT(p.cod_per)                                                       AS population,
    ROUND(AVG(p.age_years)::NUMERIC, 2)                                    AS mean_age,
    ROUND(AVG(CASE WHEN p.age_years >= 18 THEN p.schooling_years END)::NUMERIC, 2) AS mean_schooling_yrs,
    ROUND(100.0 * AVG(CASE WHEN p.age_years >= 10 THEN p.is_literate::INT END)::NUMERIC, 2) AS literacy_rate_pct
FROM cpv2024_analytics.dim_geography g
JOIN cpv2024_analytics.fact_population p ON p.dist_code = g.dist_code
WHERE g.is_institutional = FALSE
GROUP BY g.dist_code, g.district_name, g.muni_code, g.municipality_name, g.dept_code, g.department_name;

-- =============================================================================
-- ANALYTICAL VIEW 2: THE REMITTANCE-ASSET PARADOX BY MUNICIPALITY (44 MUNIS)
-- =============================================================================
CREATE OR REPLACE VIEW cpv2024_analytics.vw_remittance_asset_paradox AS
SELECT
    g.muni_code,
    g.municipality_name,
    g.department_name,
    COUNT(h.cod_hog)                                          AS total_households,
    ROUND(AVG(h.asset_count_0_to_15)::NUMERIC, 2)             AS mean_household_assets,
    ROUND(100.0 * AVG(h.has_internet::INT)::NUMERIC, 2)       AS internet_penetration_pct,
    ROUND(100.0 * AVG(h.receives_remittances::INT)::NUMERIC, 2) AS remittance_household_pct
FROM cpv2024_analytics.dim_geography g
JOIN cpv2024_analytics.dim_households h ON h.dist_code = g.dist_code
WHERE g.is_institutional = FALSE
GROUP BY g.muni_code, g.municipality_name, g.department_name
ORDER BY remittance_household_pct DESC;
