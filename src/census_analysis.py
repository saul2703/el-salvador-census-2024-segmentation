from dataclasses import dataclass, asdict
from pathlib import Path
import json
import re
import time
import zipfile
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

# ==============================================================================
# TYPED SCHEMA CONTRACT & SPANISH-TO-ENGLISH QUESTIONNAIRE SENTENCE TRANSFORMER
# (Pydantic/Pandera-equivalent zero-dependency dataclass validator + NLP translator)
# ==============================================================================
@dataclass(frozen=True)
class DistrictSchemaContract:
    dist_code: str
    district: str
    municipality: str
    department: str
    cluster_id: int
    population: int
    households: int
    mean_schooling_yrs: float
    literacy_pct: float
    mean_hh_assets: float
    internet_pct: float
    piped_water_pct: float
    firewood_pct: float
    remittance_pct: float

    def validate(self) -> None:
        if not re.match(r"^(0[1-9]|1[0-4])\d{4}$", self.dist_code):
            raise ValueError(f"Invalid 6-digit district code: {self.dist_code}")
        if self.population <= 0 or self.households <= 0:
            raise ValueError(f"Non-positive population/households in {self.dist_code}")
        if not (0.0 <= self.mean_schooling_yrs <= 23.0):
            raise ValueError(f"Schooling out of bounds [0,23] in {self.dist_code}")
        if not (0.0 <= self.mean_hh_assets <= 15.0):
            raise ValueError(f"Household assets out of bounds [0,15] in {self.dist_code}")
        for pct_name, val in [
            ("literacy_pct", self.literacy_pct),
            ("internet_pct", self.internet_pct),
            ("piped_water_pct", self.piped_water_pct),
            ("firewood_pct", self.firewood_pct),
            ("remittance_pct", self.remittance_pct),
        ]:
            if not (0.0 <= val <= 100.0):
                raise ValueError(f"{pct_name}={val} out of bounds [0,100] in {self.dist_code}")

SPANISH_TO_ENGLISH_SENTENCES = [
    {
        "table": "Viviendas (Dwellings)",
        "mnemonic": "V02_VIV_OCUPACION & V01_2_VIV_COLECTIVA",
        "spanish_sentence": "2. La vivienda está: 1. Ocupada / 2. Desocupada | 1.2. Colectiva: 3. Centro penal o readaptación de menores",
        "english_sentence": "Is the dwelling occupied or unoccupied, and if collective, is it a penitentiary or juvenile detention center?",
        "transformation_rule": "Separate code 150101 (14 penitentiary records housing 107,055 inmates) from the 262 residential districts (5,922,921 residents).",
    },
    {
        "table": "Viviendas (Dwellings)",
        "mnemonic": "V11_VIV_AGUA & V14_VIV_COMBUSTIBLE_COCINA",
        "spanish_sentence": "11. ¿En esta vivienda cuentan con tubería o chorro dentro de la vivienda? | 14. ¿Cuál es el principal combustible que utilizan para cocinar?",
        "english_sentence": "Does this dwelling have piped water indoors, and what is the primary fuel used for cooking (LPG, Electricity, or Firewood)?",
        "transformation_rule": "Map V11_VIV_AGUA == 1 over valid (1..3) to piped_water_pct; map V14 == 3 over occupied dwellings to firewood_cooking_pct.",
    },
    {
        "table": "Hogares (Households)",
        "mnemonic": "H02_1_HOG_REFRI .. H02_15_HOG_MOTOCICLETA",
        "spanish_sentence": "2.1–2.15. En este hogar, ¿tienen refrigeradora, lavadora, internet residencial, computadora, automóvil, aire acondicionado...?",
        "english_sentence": "Does this household own each of the 15 tracked durable appliances, digital services, and transport vehicles?",
        "transformation_rule": "Recode 1=Yes -> 1, 2=No -> 0, and exclude 9=Unknown sentinels to construct continuous mean_hh_assets (0–15 scale) and internet_pct.",
    },
    {
        "table": "Hogares (Households)",
        "mnemonic": "E02_EMI_AYUDA & E01_EMI_EMIGRACION",
        "spanish_sentence": "Alguna persona miembro del hogar en los últimos 12 meses, ¿ha recibido remesa en dinero de alguien que reside en otro país?",
        "english_sentence": "Has any member of this household received cash remittances from someone residing abroad during the past 12 months?",
        "transformation_rule": "Compute remittance_hh_pct = 100 * count(E02 == 1) / count(E02 in {1,2}) per district and municipality.",
    },
    {
        "table": "Población (Population)",
        "mnemonic": "P10_2_ANIOS_ESTUDIOS & P12_ANALFABETISMO",
        "spanish_sentence": "10.2. Años de estudio aprobados en el sistema educativo formal | 12. ¿Sabe leer y escribir?",
        "english_sentence": "How many years of formal schooling has the person completed (0–23), and can the person read and write?",
        "transformation_rule": "Filter to adults aged 18+ for mean_schooling_yrs and ages 10+ with valid (1,2) responses for literacy_rate_pct.",
    },
    {
        "table": "Emigración Internacional",
        "mnemonic": "E01_5_EMI_ANO, E01_6_EMI_PAIS & E01_7_EMI_CAUSA",
        "spanish_sentence": "1.5. ¿En qué año salió de El Salvador? | 1.6. ¿Cuál es el país de residencia actual? | 1.7. ¿Por qué se fue a vivir a otro país?",
        "english_sentence": "In what year (2014–2024) did the emigrant leave El Salvador, what is their destination country, and what was their primary reason?",
        "transformation_rule": "Map country codes (1=United States [85.9%], 4=Spain [4.0%]...) and cause codes (1=Job Search [57.0%], 3=Family [14.9%], 6=Insecurity [14.4%]).",
    },
]

ROOT_DIR = Path(__file__).resolve().parent.parent
ZIP_PATH = ROOT_DIR / "Bases-Finales-CPV2024SV-CSV.zip"
OUT_DIR = ROOT_DIR / "data"
IMG_DIR = ROOT_DIR / "images"
OUT_DIR.mkdir(parents=True, exist_ok=True)
IMG_DIR.mkdir(parents=True, exist_ok=True)

def num(series):
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64)

# ==============================================================================
# 1. OFFICIAL EL SALVADOR GEOGRAPHIC CATALOG (14 DEPTS, 44 MUNIS, 262 DISTRICTS)
# ==============================================================================
DEPT_NAMES = {
    "01": "Ahuachapán",
    "02": "Santa Ana",
    "03": "Sonsonate",
    "04": "Chalatenango",
    "05": "La Libertad",
    "06": "San Salvador",
    "07": "Cuscatlán",
    "08": "La Paz",
    "09": "Cabañas",
    "10": "San Vicente",
    "11": "Usulután",
    "12": "San Miguel",
    "13": "Morazán",
    "14": "La Unión",
    "15": "Special Territorial Zone",
}

MUNI_NAMES = {
    "0101": "Ahuachapán Centro", "0102": "Ahuachapán Norte", "0103": "Ahuachapán Sur",
    "0201": "Santa Ana Centro", "0202": "Santa Ana Este", "0203": "Santa Ana Norte", "0204": "Santa Ana Oeste",
    "0301": "Sonsonate Centro", "0302": "Sonsonate Este", "0303": "Sonsonate Norte", "0304": "Sonsonate Oeste",
    "0401": "Chalatenango Centro", "0402": "Chalatenango Norte", "0403": "Chalatenango Sur",
    "0501": "La Libertad Centro", "0502": "La Libertad Costa", "0503": "La Libertad Este",
    "0504": "La Libertad Norte", "0505": "La Libertad Oeste", "0506": "La Libertad Sur",
    "0601": "San Salvador Centro", "0602": "San Salvador Este", "0603": "San Salvador Norte",
    "0604": "San Salvador Oeste", "0605": "San Salvador Sur",
    "0701": "Cuscatlán Norte", "0702": "Cuscatlán Sur",
    "0801": "La Paz Centro", "0802": "La Paz Este", "0803": "La Paz Oeste",
    "0901": "Cabañas Este", "0902": "Cabañas Oeste",
    "1001": "San Vicente Norte", "1002": "San Vicente Sur",
    "1101": "Usulután Este", "1102": "Usulután Norte", "1103": "Usulután Oeste",
    "1201": "San Miguel Centro", "1202": "San Miguel Norte", "1203": "San Miguel Oeste",
    "1301": "Morazán Norte", "1302": "Morazán Sur",
    "1401": "La Unión Norte", "1402": "La Unión Sur",
    "1501": "Bolsones / Border Zone",
}

