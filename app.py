import re
import unicodedata
from collections import defaultdict

import pandas as pd
import pdfplumber
import streamlit as st

# =====================================================
# CONFIGURATION
# =====================================================

st.set_page_config(page_title="Simulateur bac", page_icon="🎓", layout="wide")

SEUILS = {
    "Bac sans mention": 10,
    "Mention assez bien": 12,
    "Mention bien": 14,
    "Mention très bien": 16,
    "Félicitations du jury": 18,
}

SPECIALITES_POSSIBLES = [
    "Mathématiques",
    "Physique-chimie",
    "Sciences de l'ingénieur",
    "NSI",
    "SVT",
    "SES",
    "HGGSP",
    "HLP",
    "LLCE",
    "AMC",
    "Arts",
    "Autre spécialité",
]

# Matières lues dans les bulletins Pronote.
# L'ordre est important : on évite de lire les lignes de catégorie du type LV ou ENS COMMUNS.
MATIERES = {
    "Français": ["FRANCAIS"],
    "Philosophie": ["PHILOSOPHIE"],
    "Histoire-géographie": ["HISTOIRE-GEOGRAPHIE", "HISTOIRE GEOGRAPHIE"],
    "EMC": ["ENS. MORAL & CIVIQUE", "ENS MORAL & CIVIQUE", "EMC"],
    "Anglais LV1": ["ANGLAIS LV1", "ANGLAIS LVA", "ANGLAIS"],
    "Italien LV2": ["ITALIEN LV2", "ITALIEN LVB", "ITALIEN"],
    "Espagnol LV2": ["ESPAGNOL LV2", "ESPAGNOL LVB", "ESPAGNOL"],
    "Allemand LV2": ["ALLEMAND LV2", "ALLEMAND LVB", "ALLEMAND"],
    "Enseignement scientifique": ["ENSEIGN.SCIENTIFIQUE", "ENSEIGNEMENT SCIENTIFIQUE", "ENS SCIENTIFIQUE"],
    "EPS": ["ED.PHYSIQUE & SPORT.", "ED PHYSIQUE & SPORT", "EDUCATION PHYSIQUE", "EPS"],
    "Mathématiques": ["MATHEMATIQUES"],
    "Physique-chimie": ["PHYSIQUE-CHIMIE", "PHYSIQUE CHIMIE"],
    "Sciences de l'ingénieur": ["SCIENCES INGENIEUR", "SCIENCES DE L'INGENIEUR"],
    "SI + physique": ["SC.INGEN. & SC.PHYS.", "SC INGEN & SC PHYS"],
    "NSI": ["NUMERIQUE ET SCIENCES INFORMATIQUES", "NSI"],
    "SVT": ["SCIENCES VIE TERRE", "SCIENCES DE LA VIE ET DE LA TERRE", "SVT"],
    "SES": ["SCIENCES ECONOMIQUES ET SOCIALES", "SES"],
    "HGGSP": ["HGGSP", "HISTOIRE-GEOGRAPHIE GEOPOLITIQUE"],
    "HLP": ["HLP", "HUMANITES LITTERATURE PHILOSOPHIE"],
    "LLCE": ["LLCE", "LANGUES LITTERATURES CULTURES ETRANGERES"],
    "AMC": ["AMC", "ANGLAIS MONDE CONTEMPORAIN"],
    "Maths expertes": ["MATHS EXPERTES", "MATHEMATIQUES EXPERTES"],
}

SPECIALITES_DETECTEES = [
    "Mathématiques",
    "Physique-chimie",
    "Sciences de l'ingénieur",
    "SI + physique",
    "NSI",
    "SVT",
    "SES",
    "HGGSP",
    "HLP",
    "LLCE",
    "AMC",
    "Arts",
]

# =====================================================
# OUTILS DE LECTURE
# =====================================================


def normaliser(texte: str) -> str:
    texte = str(texte).upper()
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    texte = texte.replace("Œ", "OE").replace("’", "'")
    texte = re.sub(r"\s+", " ", texte)
    return texte.strip()


def convertir_note(texte: str) -> float:
    return float(texte.replace(",", "."))


def texte_pdf(fichier) -> str:
    texte = ""
    with pdfplumber.open(fichier) as pdf:
        for page in pdf.pages:
            texte += "\n" + (page.extract_text(x_tolerance=1, y_tolerance=3) or "")
    return texte


