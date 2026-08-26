import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import re
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz
import uuid
import time

plans = {
    "solo": {"clients": 20, "credits": 50, "prix": 2000, "devise": "FCFA"},
    "starter": {"clients": 50, "credits": 100, "prix": 5000, "devise": "FCFA"},
    "pro": {"clients": 200, "credits": 500, "prix": 10000, "devise": "FCFA"}
}

st.set_page_config(page_title="One7 Pro - TVA & AIB", page_icon="🧾", layout="wide")

# 1. CONNEXION SECRETS
supabase: Client = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel('gemini-1.5-flash')

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

# 3. FONCTIONS FOND : MULTI-TENANT + CREDITS
def signup_user(nom, email, password, pays, plan):
    try:
        # 1. CRÉER USER DANS AUTH D'ABORD
        res_auth = supabase.auth.sign_up({"email": email, "password": password})
        
        if not res_auth.user:
            st.error("Erreur création Auth. Vérifie l'email")
            return False
            
        user = res_auth.user
        time.sleep(1) # <- ON ATTEND 1 SECONDE QUE SUPABASE CRÉE LE USER

        # 2. CRÉER CABINET
        cab_id = str(uuid.uuid4())
        supabase.table("cabinets").insert({
    "id": cab_id, 
    "nom": nom, 
    "pays": pays, 
    "plan": plan,
    "statut": "actif",
    "limite_clients": plans[plan]["clients"], 
    "limite_credits": plans[plan]["credits"],
    "prix": plans[plan]["prix"], # <- AJOUTE ÇA
    "date_expiration": (datetime.now() + timedelta(days=90)).isoformat() # <- 90 jours promo
}).execute()
        
        # 3. CRÉER PROFIL ENSUITE
        supabase.table("profiles").insert({
            "id": user.id, 
            "cabinet_id": cab_id, 
            "nom": nom,
            "role": "cabinet", 
            "plan": plan,
            "credits_restants": plans[plan]["credits"]
        }).execute()
        
        return True
        
    except Exception as e:
        st.error(f"Erreur: {e}")
        return False
def login(email, password):
    try: return supabase.auth.sign_in_with_password({"email": email, "password": password})
    except: return None

def get_cabinet_data():
    user = supabase.auth.get_user()
    if not user.user: return None
    profile = supabase.table("profiles").select("cabinet_id, role").eq("id", user.user.id).single().execute()
    cabinet = supabase.table("cabinets").select("*").eq("id", profile.data["cabinet_id"]).single().execute()
    return {"user": user.user, "profile": profile.data, "cabinet": cabinet.data}

def use_credits(cab_id, nb):
    cab = supabase.table("cabinets").select("credits_restants").eq("id", cab_id).single().execute()
    if cab.data["credits_restants"] >= nb:
        supabase.table("cabinets").update({"credits_restants": cab.data["credits_restants"] - nb}).eq("id", cab_id).execute()
        return True
    return False

# 4. LOGIQUE METIER : TON CODE TVA/AIB
EXONERATIONS_TVA = ["produit pharmaceutique", "livre", "produit agricole non transformé", "éducation", "santé", "exportation", "location immobilière habitation"]
TAUX_AIB = {"biens": 0.01, "travaux": 0.03, "prestation": 0.03, "prestation_intel": 0.05}