MUNI_DISTRICTS_ORDERED = {
    "0101": ["Ahuachapán", "Apaneca", "Concepción de Ataco", "Tacuba"],
    "0102": ["Atiquizaya", "El Refugio", "San Lorenzo", "Turín"],
    "0103": ["Guaymango", "Jujutla", "San Francisco Menéndez", "San Pedro Puxtla"],
    "0201": ["Santa Ana"],
    "0202": ["Coatepeque", "El Congo"],
    "0203": ["Masahuat", "Metapán", "Santa Rosa Guachipilín", "Texistepeque"],
    "0204": ["Candelaria de la Frontera", "Chalchuapa", "El Porvenir", "San Antonio Pajonal", "San Sebastián Salitrillo", "Santiago de la Frontera"],
    "0301": ["Nahulingo", "San Antonio del Monte", "Santo Domingo de Guzmán", "Sonsonate", "Sonzacate"],
    "0302": ["Armenia", "Caluco", "Cuisnahuat", "Izalco", "San Julián", "Santa Isabel Ishuatán"],
    "0303": ["Juayúa", "Nahuizalco", "Salcoatitán", "Santa Catarina Masahuat"],
    "0304": ["Acajutla"],
    "0401": ["Agua Caliente", "Dulce Nombre de María", "El Paraíso", "La Reina", "Nueva Concepción", "San Fernando", "San Francisco Morazán", "San Rafael", "Santa Rita", "Tejutla"],
    "0402": ["Citalá", "La Palma", "San Ignacio"],
    "0403": ["Arcatao", "Azacualpa", "Chalatenango", "Comalapa", "Concepción Quezaltepeque", "El Carrizal", "La Laguna", "Las Vueltas", "Nombre de Jesús", "Nueva Trinidad", "Ojos de Agua", "Potonico", "San Antonio de la Cruz", "San Antonio Los Ranchos", "San Francisco Lempa", "San Isidro Labrador", "San José Cancasque", "San José Las Flores", "San Luis del Carmen", "San Miguel de Mercedes"],
    "0501": ["Ciudad Arce", "San Juan Opico"],
    "0502": ["Chiltiupán", "Jicalapa", "La Libertad", "Tamanique", "Teotepeque"],
    "0503": ["Antiguo Cuscatlán", "Huizúcar", "Nuevo Cuscatlán", "San José Villanueva", "Zaragoza"],
    "0504": ["Quezaltepeque", "San Matías", "San Pablo Tacachico"],
    "0505": ["Colón", "Jayaque", "Sacacoyo", "Talnique", "Tepecoyo"],
    "0506": ["Comasagua", "Santa Tecla"],
    "0601": ["Ayutuxtepeque", "Cuscatancingo", "Mejicanos", "San Salvador", "Ciudad Delgado"],
    "0602": ["Ilopango", "San Martín", "Soyapango", "Tonacatepeque"],
    "0603": ["Aguilares", "El Paisnal", "Guazapa"],
    "0604": ["Apopa", "Nejapa"],
    "0605": ["Panchimalco", "Rosario de Mora", "San Marcos", "Santiago Texacuangos", "Santo Tomás"],
    "0701": ["Oratorio de Concepción", "San Bartolomé Perulapía", "San José Guayabal", "San Pedro Perulapán", "Suchitoto"],
    "0702": ["Candelaria", "Cojutepeque", "El Carmen", "El Rosario", "Monte San Juan", "San Cristóbal", "San Rafael Cedros", "San Ramón", "Santa Cruz Analquito", "Santa Cruz Michapa", "Tenancingo"],
    "0801": ["El Rosario", "Jerusalén", "Mercedes La Ceiba", "Paraíso de Osorio", "San Antonio Masahuat", "San Emigdio", "San Juan Tepezontes", "San Luis La Herradura", "San Miguel Tepezontes", "San Pedro Nonualco", "Santa María Ostuma", "Santiago Nonualco"],
    "0802": ["San Juan Nonualco", "San Rafael Obrajuelo", "Zacatecoluca"],
    "0803": ["Cuyultitán", "Olocuilta", "San Francisco Chinameca", "San Juan Talpa", "San Luis Talpa", "San Pedro Masahuat", "Tapalhuaca"],
    "0901": ["Dolores", "Guacotecti", "San Isidro", "Sensuntepeque", "Victoria"],
    "0902": ["Cinquera", "Ilobasco", "Jutiapa", "Tejutepeque"],
    "1001": ["Apastepeque", "San Esteban Catarina", "San Ildefonso", "San Lorenzo", "San Sebastián", "Santa Clara", "Santo Domingo"],
    "1002": ["Guadalupe", "San Cayetano Istepeque", "San Vicente", "Tecoluca", "Tepetitán", "Verapaz"],
    "1101": ["California", "Concepción Batres", "Ereguayquín", "Jucuarán", "Ozatlán", "San Dionisio", "Santa Elena", "Santa María", "Tecapán", "Usulután"],
    "1102": ["Alegría", "Berlín", "El Triunfo", "Estanzuelas", "Jucuapa", "Mercedes Umaña", "Nueva Granada", "San Buenaventura", "Santiago de María"],
    "1103": ["Jiquilisco", "Puerto El Triunfo", "San Agustín", "San Francisco Javier"],
    "1201": ["Chirilagua", "Comacarán", "Moncagua", "Quelepa", "San Miguel", "Uluazapa"],
    "1202": ["Carolina", "Chapeltique", "Ciudad Barrios", "Nuevo Edén de San Juan", "San Antonio", "San Gerardo", "San Luis de la Reina", "Sesori"],
    "1203": ["Chinameca", "El Tránsito", "Lolotique", "Nueva Guadalupe", "San Jorge", "San Rafael Oriente"],
    "1301": ["Arambala", "Cacaopera", "Corinto", "El Rosario", "Joateca", "Jocoaitique", "Meanguera", "Perquín", "San Fernando", "San Isidro", "Torola"],
    "1302": ["Chilanga", "Delicias de Concepción", "El Divisadero", "Gualococti", "Guatajiagua", "Jocoro", "Lolotiquillo", "Osicala", "San Carlos", "San Francisco Gotera", "San Simón", "Sensembra", "Sociedad", "Yamabal", "Yoloaiquín"],
    "1401": ["Anamorós", "Bolívar", "Concepción de Oriente", "El Sauce", "Lislique", "Nueva Esparta", "Pasaquina", "Polorós", "San José", "Santa Rosa de Lima"],
    "1402": ["Conchagua", "El Carmen", "Intipucá", "La Unión", "Meanguera del Golfo", "San Alejo", "Yayantique", "Yucuaiquín"],
    "1501": ["Bolsones Territory"],
}

EMIG_COUNTRY_NAMES = {
    1: "United States", 2: "Canada", 3: "Australia", 4: "Spain", 5: "Italy",
    6: "Mexico", 7: "Guatemala", 8: "Costa Rica", 9: "Belize", 10: "Honduras", 11: "Other Country",
}

EMIG_REASON_NAMES = {
    1: "Job Search / Employment", 2: "Marriage / Partner", 3: "Family Reunification",
    4: "Job Offer Abroad", 5: "Education / Study", 6: "Insecurity or Violence",
    7: "Natural Disasters", 8: "Deportation", 9: "Other Reason",
}

AGE_GROUP_LABELS = {
    0: "0-4", 1: "5-9", 2: "10-14", 3: "15-19", 4: "20-24",
    5: "25-29", 6: "30-34", 7: "35-39", 8: "40-44", 9: "45-49",
    10: "50-54", 11: "55-59", 12: "60-64", 13: "65-69", 14: "70-74",
    15: "75-79", 16: "80-84", 17: "85-89", 18: "90-94", 19: "95+",
}

t0 = time.time()
print(f"Opening {ZIP_PATH.name} ({ZIP_PATH.stat().st_size / (1024**2):.1f} MB)...")

