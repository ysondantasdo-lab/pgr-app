import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import os
import datetime
import subprocess
import io
import markupsafe
import jinja2

# Correção de compatibilidade para a biblioteca secretary no Python >= 3.10
jinja2.Markup = markupsafe.Markup
jinja2.contextfilter = getattr(jinja2, 'pass_context', None)
jinja2.evalcontextfilter = getattr(jinja2, 'pass_eval_context', None)
jinja2.environmentfilter = getattr(jinja2, 'pass_environment', None)

from secretary import Renderer
import traceback

# Configurações de layout
st.set_page_config(page_title="PGR Dinâmico em Nuvem", layout="wide")

# ------------------------------------------------------------------------------
# 1. SEGURANÇA E INICIALIZAÇÃO VIA STREAMLIT SECRETS E GOOGLE CLOUD
# ------------------------------------------------------------------------------
@st.cache_resource
def setup_gcp():
    scopes = [
        "https://googleapis.com",
        "https://googleapis.com"
    ]
    # Puxa as credenciais do Secrets do Streamlit de forma segura
    creds_dict = dict(st.secrets["gcp_service_account"])
    # Ajuste drástico para evitar o Erro "RefreshError (jwt_grant)" com chaves geradas in TOML:
    if "\\n" in creds_dict["private_key"]:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    drive_service = build('drive', 'v3', credentials=creds)
    return gc, drive_service

try:
    gc, drive_service = setup_gcp()
    DB_SHEET_ID = st.secrets["app_settings"]["DB_SHEET_ID"]
    DADOS_SHEET_ID = st.secrets["app_settings"]["DADOS_SHEET_ID"]
    ODT_TEMPLATE_ID = st.secrets["app_settings"]["ODT_TEMPLATE_ID"]
    ADMIN_PWD = st.secrets["auth"]["admin_password"]
    USER_PWD = st.secrets["auth"]["user_password"]
except Exception as e:
    st.error("🚨 Erro na configuração. Verifique os Streamlit Secrets nas opções avançadas de deploy.")
    st.stop()

if "usuario_perfil" not in st.session_state:
    st.session_state["usuario_perfil"] = None

def validar_senha(senha_input):
    if senha_input == ADMIN_PWD and ADMIN_PWD != "":
        return "Admin", None
    elif senha_input == USER_PWD and USER_PWD != "":
        return "Usuário", None
    else:
        return None, "Credenciais Inválidas."

# Interface de Login
if st.session_state["usuario_perfil"] is None:
    st.title("🔐 Sistema Integrado - PGR SESMT (Cloud)")
    st.info("O sistema agora opera via Conta de Serviço 24h na Nuvem.")
    senha = st.text_input("Insira sua credencial de acesso:", type="password")
    if st.button("Acessar Sistema"):
        perfil, erro = validar_senha(senha)
        if perfil:
            st.session_state["usuario_perfil"] = perfil
            st.rerun()
        else:
            st.error(erro)
    st.stop()

st.sidebar.markdown(f"**Perfil Ativo:** {st.session_state['usuario_perfil']}")
if st.sidebar.button("Encerrar Sessão"):
    st.session_state["usuario_perfil"] = None
    st.rerun()

# ------------------------------------------------------------------------------
# 2. MODELAGEM DO BANCO DE DADOS (Helpers via Google Sheets API)
# ------------------------------------------------------------------------------
ESTRUTURA_TABS = {
    "Secretaria": ["Id_Secretaria", "Nome do Órgão", "Sigla", "Endereço", "CNPJ", "CNAE", "Descrição CNAE", "Grau de Risco", "Grupo de Risco"],
    "Cargo": ["Id_Cargo", "Nome do Cargo"],
    "Riscos_Ambientais": ["Id_Risco", "Nome Risco"],
    "Tipo_Exposicao": ["Id_Exposição", "Nome Exposição"],
    "Probabilidade": ["Id_Probabilidade", "Nome Probabilidade", "Peso Probabilidade", "Descrição"],
    "Efeito": ["Id_Efeito", "Nome Efeito", "Peso Efeito", "Descrição"],
    "Tipo_Medida_Proposta": ["Id_Tipo_Med_Proposta", "Nome Tipo Medida Proposta"],
    "Secretaria_Lotacao": ["Id_Sec_Lotação", "Id_Secretaria", "Lotação", "Descrição Física"],
    "Cargo_Funcao": ["Id_Cargo_Func", "Id_Sec_Lotação", "Id_Cargo", "Função", "Descrição Atividade", "Quantidade M", "Quantidade F", "TOTAL"],
    "Lotacao_Risco": ["Id_Lotação_Risco", "Id_Sec_Lotação", "Id_Cargo_Func", "Id_Risco", "Fator de Risco", "Fonte Geradora", "Avaliação Quantitativa", "Danos à Saúde", "Id_Exposição"],
    "Risco_Medida_Existente": ["Id_Risco_Med_Existente", "Id_Lotação_Risco", "Medida Existente", "EPI EFICAZ", "EPC EFICAZ", "Id_Probabilidade", "Id_Efeito", "Nível", "Classificação"],
    "Risco_Medida_Proposta": ["Id_Risco_Med_Proposta", "Id_Risco_Med_Existente", "Medida Proposta", "Id_Probabilidade", "Id_Efeito", "Nível", "Classificação", "Imediata", "Responsável", "Data Início", "Data Final", "Status", "Porcentagem", "Data Execução"]
}