def analyser_eligibilite(data, seuil_aib):
    eligible_tva = "Oui"; eligible_aib = "Oui"; motif = []; tva_montant = data.get("tva", 0); aib_montant = 0; taux_aib_applique = 0
    ht = data.get("ht", 0); libelle = data.get("libelle", "").lower(); nifu = data.get("nifu", ""); type_op = data.get("type_operation", "biens").lower(); vendeur = data.get("fournisseur", "").lower(); n_facture = data.get("n_facture", "").lower()
    if not nifu or nifu == "N/A":
        eligible_tva = "Non"; eligible_aib = "Non"; motif.append("⚠️ Absence de N°IFU Art 227"); tva_montant = 0; aib_montant = 0; return eligible_tva, eligible_aib, " | ".join(motif), tva_montant, aib_montant, 0.0
    if "sfe en ligne" in vendeur or "mec" in n_facture or data.get("aib_deja_retenu", False):
        eligible_aib = "Non"; motif.append("AIB déjà retenu à la source"); aib_montant = 0
    elif any(exo in libelle for exo in EXONERATIONS_TVA):
        eligible_tva = "Non"; motif.append(f"Exonéré TVA Art 229 CGI"); tva_montant = 0
    if seuil_aib and ht < 10000 and eligible_aib == "Oui":
        eligible_aib = "Non"; motif.append("HT < 10 000 FCFA")
    if eligible_aib == "Oui":
        if "intellectuelle" in type_op or any(x in libelle for x in ["avocat", "comptable", "consultant"]): taux_aib_applique = TAUX_AIB["prestation_intel"]
        elif "travaux" in type_op: taux_aib_applique = TAUX_AIB["travaux"]
        elif "prestation" in type_op or "service" in type_op: taux_aib_applique = TAUX_AIB["prestation"]
        else: taux_aib_applique = TAUX_AIB["biens"]
        aib_montant = ht * taux_aib_applique
    if not motif: motif = ["Éligible"]
    return eligible_tva, eligible_aib, " | ".join(motif), tva_montant, aib_montant, taux_aib_applique*100

def sauver_factures_en_lot(liste_factures):
    if liste_factures: supabase.table('factures').insert(liste_factures).execute()

# 5. PAGES
st.markdown("## 🚀 OFFRE DE LANCEMENT - 3 PREMIERS MOIS")
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("### Solo")
    st.markdown("**2000 FCFA/mois**")
    st.write("20 clients")
    st.write("50 crédits")

with col2:
    st.markdown("### Starter")
    st.markdown("**5000 FCFA/mois**")
    st.write("50 clients")
    st.write("100 crédits")

with col3:
    st.markdown("### Pro")
    st.markdown("**10000 FCFA/mois**")
    st.write("200 clients")
    st.write("500 crédits")
  
def page_login_signup():
    tab1, tab2 = st.tabs(["Connexion", "Créer un compte"])
    with tab1:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Mot de passe", type="password", key="login_password")
        if st.button("Se connecter", type="primary", key="btn_login"):
            res = login(email, password)
            if res: st.session_state.user = res.user; st.rerun()
            else: st.error("Identifiants incorrects")

    with tab2:
        nom = st.text_input("Nom du Cabinet", key="signup_nom")
        email = st.text_input("Email", key="signup_email")
        password = st.text_input("Mot de passe", type="password", key="signup_password")
        pays = st.selectbox("Pays", ["Bénin", "Autres pays"], key="signup_pays")
        plan = st.selectbox("Choisis ton Pack", ["solo", "starter", "pro"])
        st.success(f"**Tarif Promo 3 mois: {plans[plan]['prix']:,} {plans[plan]['devise']}/mois**")
        st.caption(f"Inclus: {plans[plan]['clients']} clients, {plans[plan]['credits']} crédits")
        st.caption("⚡ Après 3 mois : tarif normal s'appliquera")
        if st.button("Créer mon compte", key="btn_signup"):
        if signup_user(nom, email, password, pays, plan): st.success("Compte créé! Paiement simulé. Attends l'activation de l'admin.")