with zipfile.ZipFile(ZIP_PATH, "r") as z:
    # ==========================================================================
    # STEP 1: STREAM VIVIENDAS (DWELLINGS) - 2.27M ROWS
    # ==========================================================================
    print("\n[1/5] Streaming Viviendas (Dwellings)...")
    viv_cols = [
        "DEPTODESC", "MUNICDESC", "DISTODESC", "V02_VIV_OCUPACION",
        "V03_VIV_PARED", "V05_VIV_PISO", "V08_VIV_TIPO_SANIT",
        "V11_VIV_AGUA", "V13_VIV_FUENTE_ENERGIA", "V14_VIV_COMBUSTIBLE_COCINA",
        "V15_VIV_ELIM_BASURA", "AREA"
    ]
    viv_frames = []
    total_viv_rows = 0
    with z.open("Base de Datos de Viviendas - CPV 2024 SV.csv") as f:
        for ch in pd.read_csv(f, usecols=viv_cols, dtype=str, chunksize=500_000):
            total_viv_rows += len(ch)
            dist = ch["DISTODESC"].str.strip().str.zfill(6)
            muni = ch["MUNICDESC"].str.strip().str.zfill(4)
            dept = ch["DEPTODESC"].str.strip().str.zfill(2)
            ocup = num(ch["V02_VIV_OCUPACION"])
            pared = num(ch["V03_VIV_PARED"])
            piso = num(ch["V05_VIV_PISO"])
            sanit = num(ch["V08_VIV_TIPO_SANIT"])
            agua = num(ch["V11_VIV_AGUA"])
            elec = num(ch["V13_VIV_FUENTE_ENERGIA"])
            lena = num(ch["V14_VIV_COMBUSTIBLE_COCINA"])
            basura = num(ch["V15_VIV_ELIM_BASURA"])
            area = num(ch["AREA"])

            tmp = pd.DataFrame({
                "DISTODESC": dist,
                "MUNICDESC": muni,
                "DEPTODESC": dept,
                "dwellings_total": 1,
                "dwellings_occupied": (ocup == 1).astype(np.int32),
                "dwellings_unoccupied": (ocup == 2).astype(np.int32),
                "urban_dwellings": (area == 1).astype(np.int32),
                "concrete_wall": (pared == 1).astype(np.int32),
                "dirt_floor": (piso == 7).astype(np.int32),
                "flush_sewer": np.isin(sanit, [1, 2, 3]).astype(np.int32),
                "piped_water": (agua == 1).astype(np.int32),
                "water_valid": np.isin(agua, [1, 2, 3]).astype(np.int32),
                "electric_grid": np.isin(elec, [1, 2]).astype(np.int32),
                "firewood_cooking": (lena == 3).astype(np.int32),
                "muni_garbage": np.isin(basura, [1, 2]).astype(np.int32),
            })
            viv_frames.append(tmp.groupby(["DEPTODESC", "MUNICDESC", "DISTODESC"], observed=True).sum().reset_index())

    viv_dist = pd.concat(viv_frames, ignore_index=True).groupby(["DEPTODESC", "MUNICDESC", "DISTODESC"], observed=True).sum().reset_index()
    print(f"  -> Viviendas rows: {total_viv_rows:,} across {len(viv_dist)} districts ({time.time() - t0:.1f}s)")

    DISTRICT_NAMES = {}
    for muni_code, grp in viv_dist.groupby("MUNICDESC"):
        sorted_codes = sorted(grp["DISTODESC"].unique().tolist())
        names_list = MUNI_DISTRICTS_ORDERED.get(muni_code, [])
        for idx_d, d_code in enumerate(sorted_codes):
            if idx_d < len(names_list):
                DISTRICT_NAMES[d_code] = names_list[idx_d]
            else:
                DISTRICT_NAMES[d_code] = f"District {d_code}"

    # ==========================================================================
    # STEP 2: STREAM HOGARES (HOUSEHOLDS)
    # ==========================================================================
    print("\n[2/5] Streaming Hogares (Households)...")
    asset_cols = [
        "H02_1_HOG_REFRI", "H02_2_HOG_LAVADORA", "H02_3_HOG_MICRO",
        "H02_4_HOG_COCINA", "H02_5_HOG_AC", "H02_6_HOG_TV",
        "H02_7_HOG_CEL", "H02_8_HOG_COMPU", "H02_9_HOG_TABLET",
        "H02_10_HOG_CABLETV", "H02_11_HOG_INTERNET", "H02_12_HOG_PLATAFORMAS",
        "H02_13_HOG_AUTO", "H02_14_HOG_BICICLETA", "H02_15_HOG_MOTOCICLETA",
    ]
    hog_cols = [
        "DEPTODESC", "MUNICDESC", "DISTODESC", "H01_HOG_DORMITORIOS",
        "P01_PERSONAS", "E01_EMI_EMIGRACION", "E02_EMI_AYUDA", "AREA"
    ] + asset_cols

    hog_frames = []
    total_hog_rows = 0
    asset_totals = {c: 0 for c in asset_cols}
    asset_valid_totals = {c: 0 for c in asset_cols}

    with z.open("Base de Datos de Hogares - CPV 2024 SV.csv") as f:
        for ch in pd.read_csv(f, usecols=hog_cols, dtype=str, chunksize=500_000):
            total_hog_rows += len(ch)
            dist = ch["DISTODESC"].str.strip().str.zfill(6)
            dorm = np.nan_to_num(num(ch["H01_HOG_DORMITORIOS"]), nan=0.0)
            pers = np.nan_to_num(num(ch["P01_PERSONAS"]), nan=0.0)
            emig_h = num(ch["E01_EMI_EMIGRACION"])
            remit = num(ch["E02_EMI_AYUDA"])

            asset_count = np.zeros(len(ch), dtype=np.int32)
            for ac in asset_cols:
                v = num(ch[ac])
                is_yes = (v == 1).astype(np.int32)
                is_val = np.isin(v, [1, 2]).astype(np.int32)
                asset_count += is_yes
                asset_totals[ac] += int(is_yes.sum())
                asset_valid_totals[ac] += int(is_val.sum())

            net_v = num(ch["H02_11_HOG_INTERNET"])
            comp_v = num(ch["H02_8_HOG_COMPU"])
            auto_v = num(ch["H02_13_HOG_AUTO"])
            ac_v = num(ch["H02_5_HOG_AC"])
            refri_v = num(ch["H02_1_HOG_REFRI"])
            lav_v = num(ch["H02_2_HOG_LAVADORA"])
            stream_v = num(ch["H02_12_HOG_PLATAFORMAS"])

            overcrowded = ((pers / np.maximum(1.0, dorm)) >= 3.0).astype(np.int32)

            tmp = pd.DataFrame({
                "DISTODESC": dist,
                "households_total": 1,
                "hh_persons_sum": pers.astype(np.int64),
                "hh_bedrooms_sum": dorm.astype(np.int64),
                "hh_assets_sum": asset_count,
                "hh_internet_yes": (net_v == 1).astype(np.int32),
                "hh_internet_valid": np.isin(net_v, [1, 2]).astype(np.int32),
                "hh_computer_yes": (comp_v == 1).astype(np.int32),
                "hh_auto_yes": (auto_v == 1).astype(np.int32),
                "hh_ac_yes": (ac_v == 1).astype(np.int32),
                "hh_refri_yes": (refri_v == 1).astype(np.int32),
                "hh_washer_yes": (lav_v == 1).astype(np.int32),
                "hh_streaming_yes": (stream_v == 1).astype(np.int32),
                "hh_remittance_yes": (remit == 1).astype(np.int32),
                "hh_remittance_valid": np.isin(remit, [1, 2]).astype(np.int32),
                "hh_emigrant_yes": (emig_h == 1).astype(np.int32),
                "hh_overcrowded": overcrowded,
            })
            hog_frames.append(tmp.groupby("DISTODESC", observed=True).sum().reset_index())

    hog_dist = pd.concat(hog_frames, ignore_index=True).groupby("DISTODESC", observed=True).sum().reset_index()
    print(f"  -> Hogares rows: {total_hog_rows:,} ({time.time() - t0:.1f}s)")

    # ==========================================================================
    # STEP 3: STREAM MORTALIDAD & EMIGRACIÓN INTERNACIONAL
    # ==========================================================================
    print("\n[3/5] Streaming Mortalidad & Emigración Internacional...")
    with z.open("Base de Datos de Mortalidad - CPV 2024 SV.csv") as f:
        mort_df = pd.read_csv(f, dtype=str)
    total_mort_rows = len(mort_df)
    mort_df["DISTODESC"] = mort_df["DISTODESC"].str.strip().str.zfill(6)
    mort_df["age_death"] = np.nan_to_num(num(mort_df["M01_4_MORT_EDAD_FALLECIDOS"]), nan=0.0)
    mort_df["external_cause"] = (num(mort_df["M01_5_MORT_CAUSA_FALL"]) == 1).astype(np.int32)
    mort_dist = mort_df.groupby("DISTODESC", observed=True).agg(
        deaths_total=("DISTODESC", "count"),
        deaths_age_sum=("age_death", "sum"),
        deaths_external=("external_cause", "sum"),
    ).reset_index()

    with z.open("Base de Datos de Emigración Internacional - CPV 2024 SV.csv") as f:
        emig_df = pd.read_csv(f, dtype=str)
    total_emig_rows = len(emig_df)
    emig_df["DISTODESC"] = emig_df["DISTODESC"].str.strip().str.zfill(6)
    emig_df["sex"] = num(emig_df["E01_3_EMI_SEXO"])
    emig_df["age"] = num(emig_df["E01_4_EMI_EDAD"])
    emig_df["year"] = num(emig_df["E01_5_EMI_ANO"])
    emig_df["country"] = num(emig_df["E01_6_EMI_PAIS"])
    emig_df["reason"] = num(emig_df["E01_7_EMI_CAUSA"])

    emig_dist = emig_df.groupby("DISTODESC", observed=True).agg(
        emigrants_total=("DISTODESC", "count"),
        emigrants_male=("sex", lambda s: int((s == 1).sum())),
        emigrants_usa=("country", lambda s: int((s == 1).sum())),
    ).reset_index()

    emig_by_year = emig_df[emig_df["year"].between(2014, 2024)].groupby("year").size().to_dict()
    emig_by_reason = emig_df["reason"].map(EMIG_REASON_NAMES).value_counts().to_dict()
    emig_by_country = emig_df["country"].map(EMIG_COUNTRY_NAMES).value_counts().to_dict()
    print(f"  -> Mortalidad: {total_mort_rows:,} rows | Emigración: {total_emig_rows:,} rows ({time.time() - t0:.1f}s)")

    # ==========================================================================
    # STEP 4: STREAM POBLACIÓN (POPULATION) - ~6.03M ROWS
    # ==========================================================================
    print("\n[4/5] Streaming Población (6.03M Individual Records)...")
    pob_cols = [
        "DEPTODESC", "MUNICDESC", "DISTODESC", "P02_2_SEXO", "P02_3_EDAD",
        "P06_INDIGENA", "P08_AFRODESCENDIENTE", "P10_GRADO_APROBADO",
        "P10_2_ANIOS_ESTUDIOS", "P12_ANALFABETISMO", "P12_3_A_ENG",
        "P13A_DISCAPACIDAD", "P14_4_USO_TIC_SMARTPHONE", "P14_6_USO_TIC_INTERNET",
        "PET", "PEA_OCUP", "P24_3_NAC_TOTAL", "GRUPO_ETARIO", "AREA"
    ]
    pob_frames = []
    pyr_frames = []
    total_pob_rows = 0

    with z.open("Base de Datos de Población - CPV 2024 SV.csv") as f:
        for idx_c, ch in enumerate(pd.read_csv(f, usecols=pob_cols, dtype=str, chunksize=750_000)):
            total_pob_rows += len(ch)
            dist = ch["DISTODESC"].str.strip().str.zfill(6)
            sex = num(ch["P02_2_SEXO"])
            age = np.nan_to_num(num(ch["P02_3_EDAD"]), nan=0.0)
            indig = num(ch["P06_INDIGENA"])
            afro = num(ch["P08_AFRODESCENDIENTE"])
            grado = num(ch["P10_GRADO_APROBADO"])
            sch = num(ch["P10_2_ANIOS_ESTUDIOS"])
            lit = num(ch["P12_ANALFABETISMO"])
            eng = num(ch["P12_3_A_ENG"])
            disc = num(ch["P13A_DISCAPACIDAD"])
            smart = num(ch["P14_4_USO_TIC_SMARTPHONE"])
            net_p = num(ch["P14_6_USO_TIC_INTERNET"])
            pet = num(ch["PET"])
            pea_ocup = num(ch["PEA_OCUP"])
            nac = num(ch["P24_3_NAC_TOTAL"])
            et = np.nan_to_num(num(ch["GRUPO_ETARIO"]), nan=-1.0).astype(np.int32)
            area_p = num(ch["AREA"])

            is_adult = (age >= 18) & (~np.isnan(sch)) & (sch <= 25)
            is_age10 = (age >= 10) & np.isin(lit, [1, 2])
            is_fem_15_49 = (sex == 2) & (age >= 15) & (age <= 49)

            tmp = pd.DataFrame({
                "DISTODESC": dist,
                "pop_total": 1,
                "pop_male": (sex == 1).astype(np.int32),
                "pop_female": (sex == 2).astype(np.int32),
                "pop_urban": (area_p == 1).astype(np.int32),
                "age_sum": age.astype(np.int64),
                "pop_0_14": (age <= 14).astype(np.int32),
                "pop_15_64": ((age >= 15) & (age <= 64)).astype(np.int32),
                "pop_65_plus": (age >= 65).astype(np.int32),
                "adult_count": is_adult.astype(np.int32),
                "adult_schooling_sum": np.where(is_adult, np.nan_to_num(sch, nan=0.0), 0.0),
                "univ_degree_adult": ((age >= 18) & np.isin(grado, [9, 10, 11, 12])).astype(np.int32),
                "lit_yes": (is_age10 & (lit == 1)).astype(np.int32),
                "lit_valid": is_age10.astype(np.int32),
                "english_yes": (eng == 1).astype(np.int32),
                "indig_yes": (indig == 1).astype(np.int32),
                "afro_yes": (afro == 1).astype(np.int32),
                "disability_yes": (disc == 1).astype(np.int32),
                "smartphone_yes": (smart == 1).astype(np.int32),
                "internet_user_yes": (net_p == 1).astype(np.int32),
                "pea_active": (pet == 1).astype(np.int32),
                "pet_total": np.isin(pet, [1, 2]).astype(np.int32),
                "employed": (pea_ocup == 1).astype(np.int32),
                "fem_15_49": is_fem_15_49.astype(np.int32),
                "births_fem_15_49": np.where(is_fem_15_49 & (~np.isnan(nac)), np.nan_to_num(nac, nan=0.0), 0.0),
            })
            pob_frames.append(tmp.groupby("DISTODESC", observed=True).sum().reset_index())

            pyr_tmp = pd.DataFrame({
                "GRUPO_ETARIO": et,
                "male": (sex == 1).astype(np.int32),
                "female": (sex == 2).astype(np.int32),
            })
            pyr_frames.append(pyr_tmp.groupby("GRUPO_ETARIO", observed=True).sum().reset_index())
            print(f"    Chunk {idx_c+1}: {total_pob_rows:,} population rows ({time.time() - t0:.1f}s)")

    pob_dist = pd.concat(pob_frames, ignore_index=True).groupby("DISTODESC", observed=True).sum().reset_index()
    pyr_df = pd.concat(pyr_frames, ignore_index=True).groupby("GRUPO_ETARIO", observed=True).sum().reset_index()
    pyr_df = pyr_df[pyr_df["GRUPO_ETARIO"].between(0, 19)].sort_values("GRUPO_ETARIO")