def detecter_trimestre_depuis_texte(texte: str):
    t = normaliser(texte)
    if "1ER TRIMESTRE" in t or "1ER TRIM" in t:
        return 1
    if "2EME TRIMESTRE" in t or "2E TRIMESTRE" in t or "2EME TRIM" in t:
        return 2
    if "3EME TRIMESTRE" in t or "3E TRIMESTRE" in t or "3EME TRIM" in t:
        return 3
    return None


def ligne_contient_matiere(ligne_norm: str, aliases):
    for alias in aliases:
        if normaliser(alias) in ligne_norm:
            return True
    return False


def premiere_note_apres_nb_notes_ou_coef(ligne: str):
    """
    Format Pronote utilisé ici :
    - Avec nombre de notes : MATIERE 3h00 3,00 4/4 17,20 16,83 ...
      => on prend le premier nombre décimal après 4/4.
    - Sans nombre de notes, exemple EMC : MATIERE 0h30 1,00 19,38 ...
      => on prend le premier nombre décimal après volume horaire + coefficient.

    Pour le T3 de Première, cette valeur correspond à la colonne An.
    Pour les trimestres de Terminale, cette valeur correspond à la moyenne du trimestre.
    """
    ligne_norm = normaliser(ligne)

    fractions = list(re.finditer(r"\d+\s*/\s*\d+", ligne_norm))

    if fractions:
        depart = fractions[-1].end()
    else:
        match_vol_coef = re.search(r"\d+H\d{2}\s+\d{1,2},\d{2}", ligne_norm)
        depart = match_vol_coef.end() if match_vol_coef else 0

    segment = ligne_norm[depart:]
    nombres = re.findall(r"(?<!\d)(\d{1,2},\d{2})(?!\d)", segment)

    for nombre in nombres:
        valeur = convertir_note(nombre)
        if 0 <= valeur <= 20:
            return valeur

    return None


def parser_bulletin_lignes(fichiers, mode: str) -> pd.DataFrame:
    """
    mode = 'premiere_t3' : on prend uniquement le T3 de Première, colonne An.
    mode = 'terminale' : on prend les trois bulletins de Terminale, un par trimestre.
    """
    lignes_resultat = []

    for fichier in fichiers:
        texte = texte_pdf(fichier)
        trimestre = detecter_trimestre_depuis_texte(texte)

        if mode == "premiere_t3" and trimestre != 3:
            continue

        matieres_vues = set()

        for ligne in texte.splitlines():
            ligne_norm = normaliser(ligne)

            for matiere, aliases in MATIERES.items():
                if matiere in matieres_vues:
                    continue

                if ligne_contient_matiere(ligne_norm, aliases):
                    note = premiere_note_apres_nb_notes_ou_coef(ligne)
                    if note is not None:
                        lignes_resultat.append({
                            "Fichier": fichier.name,
                            "Trimestre": trimestre,
                            "Matière": matiere,
                            "Note détectée": round(note, 2),
                            "Ligne lue": ligne,
                        })
                        matieres_vues.add(matiere)
                    break

    return pd.DataFrame(lignes_resultat)


