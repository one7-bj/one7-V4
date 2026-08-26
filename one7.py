import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import re
from datetime import datetime
from supabase import create_client, Client
import fitz # Ajoute PyMuPDF pour lire PDF texte

st.set_page_config(page_title="One7 Pro - TVA & AIB Bénin", page_icon="🧾", layout="wide")

# 1. CONNEXION SECRETS
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
GOOGLE_API_KEY = st.secrets["GEMINI_API_KEY"] # Tu l'appelais GEMINI_API_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # 3.6 n'existe pas, j'ai mis 1.5-flash

# 2. CSS GLOBAL : BLEU PRO #0056A6 + JAUNE #FFC107
st.markdown("""
<style>
    .stButton>button[data-testid="baseButton-primary"] {background-color: #0056A6; color: white; border-radius: 8px; border: none; font-weight: 600;}
    .stButton>button[data-testid="baseButton-primary"]:hover {background-color: #004080;}
    .stButton>button[data-testid="baseButton-secondary"] {background-color: #FFC107; color: #1E293B; border-radius: 8px; border: none; font-weight: 700;}
    [data-testid="stSidebar"] {background-color: #0056A6;}
    [data-testid="stSidebar"] * {color: white;}
</style>
""", unsafe_allow_html=True)

# 3. FONCTIONS SUPABASE
def get_user_credits(cabinet_id):
    res = supabase.table('credits').select('solde').eq('cabinet_id', cabinet_id).single().execute()
    return res.data['solde'] if res.data else 0

def sauver_factures_en_lot(liste_factures, cabinet_id):
    if liste_factures:
        supabase.table('factures').insert(liste_factures).execute()
        # Décrémente 1 crédit par facture
        for _ in liste_factures:
            supabase.rpc('decrement_credit', {'cab_id': cabinet_id}).execute()

# 4. LOGIQUE METIER - TON CODE
EXONERATIONS_TVA = ["produit pharmaceutique", "livre", "produit agricole non transformé", "éducation", "santé", "exportation", "location immobilière habitation"]
TAUX_AIB = {"biens": 0.01, "travaux": 0.03, "prestation": 0.03, "prestation_intel": 0.05}

def analyser_eligibilite(data, seuil_aib):
    # TON CODE ICI INCHANGÉ
    eligible_tva = "Oui"; eligible_aib = "Oui"; motif = []; tva_montant = data.get("tva", 0); aib_montant = 0; taux_aib_applique = 0
    ht = data.get("ht", 0); libelle = data.get("libelle", "").lower(); nifu = data.get("nifu", ""); type_op = data.get("type_operation", "biens").lower(); vendeur = data.get("fournisseur", "").lower(); n_facture = data.get("n_facture", "").lower()
    if not nifu or nifu == "N/A":
        eligible_tva = "Non"; eligible_aib = "Non"; motif.append("⚠️ Absence de N°IFU Art 227 - Non éligible à la déduction"); tva_montant = 0; aib_montant = 0; return eligible_tva, eligible_aib, " | ".join(motif), tva_montant, aib_montant, 0.0
    if "sfe en ligne" in vendeur or "mec" in n_facture or data.get("aib_deja_retenu", False):
        eligible_aib = "Non"; motif.append("AIB déjà retenu à la source par la plateforme DGI - Facture Normalisée Art 130"); aib_montant = 0
    elif any(exo in libelle for exo in EXONERATIONS_TVA):
        eligible_tva = "Non"; motif.append(f"Exonéré TVA Art 229 CGI"); tva_montant = 0
    if seuil_aib and ht < 10000 and eligible_aib == "Oui":
        eligible_aib = "Non"; motif.append("HT < 10 000 FCFA - Tolérance adm")
    if eligible_aib == "Oui":
        if "intellectuelle" in type_op or any(x in libelle for x in ["avocat", "comptable", "consultant", "audit"]): taux_aib_applique = TAUX_AIB["prestation_intel"]
        elif "travaux" in type_op: taux_aib_applique = TAUX_AIB["travaux"]
        elif "prestation" in type_op or "service" in type_op: taux_aib_applique = TAUX_AIB["prestation"]
        else: taux_aib_applique = TAUX_AIB["biens"]
        aib_montant = ht * taux_aib_applique
    if not motif: motif = ["Éligible"]
    return eligible_tva, eligible_aib, " | ".join(motif), tva_montant, aib_montant, taux_aib_applique*100

# 5. PAGE LOGIN
def page_login():
    st.title("🔐 One7 Pro - Connexion Cabinet")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Mot de passe", type="password")
        if st.form_submit_button("Se connecter", type="primary"):
            try:
                res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.user = res.user
                st.rerun()
            except: st.error("Email ou mot de passe incorrect")