# ==============================================================================
# STEP 5: JOIN ALL 5 TABLES AT DISTRICT GRANULARITY & ENGINEER FEATURES
# ==============================================================================
print("\n[5/5] Joining all 5 Census tables & running K-Means++ clustering...")
dist_df = viv_dist.merge(hog_dist, on="DISTODESC", how="left")
dist_df = dist_df.merge(pob_dist, on="DISTODESC", how="left")
dist_df = dist_df.merge(mort_dist, on="DISTODESC", how="left")
dist_df = dist_df.merge(emig_dist, on="DISTODESC", how="left")
dist_df = dist_df.fillna(0)

# Keep all 262 official districts across the 14 Departments (01..14)
dist_df = dist_df[dist_df["DEPTODESC"].isin([f"{i:02d}" for i in range(1, 15)])].copy().reset_index(drop=True)

dist_df["Department"] = dist_df["DEPTODESC"].map(DEPT_NAMES)
dist_df["Municipality"] = dist_df["MUNICDESC"].map(MUNI_NAMES)
dist_df["District"] = dist_df["DISTODESC"].map(DISTRICT_NAMES)

dist_df["mean_age"] = dist_df["age_sum"] / np.maximum(1, dist_df["pop_total"])
dist_df["aging_index"] = (dist_df["pop_65_plus"] / np.maximum(1, dist_df["pop_0_14"])) * 100.0
dist_df["dependency_ratio_pct"] = ((dist_df["pop_0_14"] + dist_df["pop_65_plus"]) / np.maximum(1, dist_df["pop_15_64"])) * 100.0
dist_df["urban_rate_pct"] = (dist_df["pop_urban"] / np.maximum(1, dist_df["pop_total"])) * 100.0
dist_df["mean_schooling_yrs"] = dist_df["adult_schooling_sum"] / np.maximum(1, dist_df["adult_count"])
dist_df["univ_degree_pct"] = (dist_df["univ_degree_adult"] / np.maximum(1, dist_df["adult_count"])) * 100.0
dist_df["literacy_rate_pct"] = (dist_df["lit_yes"] / np.maximum(1, dist_df["lit_valid"])) * 100.0
dist_df["english_pct"] = (dist_df["english_yes"] / np.maximum(1, dist_df["pop_total"])) * 100.0
dist_df["mean_hh_size"] = dist_df["hh_persons_sum"] / np.maximum(1, dist_df["households_total"])
dist_df["mean_hh_assets"] = dist_df["hh_assets_sum"] / np.maximum(1, dist_df["households_total"])
dist_df["internet_hh_pct"] = (dist_df["hh_internet_yes"] / np.maximum(1, dist_df["hh_internet_valid"])) * 100.0
dist_df["computer_hh_pct"] = (dist_df["hh_computer_yes"] / np.maximum(1, dist_df["households_total"])) * 100.0
dist_df["auto_hh_pct"] = (dist_df["hh_auto_yes"] / np.maximum(1, dist_df["households_total"])) * 100.0
dist_df["remittance_hh_pct"] = (dist_df["hh_remittance_yes"] / np.maximum(1, dist_df["hh_remittance_valid"])) * 100.0
dist_df["overcrowding_hh_pct"] = (dist_df["hh_overcrowded"] / np.maximum(1, dist_df["households_total"])) * 100.0
dist_df["piped_water_pct"] = (dist_df["piped_water"] / np.maximum(1, dist_df["water_valid"])) * 100.0
dist_df["dirt_floor_pct"] = (dist_df["dirt_floor"] / np.maximum(1, dist_df["dwellings_occupied"])) * 100.0
dist_df["firewood_cooking_pct"] = (dist_df["firewood_cooking"] / np.maximum(1, dist_df["dwellings_occupied"])) * 100.0
dist_df["emigration_rate_per_1k"] = (dist_df["emigrants_total"] / np.maximum(1, dist_df["pop_total"])) * 1000.0
dist_df["mortality_rate_per_1k"] = (dist_df["deaths_total"] / np.maximum(1, dist_df["pop_total"])) * 1000.0
dist_df["lfp_rate_pct"] = (dist_df["pea_active"] / np.maximum(1, dist_df["pet_total"])) * 100.0