def page_app(cab_data):
    cab = cab_data["cabinet"]
    with st.sidebar:
        st.title("🧾 One7 Pro")
        st.caption(f"Plan: {cab['plan']} | {cab['pays']}")
        st.metric("Crédits Restants", cab["credits_restants"])
        if st.button("Se déconnecter", key="btn_logout"): supabase.auth.sign_out(); del st.session_state.user; st.rerun()
        menu = st.radio("Menu", ["📊 Traitement Factures", "📈 Dashboard", "👔 Admin"], key="menu_radio")

    if not cab["abonnement_actif"]:
        st.warning("Votre abonnement n'est pas actif. Contactez l'admin."); return

    if menu == "📊 Traitement Factures":
        st.title("🧾 Traitement TVA & AIB")
        SEUIL_AIB = st.checkbox("Appliquer seuil 10 000 FCFA", value=False, key="check_seuil")
        fichiers = st.file_uploader("Charge tes factures PDF", type=["pdf"], accept_multiple_files=True, key="uploader_factures")

        if st.button("🚀 Lancer le traitement", type="primary", key="btn_traiter"):
            if not fichiers: st.warning("Ajoute des fichiers"); return
            if not use_credits(cab["id"], len(fichiers)): st.error(f"Crédits insuffisants. Il faut {len(fichiers)}"); st.rerun(); return

            resultats_detail, etat_tva, etat_aib, factures_a_sauver = [], [], [], []
            progress = st.progress(0)
            for i, fichier in enumerate(fichiers):
                file_bytes = fichier.read()
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                texte_pdf = "".join([page.get_text() for page in doc])
                prompt = f"""Tu es un expert comptable au Bénin. Extrait en JSON strict: {{"n_facture": "", "date": "JJ/MM/AAAA", "fournisseur": "", "nifu": "", "libelle": "", "type_operation": "biens/travaux/prestation", "ht": 0, "tva": 0, "ttc": 0, "aib_deja_retenu": false}}. Texte: {texte_pdf}"""
                response = model.generate_content(prompt)
                try: json_str = re.search(r'\{.*\}', response.text, re.DOTALL).group(); data = json.loads(json_str)
                except: data = {"n_facture": fichier.name}

                elig_tva, elig_aib, motif, tva_final, aib_final, taux_aib = analyser_eligibilite(data, SEUIL_AIB)
                factures_a_sauver.append({"cabinet_id": cab["id"], "numero_facture": data.get("n_facture"), "fournisseur": data.get("fournisseur"), "nif_fournisseur": data.get("nifu"), "montant_ht": data.get("ht"), "tva": tva_final, "aib": aib_final, "data_brute": data})
                resultats_detail.append({"Fichier": fichier.name, "N° Facture": data.get("n_facture"), "HT": data.get("ht"), "TVA 18%": tva_final, "AIB": aib_final, "Éligible TVA": elig_tva, "Éligible AIB": elig_aib, "Motif": motif})
                if elig_tva == "Oui": etat_tva.append({"NIFU_FOURNISSEUR": data.get("nifu"), "NUM_FACTURE": data.get("n_facture"), "MONTANT_HT": data.get("ht"), "MONTANT_TVA": tva_final})
                if elig_aib == "Oui": etat_aib.append({"NIFU_BENEFICIAIRE": data.get("nifu"), "NUM_FACTURE": data.get("n_facture"), "BASE_AIB": data.get("ht"), "TAUX_AIB": f"{taux_aib}%", "MONTANT_AIB": aib_final})
                progress.progress((i+1)/len(fichiers))

            sauver_factures_en_lot(factures_a_sauver)
            st.success(f"{len(fichiers)} factures traitées et sauvées! -{len(fichiers)} crédits")
            tab1, tab2, tab3 = st.tabs(["📊 Détail", "📑 Etat TVA", "📑 Etat AIB"])
            with tab1: st.dataframe(pd.DataFrame(resultats_detail), use_container_width=True, hide_index=True)
            with tab2: st.dataframe(pd.DataFrame(etat_tva), use_container_width=True, hide_index=True); st.download_button("📥 Etat TVA CSV", pd.DataFrame(etat_tva).to_csv(index=False), f"ETAT_TVA_{datetime.now().strftime('%Y%m')}.csv", key="dl_tva")
            with tab3: st.dataframe(pd.DataFrame(etat_aib), use_container_width=True, hide_index=True); st.download_button("📥 Etat AIB CSV", pd.DataFrame(etat_aib).to_csv(index=False), f"ETAT_AIB_{datetime.now().strftime('%Y%m')}.csv", key="dl_aib")

    elif menu == "👔 Admin" and cab_data["profile"]["role"] == "admin":
        st.title("Panel Admin")
        cabinets = supabase.table("cabinets").select("*").execute()
        for c in cabinets.data:
            st.write(f"{c['nom']} - Crédits: {c['credits_restants']} - Actif: {c['abonnement_actif']}")
            if not c['abonnement_actif']:
                if st.button(f"Activer {c['nom']}", key=f"activate_{c['id']}"):
                    supabase.table("cabinets").update({"abonnement_actif": True}).eq("id", c['id']).execute(); st.rerun()

# 6. ROUTEUR
def main():
    if 'user' not in st.session_state: page_login_signup()
    else:
        cab_data = get_cabinet_data()
        if cab_data: page_app(cab_data)

if __name__ == "__main__": main()