# 6. APP PRINCIPALE
def page_app():
    user = st.session_state.user
    credits = get_user_credits(user.id)
    
    with st.sidebar:
        st.title("🧾 One7 Pro")
        st.caption("TVA & AIB Bénin - CGI 2026")
        st.markdown(f"**Cabinet:** {user.email}")
        st.markdown(f"**Crédits:** {credits}")
        if st.button("Se déconnecter"): del st.session_state.user; st.rerun()
        page = st.radio("Menu", ["📊 Traitement Factures", "👔 Gestion Clients", "📈 Dashboard Cabinet", "💬 Assistant Fiscal"])

    SEUIL_AIB = st.sidebar.checkbox("Appliquer seuil 10 000 FCFA", value=False)

    if page == "📊 Traitement Factures":
        st.title("🧾 One7 Pro - Déclaration TVA & AIB Bénin")
        fichiers = st.file_uploader("Charge tes factures PDF", type=["pdf"], accept_multiple_files=True) # PDF seulement pour fitz

        if st.button("🚀 Lancer le traitement", type="primary"):
            if credits < len(fichiers): st.error(f"Crédits insuffisants. Il te faut {len(fichiers)} crédits, tu as {credits}"); return
            if fichiers:
                resultats_detail, etat_tva, etat_aib, factures_a_sauver = [], [], [], []
                progress = st.progress(0)
                for i, fichier in enumerate(fichiers):
                    file_bytes = fichier.read()
                    doc = fitz.open(stream=file_bytes, filetype="pdf") # Lire PDF pour Gemini
                    texte_pdf = "".join([page.get_text() for page in doc])
                    
                    prompt = f"""Tu es un expert comptable au Bénin. Extrait de ce texte de facture en JSON strict: {{"n_facture": "", "date": "JJ/MM/AAAA", "fournisseur": "", "nifu": "", "libelle": "", "type_operation": "biens/travaux/prestation", "ht": 0, "tva": 0, "ttc": 0, "aib_deja_retenu": false}}. Texte: {texte_pdf}"""
                    response = model.generate_content(prompt)
                    try: json_str = re.search(r'\{.*\}', response.text, re.DOTALL).group(); data = json.loads(json_str)
                    except: data = {"n_facture": fichier.name}

                    elig_tva, elig_aib, motif, tva_final, aib_final, taux_aib = analyser_eligibilite(data, SEUIL_AIB)
                    
                    # Préparer pour sauvegarde Supabase
                    factures_a_sauver.append({"cabinet_id": user.id, "numero_facture": data.get("n_facture"), "fournisseur": data.get("fournisseur"), "nif_fournisseur": data.get("nifu"), "montant_ht": data.get("ht"), "tva": tva_final, "aib": aib_final, "data_brute": data})

                    # TON CODE D'AFFICHAGE INCHANGÉ
                    resultats_detail.append({"Fichier": fichier.name, "N° Facture": data.get("n_facture"), "HT": data.get("ht"), "TVA 18%": tva_final, "AIB": aib_final, "Éligible TVA": elig_tva, "Éligible AIB": elig_aib, "Motif": motif})
                    if elig_tva == "Oui": etat_tva.append({"NIFU_FOURNISSEUR": data.get("nifu"), "NUM_FACTURE": data.get("n_facture"), "MONTANT_HT": data.get("ht"), "MONTANT_TVA": tva_final})
                    if elig_aib == "Oui": etat_aib.append({"NIFU_BENEFICIAIRE": data.get("nifu"), "NUM_FACTURE": data.get("n_facture"), "BASE_AIB": data.get("ht"), "TAUX_AIB": f"{taux_aib}%", "MONTANT_AIB": aib_final})
                    progress.progress((i+1)/len(fichiers))
                
                sauver_factures_en_lot(factures_a_sauver, user.id) # SAUVEGARDE + DÉCOMPTE

                st.success(f"{len(fichiers)} factures traitées et sauvées ! -{len(fichiers)} crédits")
                tab1, tab2, tab3 = st.tabs(["📊 Détail", "📑 Etat TVA", "📑 Etat AIB"])
                with tab1: st.dataframe(pd.DataFrame(resultats_detail), use_container_width=True, hide_index=True)
                with tab2: st.dataframe(pd.DataFrame(etat_tva), use_container_width=True, hide_index=True); st.download_button("📥 Télécharger Etat TVA", pd.DataFrame(etat_tva).to_csv(index=False), f"ETAT_TVA_{datetime.now().strftime('%Y%m')}.csv")
                with tab3: st.dataframe(pd.DataFrame(etat_aib), use_container_width=True, hide_index=True); st.download_button("📥 Télécharger Etat AIB", pd.DataFrame(etat_aib).to_csv(index=False), f"ETAT_AIB_{datetime.now().strftime('%Y%m')}.csv")

    elif page == "💬 Assistant Fiscal":
        st.header("💬 Assistant Fiscal One7")
        question = st.text_input("Pose ta question sur TVA ou AIB")
        if question: st.info(model.generate_content(f"Tu es un expert fiscal au Bénin. Réponds en te basant sur le CGI 2026. Cite l'article. Question: {question}").text)

# ROUTEUR
if 'user' not in st.session_state: page_login()
else: page_app()