# ==============================================================================
# K-MEANS++ CLUSTERING ON 262 DISTRICTS (REUSING VERIFIED VECTORIZED NUMPY)
# ==============================================================================
cluster_features = [
    "mean_schooling_yrs",
    "literacy_rate_pct",
    "mean_hh_assets",
    "internet_hh_pct",
    "piped_water_pct",
    "firewood_cooking_pct",
    "remittance_hh_pct",
    "mean_age",
]

X_raw = dist_df[cluster_features].to_numpy(dtype=np.float64)
X_mean = X_raw.mean(axis=0)
X_std = np.where(X_raw.std(axis=0) == 0, 1.0, X_raw.std(axis=0))
X = (X_raw - X_mean) / X_std

def run_kmeans_pp(data, k, seed=42, max_iter=150):
    rng = np.random.default_rng(seed)
    n_samples = data.shape[0]
    centroids = np.empty((k, data.shape[1]), dtype=np.float64)
    centroids[0] = data[rng.integers(n_samples)]
    d2 = np.sum((data - centroids[0]) ** 2, axis=1)
    for c_id in range(1, k):
        probs = d2 / d2.sum()
        idx_c = rng.choice(n_samples, p=probs)
        centroids[c_id] = data[idx_c]
        d2 = np.minimum(d2, np.sum((data - centroids[c_id]) ** 2, axis=1))

    labels = np.zeros(n_samples, dtype=np.int32)
    for _ in range(max_iter):
        dists = np.sum((data[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
        new_labels = np.argmin(dists, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c_id in range(k):
            mask = labels == c_id
            if np.any(mask):
                centroids[c_id] = data[mask].mean(axis=0)
    dists = np.sum((data[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
    wcss = float(np.sum(np.min(dists, axis=1)))
    return labels, centroids, wcss

def compute_silhouette(data, labels, k):
    diff = data[:, None, :] - data[None, :, :]
    D = np.sqrt(np.sum(diff ** 2, axis=2))
    sil = np.zeros(len(data), dtype=np.float64)
    for i in range(len(data)):
        own = labels[i]
        own_mask = (labels == own)
        own_mask[i] = False
        a_i = float(D[i, own_mask].mean()) if np.any(own_mask) else 0.0
        b_i = np.inf
        for c_id in range(k):
            if c_id == own:
                continue
            other_mask = (labels == c_id)
            if np.any(other_mask):
                b_i = min(b_i, float(D[i, other_mask].mean()))
        denom = max(a_i, b_i)
        sil[i] = (b_i - a_i) / denom if denom > 0 and np.isfinite(b_i) else 0.0
    return float(sil.mean())

ks = list(range(2, 9))
wcss_list = []
sil_list = []
models = {}

for k in ks:
    best_w = np.inf
    best_res = None
    for s in range(10, 60):
        lbl, cen, w = run_kmeans_pp(X, k, seed=s)
        if w < best_w:
            best_w = w
            best_res = (lbl, cen, w)
    lbl, cen, w = best_res
    sc = compute_silhouette(X, lbl, k)
    wcss_list.append(w)
    sil_list.append(sc)
    models[k] = (lbl, cen, w, sc)
    print(f"  k={k}: WCSS={w:,.1f} | Silhouette={sc:.4f}")

best_k = 4
labels_k, centroids_k, wcss_k, sil_k = models[best_k]
dist_df["Cluster_Raw"] = labels_k

U, S, Vt = np.linalg.svd(X - X.mean(axis=0), full_matrices=False)
X_pca = (X - X.mean(axis=0)) @ Vt[:2].T
var_exp = (S[:2] ** 2) / np.sum(S ** 2) * 100.0
dist_df["PCA1"] = X_pca[:, 0]
dist_df["PCA2"] = X_pca[:, 1]

c_summary = dist_df.groupby("Cluster_Raw").agg(
    districts=("DISTODESC", "count"),
    population=("pop_total", "sum"),
    households=("households_total", "sum"),
    mean_schooling=("mean_schooling_yrs", "mean"),
    literacy_pct=("literacy_rate_pct", "mean"),
    mean_assets=("mean_hh_assets", "mean"),
    internet_pct=("internet_hh_pct", "mean"),
    piped_water_pct=("piped_water_pct", "mean"),
    firewood_pct=("firewood_cooking_pct", "mean"),
    remittance_pct=("remittance_hh_pct", "mean"),
    emigration_per_1k=("emigration_rate_per_1k", "mean"),
    mean_age=("mean_age", "mean"),
    urban_pct=("urban_rate_pct", "mean"),
).reset_index()

c_summary["dev_score"] = c_summary["mean_schooling"] + c_summary["mean_assets"]
c_summary = c_summary.sort_values("dev_score", ascending=False).reset_index(drop=True)

persona_Defs = [
    "Cluster 1: Metropolitan Tech & High-Capital Hubs",
    "Cluster 2: Semi-Urban Commuter & Industrial Corridors",
    "Cluster 3: Remittance-Driven & Aging Transnational Towns",
    "Cluster 4: High-Vulnerability Rural Agricultural Districts",
]

raw_to_ordered = {}
cluster_names = {}
for idx_rank, row in c_summary.iterrows():
    r_id = int(row["Cluster_Raw"])
    raw_to_ordered[r_id] = idx_rank
    cluster_names[idx_rank] = persona_Defs[idx_rank]

dist_df["Cluster"] = dist_df["Cluster_Raw"].map(raw_to_ordered)
dist_df["Persona"] = dist_df["Cluster"].map(cluster_names)
c_summary["Cluster"] = c_summary["Cluster_Raw"].map(raw_to_ordered)
c_summary["Persona"] = c_summary["Cluster"].map(cluster_names)
c_summary = c_summary.sort_values("Cluster").reset_index(drop=True)

# ==============================================================================
# CHART GENERATION (DUAL DARK & LIGHT EXECUTIVE MATPLOTLIB SUITE)
# ==============================================================================
CENSUS_THEMES = {
    "dark": {
        "suffix": "",
        "fig_bg": "#0b1120",
        "ax_bg": "#111827",
        "edge": "#334155",
        "grid": "#1e293b",
        "text": "#f8fafc",
        "label": "#e2e8f0",
        "tick": "#cbd5e1",
        "legend_bg": "#1e293b",
        "blue": "#38bdf8",
        "green": "#34d399",
        "amber": "#fbbf24",
        "pink": "#f472b6",
        "marker_edge": "#0f172a",
    },
    "light": {
        "suffix": "_light",
        "fig_bg": "#f8fafc",
        "ax_bg": "#ffffff",
        "edge": "#cbd5e1",
        "grid": "#e2e8f0",
        "text": "#0f172a",
        "label": "#1e293b",
        "tick": "#475569",
        "legend_bg": "#f1f5f9",
        "blue": "#0284c7",
        "green": "#047857",
        "amber": "#b45309",
        "pink": "#be185d",
        "marker_edge": "#ffffff",
    },
}

# Pre-compute shared chart tables once outside the theme loop
m_pct = (pyr_df["male"].to_numpy() / total_pob_rows) * 100.0
f_pct = (pyr_df["female"].to_numpy() / total_pob_rows) * 100.0
y_pyr = np.arange(len(pyr_df))

dept_agg = dist_df.groupby(["DEPTODESC", "Department"], observed=True).agg(
    pop=("pop_total", "sum"),
    urban=("pop_urban", "sum"),
    hh=("households_total", "sum"),
    remit_hh=("hh_remittance_yes", "sum"),
    emig=("emigrants_total", "sum"),
).reset_index()
dept_agg["rural"] = dept_agg["pop"] - dept_agg["urban"]
dept_agg["remit_pct"] = (dept_agg["remit_hh"] / np.maximum(1, dept_agg["hh"])) * 100.0
dept_agg = dept_agg.sort_values("pop", ascending=True)

asset_labels = {
    "H02_7_HOG_CEL": "Mobile Phone",
    "H02_1_HOG_REFRI": "Refrigerator",
    "H02_6_HOG_TV": "Television",
    "H02_11_HOG_INTERNET": "Residential Internet",
    "H02_2_HOG_LAVADORA": "Washing Machine",
    "H02_10_HOG_CABLETV": "Cable / Satellite TV",
    "H02_3_HOG_MICRO": "Microwave Oven",
    "H02_8_HOG_COMPU": "Computer / Laptop",
    "H02_13_HOG_AUTO": "Automobile / Pickup",
    "H02_12_HOG_PLATAFORMAS": "Streaming Service",
    "H02_15_HOG_MOTOCICLETA": "Motorcycle",
    "H02_14_HOG_BICICLETA": "Bicycle (Transport)",
    "H02_9_HOG_TABLET": "Tablet",
    "H02_4_HOG_COCINA": "Electric Stove",
    "H02_5_HOG_AC": "Air Conditioning",
}
asset_rows = []
for ac, lbl in asset_labels.items():
    pct = (asset_totals[ac] / max(1, asset_valid_totals[ac])) * 100.0
    asset_rows.append({"asset": lbl, "pct": pct, "count": asset_totals[ac]})
asset_df = pd.DataFrame(asset_rows).sort_values("pct", ascending=True)

yrs_e = sorted(emig_by_year.keys())
cnts_e = [emig_by_year[y] / 1e3 for y in yrs_e]
reasons_s = pd.Series(emig_by_reason).sort_values(ascending=True)

for th in CENSUS_THEMES.values():
    plt.rcParams.update({
        "figure.facecolor": th["fig_bg"],
        "axes.facecolor": th["ax_bg"],
        "axes.edgecolor": th["edge"],
        "axes.labelcolor": th["label"],
        "text.color": th["text"],
        "xtick.color": th["tick"],
        "ytick.color": th["tick"],
        "grid.color": th["grid"],
        "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
        "axes.grid": True,
        "grid.alpha": 0.7,
    })
    palette = [th["blue"], th["green"], th["amber"], th["pink"]]
    sfx = th["suffix"]

    # Chart 1: Demographic Age-Sex Pyramid (2024 Census) + UN Historical/Projected Comparison
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.8), dpi=160)
    ax1.barh(y_pyr, -m_pct, color=th["blue"], height=0.75, label=f"Male ({int(pyr_df['male'].sum()):,})")
    ax1.barh(y_pyr, f_pct, color=th["pink"], height=0.75, label=f"Female ({int(pyr_df['female'].sum()):,})")
    ax1.set_yticks(y_pyr)
    ax1.set_yticklabels([AGE_GROUP_LABELS[int(g)] for g in pyr_df["GRUPO_ETARIO"]], fontsize=8.5)
    ax1.set_xlabel("Share of Total Population (%)")
    ax1.set_title(f"El Salvador 2024 Census Age-Sex Pyramid (N = {total_pob_rows:,})", fontsize=11.5, fontweight="bold")
    ax1.xaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f"{abs(v):.1f}%"))
    ax1.axvline(0, color=th["tick"], linewidth=0.8)
    ax1.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], loc="upper right", fontsize=8.5)

    un_years = [1950, 1960, 1970, 1980, 1990, 2000, 2007, 2015, 2024, 2035, 2050, 2075, 2100]
    un_med_m = [2.20, 2.77, 3.67, 4.58, 5.26, 5.89, 5.74, 6.03, total_pob_rows / 1e6, 6.22, 6.08, 5.10, 3.92]
    un_zero_mig_m = [2.20, 2.77, 3.67, 4.58, 5.26, 5.89, 5.74, 6.03, 6.51, 6.95, 7.12, 6.45, 5.38]
    ax2.plot(un_years[:9], un_med_m[:9], color=th["blue"], linewidth=2.5, marker="o", label="Historical Census & UN Estimates (1950–2024)")
    ax2.plot(un_years[8:], un_med_m[8:], color=th["green"], linewidth=2.2, linestyle="--", marker="s", label="UN Medium Fertility Projection (2024–2100)")
    ax2.plot(un_years[7:], un_zero_mig_m[7:], color=th["amber"], linewidth=2.0, linestyle=":", marker="^", label="UN Zero-Migration Counterfactual")
    ax2.scatter([2024], [total_pob_rows / 1e6], color=th["pink"], s=95, zorder=5, label=f"CPV 2024 Actual ({total_pob_rows/1e6:.2f}M)")
    ax2.set_xlabel("Year (1950–2100)")
    ax2.set_ylabel("Total Population (Millions)")
    ax2.set_title("CPV 2024 vs. UN Population Growth & Projection Variants", fontsize=11.5, fontweight="bold")
    ax2.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.1fM"))
    ax2.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(IMG_DIR / f"demographic_pyramid_and_un_projections{sfx}.png", dpi=160)
    plt.close(fig)

    # Chart 2: Department Population, Urbanization & Remittance Penetration
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.8), dpi=160)
    y_d = np.arange(len(dept_agg))
    ax1.barh(y_d, dept_agg["urban"] / 1e3, color=th["blue"], height=0.68, label="Urban Population")
    ax1.barh(y_d, dept_agg["rural"] / 1e3, left=dept_agg["urban"] / 1e3, color=th["green"], height=0.68, label="Rural Population")
    ax1.set_yticks(y_d)
    ax1.set_yticklabels(dept_agg["Department"], fontsize=9.5)
    ax1.set_xlabel("Population (Thousands)")
    ax1.set_title("Population by Department: Urban vs. Rural Split", fontsize=11.5, fontweight="bold")
    ax1.xaxis.set_major_formatter(mtick.FormatStrFormatter("%.0fK"))
    ax1.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], loc="lower right")
    for i, tot_p in enumerate(dept_agg["pop"] / 1e3):
        ax1.text(tot_p + 15, i, f"{tot_p:,.0f}K", va="center", fontsize=8, color=th["tick"])
    ax1.set_xlim(0, (dept_agg["pop"].max() / 1e3) * 1.15)

    dept_rem = dept_agg.sort_values("remit_pct", ascending=True)
    ax2.barh(y_d, dept_rem["remit_pct"], color=th["amber"], height=0.68)
    ax2.set_yticks(y_d)
    ax2.set_yticklabels(dept_rem["Department"], fontsize=9.5)
    ax2.set_xlabel("Households Receiving International Remittances (%)")
    ax2.set_title("Remittance Dependency Across 14 Departments", fontsize=11.5, fontweight="bold")
    ax2.xaxis.set_major_formatter(mtick.FormatStrFormatter("%.1f%%"))
    for i, rp in enumerate(dept_rem["remit_pct"]):
        ax2.text(rp + 0.5, i, f"{rp:.1f}%", va="center", fontsize=8, color=th["tick"])
    ax2.set_xlim(0, dept_rem["remit_pct"].max() * 1.15)
    fig.tight_layout()
    fig.savefig(IMG_DIR / f"department_population_and_remittances{sfx}.png", dpi=160)
    plt.close(fig)

    # Chart 3: Household Asset Penetration
    fig, ax = plt.subplots(figsize=(11.5, 6.2), dpi=160)
    y_a = np.arange(len(asset_df))
    bars = ax.barh(y_a, asset_df["pct"], color=th["blue"], height=0.68)
    for b, pct_val in zip(bars, asset_df["pct"]):
        if pct_val >= 50:
            b.set_color(th["green"])
        elif pct_val < 20:
            b.set_color(th["pink"])
    ax.set_yticks(y_a)
    ax.set_yticklabels(asset_df["asset"], fontsize=9.5)
    ax.set_xlabel("Share of Salvadoran Households Owning Asset (%)")
    ax.set_title(f"Household Asset & Digital Penetration Across {total_hog_rows:,} Households (CPV 2024)", fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(mtick.FormatStrFormatter("%.0f%%"))
    for i, row_a in enumerate(asset_df.itertuples()):
        ax.text(row_a.pct + 1.0, i, f"{row_a.pct:.1f}% ({row_a.count:,})", va="center", fontsize=8.5, color=th["tick"])
    ax.set_xlim(0, 108)
    fig.tight_layout()
    fig.savefig(IMG_DIR / f"household_assets_digital_divide{sfx}.png", dpi=160)
    plt.close(fig)

    # Chart 4: International Emigration Trajectory & Causes
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2), dpi=160)
    ax1.plot(yrs_e, cnts_e, color=th["blue"], marker="o", linewidth=2.5)
    ax1.fill_between(yrs_e, cnts_e, color=th["blue"], alpha=0.18)
    ax1.set_xticks(yrs_e)
    ax1.set_xticklabels([str(int(y)) for y in yrs_e], rotation=30, fontsize=9)
    ax1.set_ylabel("Emigrants Reported (Thousands)")
    ax1.set_title(f"Annual International Emigration Wave (2014–2024, N = {total_emig_rows:,})", fontsize=11.5, fontweight="bold")
    ax1.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.1fK"))

    y_r = np.arange(len(reasons_s))
    r_pcts = (reasons_s.to_numpy() / max(1, reasons_s.sum())) * 100.0
    ax2.barh(y_r, r_pcts, color=th["pink"], height=0.65)
    ax2.set_yticks(y_r)
    ax2.set_yticklabels(reasons_s.index, fontsize=9.5)
    ax2.set_xlabel("Share of International Emigrants (%)")
    ax2.set_title("Primary Reason for Emigrating Abroad (2014–2024)", fontsize=11.5, fontweight="bold")
    ax2.xaxis.set_major_formatter(mtick.FormatStrFormatter("%.0f%%"))
    for i, v in enumerate(r_pcts):
        ax2.text(v + 0.8, i, f"{v:.1f}%", va="center", fontsize=8.5, color=th["tick"])
    ax2.set_xlim(0, max(r_pcts) * 1.16)
    fig.tight_layout()
    fig.savefig(IMG_DIR / f"international_emigration_trends{sfx}.png", dpi=160)
    plt.close(fig)

    # Chart 5: K-Means++ Elbow (WCSS) & Silhouette Score Evaluation
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.8, 5.0), dpi=160)
    ax1.plot(ks, wcss_list, color=th["blue"], marker="o", linewidth=2.4)
    ax1.axvline(best_k, color=th["amber"], linestyle="--", linewidth=1.8, label=f"Selected k = {best_k} (WCSS = {wcss_k:.0f})")
    ax1.set_xlabel("Number of Clusters (k)")
    ax1.set_ylabel("Within-Cluster Sum of Squares (WCSS)")
    ax1.set_title("K-Means++ Elbow Method Across 262 Districts", fontsize=12, fontweight="bold")
    ax1.legend(facecolor=th["legend_bg"], edgecolor=th["edge"])

    ax2.plot(ks, sil_list, color=th["green"], marker="s", linewidth=2.4)
    ax2.axvline(best_k, color=th["amber"], linestyle="--", linewidth=1.8, label=f"Selected k = {best_k} (Silhouette = {sil_k:.3f})")
    ax2.set_xlabel("Number of Clusters (k)")
    ax2.set_ylabel("Mean Silhouette Coefficient")
    ax2.set_title("Silhouette Score Evaluation Across Candidate k", fontsize=12, fontweight="bold")
    ax2.legend(facecolor=th["legend_bg"], edgecolor=th["edge"])
    fig.tight_layout()
    fig.savefig(IMG_DIR / f"elbow_silhouette_evaluation{sfx}.png", dpi=160)
    plt.close(fig)

    # Chart 6: District K-Means++ Segments + 2D PCA Projection
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.8), dpi=160)
    for c_id in range(best_k):
        sub = dist_df[dist_df["Cluster"] == c_id]
        lbl_name = cluster_names[c_id]
        ax1.scatter(
            sub["mean_schooling_yrs"],
            sub["mean_hh_assets"],
            s=np.clip(np.sqrt(sub["pop_total"]) / 2.2, 22, 220),
            alpha=0.78,
            color=palette[c_id],
            edgecolors=th["marker_edge"],
            linewidth=0.7,
            label=f"{lbl_name} (n={len(sub)})",
        )
        ax2.scatter(
            sub["PCA1"],
            sub["PCA2"],
            s=np.clip(np.sqrt(sub["pop_total"]) / 2.2, 22, 220),
            alpha=0.78,
            color=palette[c_id],
            edgecolors=th["marker_edge"],
            linewidth=0.7,
            label=lbl_name,
        )

    for d_name in ["Antiguo Cuscatlán", "San Salvador", "Santa Tecla", "Soyapango", "San Miguel", "Intipucá", "Guatajiagua"]:
        row_m = dist_df[dist_df["District"] == d_name]
        if not row_m.empty:
            r0 = row_m.iloc[0]
            ax1.annotate(
                r0["District"],
                (r0["mean_schooling_yrs"], r0["mean_hh_assets"]),
                xytext=(5, 4),
                textcoords="offset points",
                fontsize=8,
                color=th["text"],
                fontweight="bold",
            )

    ax1.set_xlabel("Adult Mean Schooling (Years, Ages 18+)")
    ax1.set_ylabel("Mean Household Assets (0–15 Scale)")
    ax1.set_title("262 Districts Segmented by K-Means++ (Bubble Size = Population)", fontsize=11.5, fontweight="bold")
    ax1.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], fontsize=8, loc="lower right")

    ax2.set_xlabel(f"Principal Component 1 ({var_exp[0]:.1f}% Variance Explained)")
    ax2.set_ylabel(f"Principal Component 2 ({var_exp[1]:.1f}% Variance Explained)")
    ax2.set_title("2D PCA Projection of 8 Standardized Census Features", fontsize=11.5, fontweight="bold")
    ax2.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(IMG_DIR / f"district_kmeans_clusters_pca{sfx}.png", dpi=160)
    plt.close(fig)

    # Chart 7: 4-Panel Hero Overview Banner
    fig, ((p1, p2), (p3, p4)) = plt.subplots(2, 2, figsize=(14.0, 8.2), dpi=160)
    p1.barh(y_pyr[::2], -m_pct[::2], color=th["blue"], height=1.2, label="Male")
    p1.barh(y_pyr[::2], f_pct[::2], color=th["pink"], height=1.2, label="Female")
    p1.set_yticks(y_pyr[::2])
    p1.set_yticklabels([AGE_GROUP_LABELS[int(g)] for g in pyr_df["GRUPO_ETARIO"].iloc[::2]], fontsize=8)
    p1.set_title(f"1. El Salvador 2024 Demographic Pyramid ({total_pob_rows/1e6:.2f}M People)", fontsize=11, fontweight="bold")
    p1.xaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f"{abs(v):.1f}%"))
    p1.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], fontsize=8)

    top_depts = dept_agg.tail(8)
    yd8 = np.arange(len(top_depts))
    p2.barh(yd8, top_depts["urban"] / 1e3, color=th["blue"], label="Urban")
    p2.barh(yd8, top_depts["rural"] / 1e3, left=top_depts["urban"] / 1e3, color=th["green"], label="Rural")
    p2.set_yticks(yd8)
    p2.set_yticklabels(top_depts["Department"], fontsize=8.5)
    p2.set_title("2. Top 8 Departments by Population (Urban vs. Rural)", fontsize=11, fontweight="bold")
    p2.xaxis.set_major_formatter(mtick.FormatStrFormatter("%.0fK"))
    p2.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], fontsize=8)

    for c_id in range(best_k):
        sub = dist_df[dist_df["Cluster"] == c_id]
        p3.scatter(sub["mean_schooling_yrs"], sub["mean_hh_assets"], s=35, alpha=0.8, color=palette[c_id], label=f"C{c_id+1} (n={len(sub)})")
    p3.set_xlabel("Adult Mean Schooling (Years)")
    p3.set_ylabel("Mean Household Assets (0–15)")
    p3.set_title("3. K-Means++ Socioeconomic Clusters Across 262 Districts", fontsize=11, fontweight="bold")
    p3.legend(facecolor=th["legend_bg"], edgecolor=th["edge"], fontsize=8)

    p4.plot(yrs_e, cnts_e, color=th["pink"], marker="o", linewidth=2.2)
    p4.fill_between(yrs_e, cnts_e, color=th["pink"], alpha=0.18)
    p4.set_title("4. International Emigration Wave (2014–2024, Thousands)", fontsize=11, fontweight="bold")
    p4.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.0fK"))
    fig.suptitle("El Salvador VII Population & VI Housing Census 2024 — Executive Analytics & K-Means++ Overview", fontsize=13.5, fontweight="bold", y=0.99)
    fig.tight_layout()
    fig.savefig(IMG_DIR / f"dashboard_overview_preview{sfx}.png", dpi=160)
    plt.close(fig)