def table_premiere_t3(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Matière", "Note annuelle"])

    lignes = []
    for matiere in sorted(df["Matière"].unique()):
        sous = df[df["Matière"] == matiere]
        note = float(sous["Note détectée"].iloc[0])
        lignes.append({"Matière": matiere, "Note annuelle": round(note, 2)})

    return pd.DataFrame(lignes)


def table_terminale(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Matière", "T1", "T2", "T3", "Note retenue"])

    lignes = []
    for matiere in sorted(df["Matière"].unique()):
        sous = df[df["Matière"] == matiere]

        def note_trim(t):
            ligne = sous[sous["Trimestre"] == t]
            if ligne.empty:
                return None
            return float(ligne["Note détectée"].iloc[0])

        t1 = note_trim(1)
        t2 = note_trim(2)
        t3 = note_trim(3)
        notes = [n for n in [t1, t2, t3] if n is not None]
        moyenne = sum(notes) / len(notes) if notes else 0.0

        lignes.append({
            "Matière": matiere,
            "T1": None if t1 is None else round(t1, 2),
            "T2": None if t2 is None else round(t2, 2),
            "T3": None if t3 is None else round(t3, 2),
            "Note retenue": round(moyenne, 2),
        })

    return pd.DataFrame(lignes)


def note_depuis_table(table: pd.DataFrame, matiere: str, colonne_note: str) -> float:
    if table is None or table.empty:
        return 0.0
    ligne = table[table["Matière"] == matiere]
    if ligne.empty:
        return 0.0
    valeur = ligne[colonne_note].iloc[0]
    if pd.isna(valeur):
        return 0.0
    return float(valeur)


def trouver_matiere(table: pd.DataFrame, mots_cles, colonne_note: str) -> float:
    if table is None or table.empty:
        return 0.0

    for mot in mots_cles:
        mot_norm = normaliser(mot)
        for _, row in table.iterrows():
            if mot_norm in normaliser(row["Matière"]):
                valeur = row[colonne_note]
                if pd.isna(valeur):
                    return 0.0
                return float(valeur)

    return 0.0


def matieres_presentes(table: pd.DataFrame, liste):
    if table is None or table.empty:
        return []
    presentes = []
    for matiere in liste:
        if matiere in table["Matière"].tolist():
            presentes.append(matiere)
    return presentes


def ajouter_specialite(epreuves: dict, nom: str):
    if nom == "Sciences de l'ingénieur":
        epreuves["SI écrit"] = 8
        epreuves["Physique liée à la SI"] = 4
        epreuves["TP / pratique SI"] = 4
    else:
        epreuves[nom] = 16


# =====================================================
# SESSION
# =====================================================

if "etape" not in st.session_state:
    st.session_state.etape = 1
if "premiere" not in st.session_state:
    st.session_state.premiere = None
if "terminale" not in st.session_state:
    st.session_state.terminale = None

# =====================================================
# INTERFACE GÉNÉRALE
# =====================================================

st.title("Simulateur de mention au bac")
st.caption("Lecture des bulletins Pronote PDF + simulation des épreuves finales")

col_nav1, col_nav2, col_nav3 = st.columns(3)
with col_nav1:
    if st.button("1. Première", use_container_width=True):
        st.session_state.etape = 1
        st.rerun()
with col_nav2:
    if st.button("2. Terminale", use_container_width=True):
        st.session_state.etape = 2
        st.rerun()
with col_nav3:
    if st.button("3. Simulateur final", use_container_width=True):
        st.session_state.etape = 3
        st.rerun()

st.divider()

# =====================================================
# ÉTAPE 1 : PREMIÈRE
# =====================================================

if st.session_state.etape == 1:
    st.header("Étape 1 — Bulletin du 3e trimestre de Première")

    st.info(
        "Pour la Première, envoie seulement le bulletin du 3e trimestre. "
        "Dans ce format Pronote, la colonne An. donne directement les moyennes annuelles."
    )

    fichiers = st.file_uploader(
        "Dépose le PDF du T3 de Première",
        type=["pdf"],
        accept_multiple_files=True,
        key="upload_premiere_t3"
    )

    if fichiers:
        df_brut = parser_bulletin_lignes(fichiers, mode="premiere_t3")

        if df_brut.empty:
            st.error("Aucune note détectée. Vérifie que tu as bien envoyé le bulletin du 3e trimestre de Première.")
            st.stop()

        with st.expander("Voir les lignes brutes lues dans le PDF"):
            st.dataframe(df_brut, use_container_width=True)

        table = table_premiere_t3(df_brut)

        st.subheader("Vérification des moyennes annuelles de Première")
        st.write("Corrige une note uniquement si elle est mal détectée.")

        table_modifiee = st.data_editor(
            table,
            use_container_width=True,
            disabled=["Matière"],
            column_config={
                "Note annuelle": st.column_config.NumberColumn(
                    "Note annuelle",
                    min_value=0.0,
                    max_value=20.0,
                    step=0.01,
                    format="%.2f",
                )
            },
            key="editor_premiere_t3"
        )

        if st.button("Valider la Première", type="primary"):
            st.session_state.premiere = table_modifiee
            st.session_state.etape = 2
            st.rerun()

    elif st.session_state.premiere is not None:
        st.success("Première déjà validée.")
        st.dataframe(st.session_state.premiere, use_container_width=True)

# =====================================================
# ÉTAPE 2 : TERMINALE
# =====================================================

elif st.session_state.etape == 2:
    st.header("Étape 2 — Bulletins de Terminale")

    if st.session_state.premiere is None:
        st.warning("Valide d'abord la Première.")
        st.stop()

    st.info("Envoie les 3 bulletins de Terminale. Le programme prend la première moyenne élève de chaque ligne matière.")

    fichiers = st.file_uploader(
        "Dépose les 3 PDF de Terminale",
        type=["pdf"],
        accept_multiple_files=True,
        key="upload_terminale"
    )

    if fichiers:
        df_brut = parser_bulletin_lignes(fichiers, mode="terminale")

        if df_brut.empty:
            st.error("Aucune note détectée pour la Terminale.")
            st.stop()

        with st.expander("Voir les lignes brutes lues dans les PDF"):
            st.dataframe(df_brut, use_container_width=True)

        table = table_terminale(df_brut)

        st.subheader("Vérification des moyennes de Terminale")
        st.write("La note retenue est la moyenne de T1, T2 et T3. Corrige si nécessaire.")

        table_modifiee = st.data_editor(
            table,
            use_container_width=True,
            disabled=["Matière"],
            column_config={
                "T1": st.column_config.NumberColumn("T1", min_value=0.0, max_value=20.0, step=0.01, format="%.2f"),
                "T2": st.column_config.NumberColumn("T2", min_value=0.0, max_value=20.0, step=0.01, format="%.2f"),
                "T3": st.column_config.NumberColumn("T3", min_value=0.0, max_value=20.0, step=0.01, format="%.2f"),
                "Note retenue": st.column_config.NumberColumn("Note retenue", min_value=0.0, max_value=20.0, step=0.01, format="%.2f"),
            },
            key="editor_terminale"
        )

        # Si l'utilisateur modifie T1/T2/T3, on ne recalcule pas automatiquement Note retenue : il peut l'écrire lui-même.
        # Cela évite les modifications invisibles dans Streamlit.

        if st.button("Valider la Terminale", type="primary"):
            st.session_state.terminale = table_modifiee
            st.session_state.etape = 3
            st.rerun()

    elif st.session_state.terminale is not None:
        st.success("Terminale déjà validée.")
        st.dataframe(st.session_state.terminale, use_container_width=True)

# =====================================================
# ÉTAPE 3 : SIMULATEUR FINAL
# =====================================================

elif st.session_state.etape == 3:
    st.header("Étape 3 — Simulateur final")

    if st.session_state.premiere is None:
        st.warning("Valide d'abord la Première.")
        st.stop()
    if st.session_state.terminale is None:
        st.warning("Valide d'abord la Terminale.")
        st.stop()

    premiere = st.session_state.premiere
    terminale = st.session_state.terminale

    st.subheader("1. Spécialités finales")

    spe1 = st.selectbox(
        "Spécialité finale 1",
        SPECIALITES_POSSIBLES,
        index=SPECIALITES_POSSIBLES.index("Mathématiques")
    )
    spe2_options = [s for s in SPECIALITES_POSSIBLES if s != spe1]
    spe2 = st.selectbox(
        "Spécialité finale 2",
        spe2_options,
        index=spe2_options.index("Sciences de l'ingénieur") if "Sciences de l'ingénieur" in spe2_options else 0
    )

    st.subheader("2. Contrôle continu")

    # Spécialité abandonnée : par défaut, on propose une spécialité détectée en Première mais non gardée en Terminale.
    spe_detectees_premiere = matieres_presentes(premiere, SPECIALITES_DETECTEES)
    spe_finales_normalisees = {spe1, spe2}
    candidates_abandon = [s for s in spe_detectees_premiere if s not in spe_finales_normalisees]

    if not candidates_abandon:
        candidates_abandon = spe_detectees_premiere if spe_detectees_premiere else ["Physique-chimie"]

    spe_abandonnee = st.selectbox(
        "Spécialité abandonnée en fin de Première",
        candidates_abandon,
        index=0
    )

    lignes_cc = [
        {"Partie": "Histoire-géographie Première", "Note": trouver_matiere(premiere, ["HISTOIRE"], "Note annuelle"), "Coefficient": 3},
        {"Partie": "Histoire-géographie Terminale", "Note": trouver_matiere(terminale, ["HISTOIRE"], "Note retenue"), "Coefficient": 3},
        {"Partie": "LVA Première", "Note": trouver_matiere(premiere, ["ANGLAIS"], "Note annuelle"), "Coefficient": 3},
        {"Partie": "LVA Terminale", "Note": trouver_matiere(terminale, ["ANGLAIS"], "Note retenue"), "Coefficient": 3},
        {"Partie": "LVB Première", "Note": trouver_matiere(premiere, ["ITALIEN", "ESPAGNOL", "ALLEMAND"], "Note annuelle"), "Coefficient": 3},
        {"Partie": "LVB Terminale", "Note": trouver_matiere(terminale, ["ITALIEN", "ESPAGNOL", "ALLEMAND"], "Note retenue"), "Coefficient": 3},
        {"Partie": "Enseignement scientifique Première", "Note": trouver_matiere(premiere, ["ENSEIGNEMENT SCIENTIFIQUE"], "Note annuelle"), "Coefficient": 3},
        {"Partie": "Enseignement scientifique Terminale", "Note": trouver_matiere(terminale, ["ENSEIGNEMENT SCIENTIFIQUE"], "Note retenue"), "Coefficient": 3},
        {"Partie": "EMC Première", "Note": trouver_matiere(premiere, ["EMC"], "Note annuelle"), "Coefficient": 1},
        {"Partie": "EMC Terminale", "Note": trouver_matiere(terminale, ["EMC"], "Note retenue"), "Coefficient": 1},
        {"Partie": "EPS Terminale", "Note": trouver_matiere(terminale, ["EPS"], "Note retenue"), "Coefficient": 6},
        {"Partie": "Spécialité abandonnée Première", "Note": note_depuis_table(premiere, spe_abandonnee, "Note annuelle"), "Coefficient": 8},
    ]

    df_cc = pd.DataFrame(lignes_cc)

    df_cc_modifie = st.data_editor(
        df_cc,
        use_container_width=True,
        disabled=["Partie", "Coefficient"],
        column_config={
            "Note": st.column_config.NumberColumn("Note", min_value=0.0, max_value=20.0, step=0.01, format="%.2f"),
            "Coefficient": st.column_config.NumberColumn("Coefficient", disabled=True),
        },
        key="cc_final_editor"
    )

    df_cc_modifie["Points"] = df_cc_modifie["Note"] * df_cc_modifie["Coefficient"]
    points_controle_continu = float(df_cc_modifie["Points"].sum())
    moyenne_controle_continu = points_controle_continu / 40

    st.success(f"Contrôle continu : {points_controle_continu:.2f} points sur 800, soit {moyenne_controle_continu:.2f}/20.")

    st.divider()

    st.subheader("3. Français et options")

    col_fr1, col_fr2 = st.columns(2)
    with col_fr1:
        francais_ecrit = st.number_input("Français écrit", min_value=0.0, max_value=20.0, value=11.0, step=0.25)
    with col_fr2:
        francais_oral = st.number_input("Français oral", min_value=0.0, max_value=20.0, value=17.0, step=0.25)

    a_maths_expertes = st.checkbox("Maths expertes", value=("Maths expertes" in terminale["Matière"].tolist()))
    note_matex_auto = note_depuis_table(terminale, "Maths expertes", "Note retenue") or 17.0

    if a_maths_expertes:
        note_maths_expertes = st.number_input("Note de maths expertes", min_value=0.0, max_value=20.0, value=float(round(note_matex_auto, 2)), step=0.25)
    else:
        note_maths_expertes = 0.0

    coef_total = 100 + (2 if a_maths_expertes else 0)
    coef_deja_connus = 40 + 5 + 5 + (2 if a_maths_expertes else 0)

    points_deja_connus = points_controle_continu + francais_ecrit * 5 + francais_oral * 5
    if a_maths_expertes:
        points_deja_connus += note_maths_expertes * 2

    moyenne_deja_connue = points_deja_connus / coef_deja_connus
    st.info(f"Points déjà connus : {points_deja_connus:.2f} points sur {coef_deja_connus} coefficients, soit {moyenne_deja_connue:.2f}/20 sur les notes connues.")

    st.divider()

    st.subheader("4. Épreuves finales et mention visée")

    epreuves_finales = {"Philosophie": 8, "Grand oral": 10}
    ajouter_specialite(epreuves_finales, spe1)
    ajouter_specialite(epreuves_finales, spe2)

    objectif = st.selectbox("Mention demandée", list(SEUILS.keys()), index=3)
    seuil = SEUILS[objectif]
    points_objectif = seuil * coef_total
    points_a_obtenir = points_objectif - points_deja_connus
    coef_restant = sum(epreuves_finales.values())

    st.warning(f"Objectif actuellement sélectionné : {objectif}")
    st.write(f"Points nécessaires : {points_objectif:.2f} points.")
    st.write(f"Points à obtenir sur les épreuves finales restantes : {points_a_obtenir:.2f} points.")

    if points_a_obtenir <= 0:
        st.success("Cet objectif est déjà atteint avec les notes connues.")
    elif points_a_obtenir > coef_restant * 20:
        st.error("Cet objectif est impossible, même avec 20 partout aux épreuves finales.")
    else:
        st.write(f"Moyenne nécessaire sur les épreuves restantes : {points_a_obtenir / coef_restant:.2f}/20.")

    st.divider()

    st.subheader("5. Fixe les notes que tu veux")

    notes_fixees = {}
    matieres_auto = []

    for matiere, coef in epreuves_finales.items():
        st.markdown(f"#### {matiere} — coefficient {coef}")
        fixer = st.checkbox(f"Fixer la note de {matiere}", key=f"fixer_{matiere}")
        if fixer:
            note = st.slider(f"Note en {matiere}", 0.0, 20.0, 12.0, 0.25, key=f"note_{matiere}")
            notes_fixees[matiere] = note
        else:
            matieres_auto.append(matiere)

    points_fixees = sum(notes_fixees[m] * epreuves_finales[m] for m in notes_fixees)
    coef_auto = sum(epreuves_finales[m] for m in matieres_auto)
    note_auto = (points_a_obtenir - points_fixees) / coef_auto if coef_auto > 0 else None

    notes_finales = {}
    for matiere in epreuves_finales:
        if matiere in notes_fixees:
            notes_finales[matiere] = notes_fixees[matiere]
        else:
            if note_auto is None:
                notes_finales[matiere] = 0.0
            elif note_auto < 0:
                notes_finales[matiere] = 0.0
            elif note_auto > 20:
                notes_finales[matiere] = 20.0
            else:
                notes_finales[matiere] = note_auto

    st.subheader("6. Notes ajustées automatiquement")

    if coef_auto == 0:
        st.write("Toutes les notes sont fixées.")
    elif note_auto < 0:
        st.success("Objectif déjà atteint avec les notes fixées : les autres peuvent être à 0.")
    elif note_auto > 20:
        st.error(f"Objectif impossible avec les notes fixées : il faudrait {note_auto:.2f}/20 sur les matières non fixées.")
    else:
        st.success(f"Pour atteindre {objectif}, les matières non fixées doivent être à environ {note_auto:.2f}/20.")

    for matiere in matieres_auto:
        st.slider(
            f"{matiere} ajusté automatiquement",
            min_value=0.0,
            max_value=20.0,
            value=float(round(notes_finales[matiere], 2)),
            step=0.25,
            disabled=True,
            key=f"auto_{matiere}"
        )

    st.divider()

    st.subheader("7. Résultat final estimé")

    points_finales = sum(notes_finales[m] * epreuves_finales[m] for m in notes_finales)
    points_total = points_deja_connus + points_finales
    moyenne_finale = points_total / coef_total

    st.write(f"Total final estimé : {points_total:.2f} points sur {coef_total * 20:.0f} points.")
    st.write(f"Moyenne finale estimée : {moyenne_finale:.2f}/20.")

    if moyenne_finale >= 18:
        st.success("Résultat : mention très bien avec félicitations du jury")
    elif moyenne_finale >= 16:
        st.success("Résultat : mention très bien")
    elif moyenne_finale >= 14:
        st.success("Résultat : mention bien")
    elif moyenne_finale >= 12:
        st.success("Résultat : mention assez bien")
    elif moyenne_finale >= 10:
        st.warning("Résultat : bac obtenu sans mention")
    else:
        st.error("Résultat : bac non obtenu")

    st.subheader("8. Détail des épreuves finales")
    for matiere, note in notes_finales.items():
        coef = epreuves_finales[matiere]
        st.write(f"- {matiere} : {note:.2f}/20, coefficient {coef}, soit {note * coef:.2f} points")
