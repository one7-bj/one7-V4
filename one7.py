import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import re
from datetime import datetime

# 1. CONFIG DE LA PAGE
st.set_page_config(
    page_title="One7 Pro - TVA & AIB Bénin", 
    page_icon="🧾", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# 2. CSS GLOBAL : BLEU PRO #0056A6 + JAUNE #FFC107
st.markdown("""
<style>
    /* Bouton Principal : Bleu Pro */
    .stButton>button[data-testid="baseButton-primary"] {
        background-color: #0056A6;
        color: white;
        border-radius: 8px;
        border: none;
        font-weight: 600;
    }
    .stButton>button[data-testid="baseButton-primary"]:hover {
        background-color: #004080;
    }
    /* Bouton Secondaire : Jaune */
    .stButton>button[data-testid="baseButton-secondary"] {
        background-color: #FFC107;
        color: #1E293B;
        border-radius: 8px;
        border: none;
        font-weight: 700;
    }
    /* Sidebar Cabinet */
    [data-testid="stSidebar"] {
        background-color: #0056A6;
    }
    [data-testid="stSidebar"] * {
        color: white;
    }
</style>
""", unsafe_allow_html=True)

# 3. SIDEBAR CABINET V4
with st.sidebar:
    st.title("🧾 One7 Pro")
    st.caption("TVA & AIB Bénin - CGI 2026")
    st.markdown("**Cabinet:** Cabinet Alpha")
    st.markdown("**Pack:** Solo | **Crédits:** 200")
    
    page = st.radio("Menu", [
        "📊 Traitement Factures", 
        "👔 Gestion Clients", 
        "📈 Dashboard Cabinet",
        "💬 Assistant Fiscal"
    ])

# 4. CONFIG GEMINI + LOGIQUE METIER - TON CODE DE DEPART
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel('gemini-3.6-flash')

EXONERATIONS_TVA = [
    "produit pharmaceutique", "livre", "produit agricole non transformé", 
    "éducation", "santé", "exportation", "location immobilière habitation"
] # Art 229

TAUX_AIB = {
    "biens": 0.01, "travaux": 0.03, "prestation": 0.03, "prestation_intel": 0.05
}
SEUIL_AIB = st.sidebar.checkbox("Appliquer seuil 10 000 FCFA", value=False)

def analyser_eligibilite(data):
    eligible_tva = "Oui"
    eligible_aib = "Oui"
    motif = []
    tva_montant = data.get("tva", 0)
    aib_montant = 0
    taux_aib_applique = 0

    ht = data.get("ht", 0)
    libelle = data.get("libelle", "").lower()
    nifu = data.get("nifu", "")
    type_op = data.get("type_operation", "biens").lower()
    vendeur = data.get("fournisseur", "").lower()
    n_facture = data.get("n_facture", "").lower()

    if not nifu or nifu == "N/A":
        eligible_tva = "Non"
        eligible_aib = "Non"
        motif.append("⚠️ Absence de N°IFU Art 227 - Non éligible à la déduction")
        tva_montant = 0
        aib_montant = 0
        return eligible_tva, eligible_aib, " | ".join(motif), tva_montant, aib_montant, 0.0

    if "sfe en ligne" in vendeur or "mec" in n_facture or data.get("aib_deja_retenu", False):
        eligible_aib = "Non"
        motif.append("AIB déjà retenu à la source par la plateforme DGI - Facture Normalisée Art 130")
        aib_montant = 0

    elif any(exo in libelle for exo in EXONERATIONS_TVA):
        eligible_tva = "Non"
        motif.append(f"Exonéré TVA Art 229 CGI")
        tva_montant = 0

    if SEUIL_AIB and ht < 10000 and eligible_aib == "Oui":
        eligible_aib = "Non"
        motif.append("HT < 10 000 FCFA - Tolérance adm")

    if eligible_aib == "Oui":
        if "intellectuelle" in type_op or any(x in libelle for x in ["avocat", "comptable", "consultant", "audit"]):
            taux_aib_applique = TAUX_AIB["prestation_intel"]
        elif "travaux" in type_op:
            taux_aib_applique = TAUX_AIB["travaux"]
        elif "prestation" in type_op or "service" in type_op:
            taux_aib_applique = TAUX_AIB["prestation"]
        else:
            taux_aib_applique = TAUX_AIB["biens"]
        aib_montant = ht * taux_aib_applique

    if not motif: motif = ["Éligible"]
    return eligible_tva, eligible_aib, " | ".join(motif), tva_montant, aib_montant, taux_aib_applique*100

# 5. PAGES
if page == "📊 Traitement Factures":
    st.title("🧾 One7 Pro - Déclaration TVA & AIB Bénin")
    st.caption("Version Prudente Légale - Conforme CGI 2026 + SFE DGI")

    fichiers = st.file_uploader("Charge tes factures PDF/Images", type=["pdf", "png", "jpg"], accept_multiple_files=True)

    if st.button("🚀 Lancer le traitement", type="primary"):
        if fichiers:
            resultats_detail, etat_tva, etat_aib = [], []
            progress = st.progress(0)

            for i, fichier in enumerate(fichiers):
                file_bytes = fichier.read()
                prompt = """
                Tu es un expert comptable au Bénin. Extrait de cette facture en JSON strict:
                {"n_facture": "", "date": "JJ/MM/AAAA", "fournisseur": "", "nifu": "", "libelle": "", "type_operation": "biens/travaux/prestation", "ht": 0, "tva": 0, "ttc": 0, "aib_deja_retenu": false}
                Si mention "AIB retenu" ou "SFE" est présente mets "aib_deja_retenu": true. Ne réponds que le JSON.
                """
                response = model.generate_content([prompt, {"mime_type": fichier.type, "data": file_bytes}])
                try:
                    json_str = re.search(r'\{.*\}', response.text, re.DOTALL).group()
                    data = json.loads(json_str)
                except:
                    data = {"n_facture": fichier.name}

                elig_tva, elig_aib, motif, tva_final, aib_final, taux_aib = analyser_eligibilite(data)

                resultats_detail.append({
                    "Fichier": fichier.name, "N° Facture": data.get("n_facture"), "Date": data.get("date"),
                    "Fournisseur": data.get("fournisseur"), "N°IFU": data.get("nifu"), "HT": data.get("ht"),
                    "TVA 18%": tva_final, "AIB": aib_final, "Taux AIB": f"{taux_aib}%",
                    "Éligible TVA": elig_tva, "Éligible AIB": elig_aib, "Motif": motif
                })

                if elig_tva == "Oui":
                    etat_tva.append({"NIFU_FOURNISSEUR": data.get("nifu"), "NUM_FACTURE": data.get("n_facture"), "DATE_FACTURE": data.get("date"), "MONTANT_HT": data.get("ht"), "MONTANT_TVA": tva_final})
                
                if elig_aib == "Oui":
                    etat_aib.append({"NIFU_BENEFICIAIRE": data.get("nifu"), "NUM_FACTURE": data.get("n_facture"), "DATE_FACTURE": data.get("date"), "BASE_AIB": data.get("ht"), "TAUX_AIB": f"{taux_aib}%", "MONTANT_AIB": aib_final})
                progress.progress((i+1)/len(fichiers))

            st.success(f"{len(fichiers)} factures traitées!")
            tab1, tab2, tab3 = st.tabs(["📊 Détail", "📑 Etat TVA e-impots", "📑 Etat AIB e-impots"])
            
            with tab1: 
                st.dataframe(pd.DataFrame(resultats_detail), use_container_width=True, hide_index=True)
                st.warning("Les lignes avec 'Absence de N°IFU' ne doivent pas être déclarées. A régulariser avec le fournisseur.")
            with tab2: 
                st.dataframe(pd.DataFrame(etat_tva), use_container_width=True, hide_index=True)
                st.download_button("📥 Télécharger Etat TVA", pd.DataFrame(etat_tva).to_csv(index=False), f"ETAT_TVA_{datetime.now().strftime('%Y%m')}.csv")
            with tab3: 
                st.dataframe(pd.DataFrame(etat_aib), use_container_width=True, hide_index=True)
                st.download_button("📥 Télécharger Etat AIB", pd.DataFrame(etat_aib).to_csv(index=False), f"ETAT_AIB_{datetime.now().strftime('%Y%m')}.csv")
        else:
            st.warning("Charge au moins 1 facture")

elif page == "👔 Gestion Clients":
    st.title("Gestion Clients Cabinet")
    st.info("Ici on connectera la table `clients` de Supabase")
    st.button("➕ Ajouter un Client", type="secondary")

elif page == "📈 Dashboard Cabinet":
    st.title("Dashboard Cabinet")
    col1, col2, col3 = st.columns(3)
    with col1: st.metric("Clients Actifs", "8 / 10")
    with col2: st.metric("Crédits Restants", "200")
    with col3: st.metric("Factures Traitées", "42")

elif page == "💬 Assistant Fiscal":
    st.header("💬 Assistant Fiscal One7")
    question = st.text_input("Pose ta question sur TVA ou AIB. Ex: Quel article parle des exonérations ?")
    if question:
        with st.spinner("Je cherche dans le CGI..."):
            reponse = model.generate_content(f"Tu es un expert fiscal au Bénin. Réponds en te basant sur le CGI 2026. Cite l'article. Question: {question}")
            st.info(reponse.text)

# 6. FOOTER
st.divider()
st.caption("One7 Pro v4.0 | SaaS Cabinet Fiscal & Comptable | Made in Bénin 🇧🇯")