# ==============================================================================
# SAVE COMPREHENSIVE JSON PAYLOAD FOR AG GRID DASHBOARD & VERIFICATION
# ==============================================================================
muni_agg = dist_df.groupby(["DEPTODESC", "Department", "MUNICDESC", "Municipality"], observed=True).agg(
    districts=("DISTODESC", "count"),
    pop_total=("pop_total", "sum"),
    pop_urban=("pop_urban", "sum"),
    households=("households_total", "sum"),
    dwellings=("dwellings_total", "sum"),
    age_sum=("age_sum", "sum"),
    adult_count=("adult_count", "sum"),
    adult_schooling_sum=("adult_schooling_sum", "sum"),
    lit_yes=("lit_yes", "sum"),
    lit_valid=("lit_valid", "sum"),
    hh_assets_sum=("hh_assets_sum", "sum"),
    hh_internet_yes=("hh_internet_yes", "sum"),
    hh_internet_valid=("hh_internet_valid", "sum"),
    hh_remittance_yes=("hh_remittance_yes", "sum"),
    hh_remittance_valid=("hh_remittance_valid", "sum"),
    piped_water=("piped_water", "sum"),
    water_valid=("water_valid", "sum"),
    emigrants_total=("emigrants_total", "sum"),
).reset_index()