@st.cache_data(ttl=120)
def load_tabela(nome):
    try:
        sh = gc.open_by_key(DB_SHEET_ID)
        worksheet = sh.worksheet(nome)
        data = worksheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=ESTRUTURA_TABS[nome])
        return pd.DataFrame(data)
    except gspread.exceptions.WorksheetNotFound:
        sh = gc.open_by_key(DB_SHEET_ID)
        ws = sh.add_worksheet(title=nome, rows="1000", cols="20")
        ws.append_row(ESTRUTURA_TABS[nome])
        return pd.DataFrame(columns=ESTRUTURA_TABS[nome])

def save_tabela(nome, df):
    sh = gc.open_by_key(DB_SHEET_ID)
    worksheet = sh.worksheet(nome)
    worksheet.clear()
    worksheet.update([df.columns.values.tolist()] + df.values.astype(str).tolist())
    load_tabela.clear()

def proximo_id(df, col_pk):
    if df.empty: return 1
    df[col_pk] = pd.to_numeric(df[col_pk], errors='coerce').fillna(0)
    return int(df[col_pk].max()) + 1

# Inicializa as tabelas basicas se vazias
def preencher_tabelas_estaticas():
    df_prob = load_tabela("Probabilidade")
    if df_prob.empty:
        save_tabela("Probabilidade", pd.DataFrame([
            [1, "Baixa", 1, "Raramente ocorre"], [2, "Média", 2, "Pode ocorrer"],
            [3, "Alta", 3, "Ocorre com certa frequência"], [4, "Muito Alta", 4, "Ocorrência constante"]
        ], columns=ESTRUTURA_TABS["Probabilidade"]))
    
    df_efeito = load_tabela("Efeito")
    if df_efeito.empty:
        save_tabela("Efeito", pd.DataFrame([
            [1, "Leve", 1, "Pequenos danos"], [2, "Moderado", 2, "Danos medianos"],
            [3, "Grave", 3, "Intervenção médica"], [4, "Gravíssimo", 4, "Risco de morte"]
        ], columns=ESTRUTURA_TABS["Efeito"]))
        
    df_expo = load_tabela("Tipo_Exposicao")
    if df_expo.empty:
        save_tabela("Tipo_Exposicao", pd.DataFrame([
            [1, "Habitual e Permanente"], [2, "Intermitente"], [3, "Eventual"]
        ], columns=ESTRUTURA_TABS["Tipo_Exposicao"]))
        
    df_med_prop = load_tabela("Tipo_Medida_Proposta")
    if df_med_prop.empty:
        save_tabela("Tipo_Medida_Proposta", pd.DataFrame([
            [1, "EPC"], [2, "EPI"], [3, "Administrativa/Organizacional"], [4, "Médica"]
        ], columns=ESTRUTURA_TABS["Tipo_Medida_Proposta"]))

if st.session_state["usuario_perfil"] == "Admin":
    preencher_tabelas_estaticas()

# ------------------------------------------------------------------------------
# 3. SINCRONIZAÇÃO VIA GOOGLE SHEETS E EXCEL MIGRADO
# ------------------------------------------------------------------------------
def sincronizar_tabelas_entidades(is_initial=False):
    try:
        sh_dados = gc.open_by_key(DADOS_SHEET_ID)
        
        df_sec = load_tabela("Secretaria")
        df_cargo = load_tabela("Cargo")
        df_risco = load_tabela("Riscos_Ambientais")

        if is_initial and not df_sec.empty and not df_cargo.empty and len(df_cargo) > 0:
            return True, "Carga inicial já havia sido feita."
            
        tabelas_lidas = []
        for ws in sh_dados.worksheets():
            dados = ws.get_all_records()
            if dados:
                tabelas_lidas.append(pd.DataFrame(dados))
                
        if not tabelas_lidas:
            return False, "Planilha DADOSTABELAS parece estar vazia."

        for df_excel in tabelas_lidas:
            df_excel.replace("", float("NaN"), inplace=True)
            df_excel.ffill(inplace=True)
            
            # --- Secretaria ---
            if "Nome do Órgão" in df_excel.columns:
                orgaos = df_excel["Nome do Órgão"].dropna().unique()
                df_sec = df_sec[df_sec["Nome do Órgão"].isin(orgaos)] 
                
                for index, row in df_excel.drop_duplicates(subset=["Nome do Órgão"]).iterrows():
                    name = row["Nome do Órgão"]
                    sigla = row.get("Sigla", "")
                    end = row.get("Endereço", "")
                    cnpj = row.get("CNPJ", "")
                    cnae = row.get("CNAE", "")
                    desc = row.get("Descrição CNAE", "")
                    grau = row.get("Grau de Risco", "")
                    grupo = row.get("Grupo de Risco", "")
                    
                    if name in df_sec["Nome do Órgão"].values:
                        idx = df_sec[df_sec["Nome do Órgão"] == name].index
                        df_sec.loc[idx, ["Sigla", "Endereço", "CNPJ", "CNAE", "Descrição CNAE", "Grau de Risco", "Grupo de Risco"]] = [sigla, end, cnpj, cnae, desc, grau, grupo]
                    else:
                        df_sec.loc[len(df_sec)] = [proximo_id(df_sec, "Id_Secretaria"), name, sigla, end, cnpj, cnae, desc, grau, grupo]
                save_tabela("Secretaria", df_sec)

            # --- Cargo ---
            col_cargo = "Nome do Cargo" if "Nome do Cargo" in df_excel.columns else ("Cargo" if "Cargo" in df_excel.columns else None)
            if col_cargo:
                cargos = df_excel[col_cargo].dropna().unique()
                df_cargo = df_cargo[df_cargo["Nome do Cargo"].isin(cargos)]