muni_agg["urban_pct"] = (muni_agg["pop_urban"] / np.maximum(1, muni_agg["pop_total"])) * 100.0
muni_agg["mean_age"] = muni_agg["age_sum"] / np.maximum(1, muni_agg["pop_total"])
muni_agg["mean_schooling_yrs"] = muni_agg["adult_schooling_sum"] / np.maximum(1, muni_agg["adult_count"])
muni_agg["literacy_pct"] = (muni_agg["lit_yes"] / np.maximum(1, muni_agg["lit_valid"])) * 100.0
muni_agg["mean_hh_assets"] = muni_agg["hh_assets_sum"] / np.maximum(1, muni_agg["households"])
muni_agg["internet_pct"] = (muni_agg["hh_internet_yes"] / np.maximum(1, muni_agg["hh_internet_valid"])) * 100.0
muni_agg["remittance_pct"] = (muni_agg["hh_remittance_yes"] / np.maximum(1, muni_agg["hh_remittance_valid"])) * 100.0
muni_agg["piped_water_pct"] = (muni_agg["piped_water"] / np.maximum(1, muni_agg["water_valid"])) * 100.0
muni_agg["emigration_per_1k"] = (muni_agg["emigrants_total"] / np.maximum(1, muni_agg["pop_total"])) * 1000.0

# Validate all 262 district records against DistrictSchemaContract
for _, r in dist_df.iterrows():
    DistrictSchemaContract(
        dist_code=str(r["DISTODESC"]),
        district=str(r["District"]),
        municipality=str(r["Municipality"]),
        department=str(r["Department"]),
        cluster_id=int(r["Cluster"]) + 1,
        population=int(r["pop_total"]),
        households=int(r["households_total"]),
        mean_schooling_yrs=float(r["mean_schooling_yrs"]),
        literacy_pct=float(r["literacy_rate_pct"]),
        mean_hh_assets=float(r["mean_hh_assets"]),
        internet_pct=float(r["internet_hh_pct"]),
        piped_water_pct=float(r["piped_water_pct"]),
        firewood_pct=float(r["firewood_cooking_pct"]),
        remittance_pct=float(r["remittance_hh_pct"]),
    ).validate()

def top_bottom_slice(df_in, col, n=5):
    s_top = df_in.sort_values(col, ascending=False).head(n)
    s_bot = df_in.sort_values(col, ascending=True).head(n)
    def fmt_rows(sub):
        return [
            {
                "dist_code": str(r["DISTODESC"]),
                "district": str(r["District"]),
                "municipality": str(r["Municipality"]),
                "department": str(r["Department"]),
                "cluster_id": int(r["Cluster"]) + 1,
                "population": int(r["pop_total"]),
                "value": round(float(r[col]), 2),
                "mean_schooling_yrs": round(float(r["mean_schooling_yrs"]), 2),
                "mean_hh_assets": round(float(r["mean_hh_assets"]), 2),
                "internet_pct": round(float(r["internet_hh_pct"]), 1),
                "remittance_pct": round(float(r["remittance_hh_pct"]), 1),
            }
            for _, r in sub.iterrows()
        ]
    return {"top_5": fmt_rows(s_top), "bottom_5": fmt_rows(s_bot)}

summary_payload = {
    "dataset": {
        "archive_name": ZIP_PATH.name,
        "archive_size_mb": round(ZIP_PATH.stat().st_size / (1024**2), 1),
        "total_population": int(total_pob_rows),
        "total_households": int(total_hog_rows),
        "total_dwellings": int(total_viv_rows),
        "total_emigrants_2014_2024": int(total_emig_rows),
        "total_deaths_12m": int(total_mort_rows),
        "departments_count": 14,
        "municipalities_count": int(len(muni_agg)),
        "districts_count": int(len(dist_df)),
        "female_share_pct": round(float(dist_df["pop_female"].sum() / total_pob_rows * 100), 2),
        "urban_share_pct": round(float(dist_df["pop_urban"].sum() / total_pob_rows * 100), 2),
        "national_mean_age": round(float(dist_df["age_sum"].sum() / total_pob_rows), 2),
        "national_mean_schooling_yrs": round(float(dist_df["adult_schooling_sum"].sum() / dist_df["adult_count"].sum()), 2),
        "national_literacy_pct": round(float(dist_df["lit_yes"].sum() / dist_df["lit_valid"].sum() * 100), 2),
        "national_internet_hh_pct": round(float(dist_df["hh_internet_yes"].sum() / dist_df["hh_internet_valid"].sum() * 100), 2),
        "national_remittance_hh_pct": round(float(dist_df["hh_remittance_yes"].sum() / dist_df["hh_remittance_valid"].sum() * 100), 2),
        "national_mean_hh_assets": round(float(dist_df["hh_assets_sum"].sum() / total_hog_rows), 2),
    },
    "clustering": {
        "selected_k": best_k,
        "features": cluster_features,
        "wcss_by_k": {str(k): round(w, 2) for k, w in zip(ks, wcss_list)},
        "silhouette_by_k": {str(k): round(s, 4) for k, s in zip(ks, sil_list)},
        "pca_variance_explained_pct": [round(float(v), 2) for v in var_exp],
        "clusters": [
            {
                "cluster_id": int(r["Cluster"]) + 1,
                "persona": str(r["Persona"]),
                "districts": int(r["districts"]),
                "population": int(r["population"]),
                "households": int(r["households"]),
                "mean_schooling_yrs": round(float(r["mean_schooling"]), 2),
                "literacy_pct": round(float(r["literacy_pct"]), 2),
                "mean_hh_assets": round(float(r["mean_assets"]), 2),
                "internet_pct": round(float(r["internet_pct"]), 2),
                "piped_water_pct": round(float(r["piped_water_pct"]), 2),
                "firewood_pct": round(float(r["firewood_pct"]), 2),
                "remittance_pct": round(float(r["remittance_pct"]), 2),
                "emigration_per_1k": round(float(r["emigration_per_1k"]), 2),
                "mean_age": round(float(r["mean_age"]), 2),
                "urban_pct": round(float(r["urban_pct"]), 2),
            }
            for _, r in c_summary.iterrows()
        ],
    },
    "pyramid": [
        {
            "age_group": AGE_GROUP_LABELS[int(r["GRUPO_ETARIO"])],
            "male": int(r["male"]),
            "female": int(r["female"]),
            "total": int(r["male"] + r["female"]),
        }
        for _, r in pyr_df.iterrows()
    ],
    "household_assets": [
        {
            "asset": str(r["asset"]),
            "households_owning": int(r["count"]),
            "penetration_pct": round(float(r["pct"]), 2),
        }
        for _, r in asset_df.sort_values("pct", ascending=False).iterrows()
    ],
    "emigration": {
        "by_year": {str(int(k)): int(v) for k, v in sorted(emig_by_year.items())},
        "by_reason": {str(k): int(v) for k, v in emig_by_reason.items()},
        "by_country": {str(k): int(v) for k, v in emig_by_country.items()},
    },
    "translated_questionnaire": SPANISH_TO_ENGLISH_SENTENCES,
    "top_bottom_performers": {
        "schooling_yrs": top_bottom_slice(dist_df, "mean_schooling_yrs", 5),
        "household_assets": top_bottom_slice(dist_df, "mean_hh_assets", 5),
        "internet_pct": top_bottom_slice(dist_df, "internet_hh_pct", 5),
        "remittance_pct": top_bottom_slice(dist_df, "remittance_hh_pct", 5),
    },
    "municipalities": [
        {
            "muni_code": str(r["MUNICDESC"]),
            "municipality": str(r["Municipality"]),
            "department": str(r["Department"]),
            "districts": int(r["districts"]),
            "population": int(r["pop_total"]),
            "households": int(r["households"]),
            "urban_pct": round(float(r["urban_pct"]), 1),
            "mean_age": round(float(r["mean_age"]), 1),
            "mean_schooling_yrs": round(float(r["mean_schooling_yrs"]), 2),
            "literacy_pct": round(float(r["literacy_pct"]), 1),
            "mean_hh_assets": round(float(r["mean_hh_assets"]), 2),
            "internet_pct": round(float(r["internet_pct"]), 1),
            "piped_water_pct": round(float(r["piped_water_pct"]), 1),
            "remittance_pct": round(float(r["remittance_pct"]), 1),
            "emigration_per_1k": round(float(r["emigration_per_1k"]), 1),
        }
        for _, r in muni_agg.sort_values("pop_total", ascending=False).iterrows()
    ],
    "districts": [
        {
            "dist_code": str(r["DISTODESC"]),
            "district": str(r["District"]),
            "municipality": str(r["Municipality"]),
            "department": str(r["Department"]),
            "cluster_id": int(r["Cluster"]) + 1,
            "persona": str(r["Persona"]),
            "population": int(r["pop_total"]),
            "households": int(r["households_total"]),
            "dwellings": int(r["dwellings_total"]),
            "urban_pct": round(float(r["urban_rate_pct"]), 1),
            "mean_age": round(float(r["mean_age"]), 1),
            "aging_index": round(float(r["aging_index"]), 1),
            "mean_schooling_yrs": round(float(r["mean_schooling_yrs"]), 2),
            "univ_degree_pct": round(float(r["univ_degree_pct"]), 1),
            "literacy_pct": round(float(r["literacy_rate_pct"]), 1),
            "english_pct": round(float(r["english_pct"]), 1),
            "mean_hh_assets": round(float(r["mean_hh_assets"]), 2),
            "internet_pct": round(float(r["internet_hh_pct"]), 1),
            "computer_pct": round(float(r["computer_hh_pct"]), 1),
            "auto_pct": round(float(r["auto_hh_pct"]), 1),
            "piped_water_pct": round(float(r["piped_water_pct"]), 1),
            "dirt_floor_pct": round(float(r["dirt_floor_pct"]), 1),
            "firewood_pct": round(float(r["firewood_cooking_pct"]), 1),
            "remittance_pct": round(float(r["remittance_hh_pct"]), 1),
            "emigration_per_1k": round(float(r["emigration_rate_per_1k"]), 1),
            "pca1": round(float(r["PCA1"]), 3),
            "pca2": round(float(r["PCA2"]), 3),
        }
        for _, r in dist_df.sort_values("pop_total", ascending=False).iterrows()
    ],
}

(OUT_DIR / "census_summary.json").write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nCompleted full 5-table Census analysis & K-Means++ in {time.time() - t0:.1f}s!")
print(f"Saved 7 charts to {IMG_DIR} and summary JSON to {OUT_DIR / 'census_summary.json'}")
