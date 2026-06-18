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
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    # Puxa as credenciais do Secrets do Streamlit de forma segura
    creds_dict = dict(st.secrets["gcp_service_account"])
    # Ajuste drástico para evitar o Erro "RefreshError (jwt_grant)" com chaves geradas em TOML:
    # Se o Streamlit ler o "\n" literalmente como caracteres de barra e 'n', forçamos a virar quebra de linha.
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
                    nome = row["Nome do Órgão"]
                    sigla = row.get("Sigla", "")
                    end = row.get("Endereço", "")
                    cnpj = row.get("CNPJ", "")
                    cnae = row.get("CNAE", "")
                    desc = row.get("Descrição CNAE", "")
                    grau = row.get("Grau de Risco", "")
                    grupo = row.get("Grupo de Risco", "")
                    
                    if nome in df_sec["Nome do Órgão"].values:
                        idx = df_sec[df_sec["Nome do Órgão"] == nome].index
                        df_sec.loc[idx, ["Sigla", "Endereço", "CNPJ", "CNAE", "Descrição CNAE", "Grau de Risco", "Grupo de Risco"]] = [sigla, end, cnpj, cnae, desc, grau, grupo]
                    else:
                        df_sec.loc[len(df_sec)] = [proximo_id(df_sec, "Id_Secretaria"), nome, sigla, end, cnpj, cnae, desc, grau, grupo]
                save_tabela("Secretaria", df_sec)

            # --- Cargo ---
            col_cargo = "Nome do Cargo" if "Nome do Cargo" in df_excel.columns else ("Cargo" if "Cargo" in df_excel.columns else None)
            if col_cargo:
                cargos = df_excel[col_cargo].dropna().unique()
                df_cargo = df_cargo[df_cargo["Nome do Cargo"].isin(cargos)]
                for cargo in cargos:
                    if cargo not in df_cargo["Nome do Cargo"].values:
                        df_cargo.loc[len(df_cargo)] = [proximo_id(df_cargo, "Id_Cargo"), cargo]
                save_tabela("Cargo", df_cargo)
                
            # --- Risco ---
            if "Nome Risco" in df_excel.columns:
                riscos = df_excel["Nome Risco"].dropna().unique()
                df_risco = df_risco[df_risco["Nome Risco"].isin(riscos)]
                for risco in riscos:
                    if risco not in df_risco["Nome Risco"].values:
                        df_risco.loc[len(df_risco)] = [proximo_id(df_risco, "Id_Risco"), risco]
                save_tabela("Riscos_Ambientais", df_risco)

        return True, "Sincronização via GSheets concluída com sucesso."
    
    except Exception as e:
        return False, f"Erro ao processar DADOSTABELAS Cloud: {str(e)}"

if st.session_state["usuario_perfil"] == "Admin":
    df_validador = load_tabela("Secretaria")
    if df_validador.empty:
        sincronizar_tabelas_entidades(is_initial=True)

tabs_gui = ["Cadastro Interativo", "Consulta", "Relatório Completo"]
abas = st.tabs(tabs_gui)

if st.session_state["usuario_perfil"] == "Admin":
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 Sincronizar Tabelas (Puxar da Planilha Fonte)"):
        suc, msg = sincronizar_tabelas_entidades(is_initial=False)
        if suc:
             st.sidebar.success(msg)
        else:
             st.sidebar.error(msg)

# ==============================================================================
# ABA 1: CADASTRO INTERATIVO
# ==============================================================================
def calcula_matriz(peso_p, peso_e):
    x = int(peso_p) * int(peso_e)
    if x <= 3:
        nivel = "Trivial"
        classificacao = "Irrelevante"
        imediata = "Não prioritário. Ações dentro do princípio de melhoria contínua..."
    elif 4 <= x <= 8:
        nivel = "Moderado"
        classificacao = "Crítica"
        imediata = "Prioridade preferencial. Adotar medidas de controle..."
    elif 9 <= x <= 12:
        nivel = "Alto"
        classificacao = "Não Tolerado"
        imediata = "Prioridade máxima. Adotar medidas imediatas de controle..."
    else: # >= 16
        nivel = "Muito Alto"
        classificacao = "Não Tolerado"
        imediata = "Prioridade máxima. Adotar medidas imediatas de controle..."
    return x, nivel, classificacao, imediata

with abas[0]:
    st.header("📝 Formulário de Mapeamento do PGR (5 Faixas)")
    with st.form("form_pgr"):
        st.markdown("### FAIXA 1: Dados Iniciais e Organogramas")
        df_sec_load = load_tabela("Secretaria")
        df_cargo_load = load_tabela("Cargo")
        
        c1, c2 = st.columns(2)
        op_sec = df_sec_load["Nome do Órgão"].tolist() if not df_sec_load.empty else []
        sec_selecionada = c1.selectbox("Órgão / Secretaria", op_sec)
        lotacao = c2.text_input("Lotação (Setor/Departamento)")
        desc_fisica = st.text_input("Descrição Física do Ambiente")
        
        c3, c4 = st.columns(2)
        op_cargo = df_cargo_load["Nome do Cargo"].tolist() if not df_cargo_load.empty else []
        cargo_selecionado = c3.selectbox("Cargo Referência", op_cargo)
        funcao_text = c4.text_input("Função Praticada")
        
        c5, c6 = st.columns(2)
        qtd_m = c5.number_input("Quantidade Masc. (M)", min_value=0, step=1)
        qtd_f = c6.number_input("Quantidade Fem. (F)", min_value=0, step=1)
        st.info(f"**Total Automático Registrado:** {qtd_m + qtd_f}")
        
        st.markdown("### FAIXA 2: Identificação do Risco")
        desc_atv = st.text_area("Descrição da Atividade")
        df_risco_load = load_tabela("Riscos_Ambientais")
        op_risco = df_risco_load["Nome Risco"].tolist() if not df_risco_load.empty else []
        risco_selecionado = st.selectbox("Risco Ambiental", op_risco)
        
        c7, c8 = st.columns(2)
        fator_risco = c7.text_input("Fator de Risco")
        fonte_geradora = c8.text_input("Fonte Geradora")
        aval_quant = c7.text_input("Avaliação Quantitativa")
        danos = c8.text_input("Danos Possíveis à Saúde")
        
        df_exp = load_tabela("Tipo_Exposicao")
        op_exp = df_exp["Nome Exposição"].tolist() if not df_exp.empty else []
        expo_sel = st.selectbox("Tipo de Exposição", op_exp)
        
        st.markdown("### FAIXA 3: Avaliação de Risco Atual (Com medidas existentes)")
        med_exist = st.text_area("Descreva a Medida Existente")
        c9, c10 = st.columns(2)
        epi_eficaz = c9.selectbox("EPI Eficaz?", ["Sim", "Não"])
        epc_eficaz = c10.selectbox("EPC Eficaz?", ["Sim", "Não"])
        
        df_prob = load_tabela("Probabilidade")
        df_efeito = load_tabela("Efeito")
        
        c11, c12 = st.columns(2)
        op_prb = [f"{row['Peso Probabilidade']} - {row['Nome Probabilidade']}" for _, row in df_prob.iterrows()]
        prob_atual_sel = c11.selectbox("Probabilidade Atual", op_prb)
        peso_p_atual = int(str(prob_atual_sel).split(" - ")[0])
        
        op_ef = [f"{row['Peso Efeito']} - {row['Nome Efeito']}" for _, row in df_efeito.iterrows()]
        efeito_atual_sel = c12.selectbox("Efeito (Severidade) Atual", op_ef)
        peso_e_atual = int(str(efeito_atual_sel).split(" - ")[0])
        
        val_x_atual, niv_atual, class_atual, _ = calcula_matriz(peso_p_atual, peso_e_atual)
        st.warning(f"**Cálculo Automático Matriz Atual:** Valor {val_x_atual} -> Nível '{niv_atual}' / Classificação '{class_atual}'")
        
        st.markdown("### FAIXA 4: Plano de Ação (Medidas Propostas)")
        med_prop = st.text_area("Descreva as Medidas Propostas")
        df_tm_prop = load_tabela("Tipo_Medida_Proposta")
        op_tmp = df_tm_prop["Nome Tipo Medida Proposta"].tolist() if not df_tm_prop.empty else []
        tmp_sel = st.selectbox("Classificação da Medida Proposta", op_tmp)
        
        c13, c14 = st.columns(2)
        prob_prop_sel = c13.selectbox("Probabilidade Esperada (Proposta)", op_prb)
        efeito_prop_sel = c14.selectbox("Efeito Esperado (Proposta)", op_ef)
        peso_p_prop = int(str(prob_prop_sel).split(" - ")[0])
        peso_e_prop = int(str(efeito_prop_sel).split(" - ")[0])
        
        val_x_prop, niv_prop, class_prop, imediata_prop = calcula_matriz(peso_p_prop, peso_e_prop)
        st.success(f"**Matriz Proposta:** Valor {val_x_prop} -> Nível '{niv_prop}' / Classificação '{class_prop}'")
        
        st.markdown("### FAIXA 5: Acompanhamento de Execução")
        st.info(f"👉 **Imediata (Preenchimento Automático):** {imediata_prop}")
        c15, c16 = st.columns(2)
        resp_acao = c15.text_input("Responsável Técnico pela Ação")
        porc_exec = c16.number_input("Concluído (%)", min_value=0, max_value=100)
        c17, c18, c19 = st.columns(3)
        dt_ini = c17.date_input("Dada Inicial")
        dt_fim = c18.date_input("Data Limite (Final)")
        dt_exec = c19.date_input("Data de Execução Tática", value=None)
        status_acao = st.selectbox("Status", ["Não Iniciado", "Em Andamento", "Atrasado", "Concluído"])
        
        # SALVAMENTO EM BANCO
        btn_salvar = st.form_submit_button("✅ Salvar Cadastro Múltiplo no Pandas")
        if btn_salvar:
            try:
                # Recupera IDs estrangeiros
                id_sec = df_sec_load[df_sec_load["Nome do Órgão"] == sec_selecionada].iloc[0]["Id_Secretaria"]
                id_cargo = df_cargo_load[df_cargo_load["Nome do Cargo"] == cargo_selecionado].iloc[0]["Id_Cargo"]
                id_risco = df_risco_load[df_risco_load["Nome Risco"] == risco_selecionado].iloc[0]["Id_Risco"]
                id_expo = df_exp[df_exp["Nome Exposição"] == expo_sel].iloc[0]["Id_Exposição"]
                
                id_prob_at = df_prob[df_prob["Peso Probabilidade"] == peso_p_atual].iloc[0]["Id_Probabilidade"]
                id_ef_at = df_efeito[df_efeito["Peso Efeito"] == peso_e_atual].iloc[0]["Id_Efeito"]
                
                id_prob_pr = df_prob[df_prob["Peso Probabilidade"] == peso_p_prop].iloc[0]["Id_Probabilidade"]
                id_ef_pr = df_efeito[df_efeito["Peso Efeito"] == peso_e_prop].iloc[0]["Id_Efeito"]
                
                # 1. Sec_Lotacao
                df_sl = load_tabela("Secretaria_Lotacao")
                id_sl = proximo_id(df_sl, "Id_Sec_Lotação")
                df_sl.loc[len(df_sl)] = [id_sl, id_sec, lotacao, desc_fisica]
                save_tabela("Secretaria_Lotacao", df_sl)
                
                # 2. Cargo_Funcao
                df_cf = load_tabela("Cargo_Funcao")
                id_cf = proximo_id(df_cf, "Id_Cargo_Func")
                df_cf.loc[len(df_cf)] = [id_cf, id_sl, id_cargo, funcao_text, desc_atv, qtd_m, qtd_f, qtd_m+qtd_f]
                save_tabela("Cargo_Funcao", df_cf)
                
                # 3. Lotacao_Risco
                df_lr = load_tabela("Lotacao_Risco")
                id_lr = proximo_id(df_lr, "Id_Lotação_Risco")
                df_lr.loc[len(df_lr)] = [id_lr, id_sl, id_cf, id_risco, fator_risco, fonte_geradora, aval_quant, danos, id_expo]
                save_tabela("Lotacao_Risco", df_lr)
                
                # 4. Medida Existente
                df_me = load_tabela("Risco_Medida_Existente")
                id_me = proximo_id(df_me, "Id_Risco_Med_Existente")
                df_me.loc[len(df_me)] = [id_me, id_lr, med_exist, epi_eficaz, epc_eficaz, id_prob_at, id_ef_at, val_x_atual, class_atual]
                save_tabela("Risco_Medida_Existente", df_me)
                
                # 5. Medida Proposta
                df_mp = load_tabela("Risco_Medida_Proposta")
                id_mp = proximo_id(df_mp, "Id_Risco_Med_Proposta")
                df_mp.loc[len(df_mp)] = [id_mp, id_me, med_prop, id_prob_pr, id_ef_pr, val_x_prop, class_prop, imediata_prop, resp_acao, dt_ini, dt_fim, status_acao, porc_exec, dt_exec]
                save_tabela("Risco_Medida_Proposta", df_mp)
                
                st.success("Dados encadeados salvos com sucesso no Google Drive.")
            except Exception as ex:
                st.error(f"Erro ao salvar relações: {ex}")

# ==============================================================================
# ABA 2: CONSULTA DE DADOS + FILTROS CUMULATIVOS
# ==============================================================================
with abas[1]:
    st.header("🔍 Painel de Filtros Avançados")
    # Join em memoria para formar view de usuario
    try:
        df1 = load_tabela("Secretaria").rename(columns={"Id_Secretaria": "id_sec"})
        df2 = load_tabela("Secretaria_Lotacao").rename(columns={"Id_Sec_Lotação": "id_sl", "Id_Secretaria": "id_sec"})
        df3 = load_tabela("Cargo_Funcao").rename(columns={"Id_Cargo_Func": "id_cf", "Id_Sec_Lotação": "id_sl", "Id_Cargo": "id_c"})
        df4 = load_tabela("Cargo").rename(columns={"Id_Cargo": "id_c"})
        
        m_sec_sl = pd.merge(df1, df2, on="id_sec")
        m_sl_cf = pd.merge(m_sec_sl, df3, on="id_sl")
        view_flat = pd.merge(m_sl_cf, df4, on="id_c")
        
        c01, c02, c03 = st.columns(3)
        op_f_orgao = ["Todos"] + list(view_flat["Nome do Órgão"].unique())
        f_o = c01.selectbox("Filtro: Órgão (Secretaria)", op_f_orgao)
        
        op_f_carg = ["Todos"] + list(view_flat["Nome do Cargo"].unique())
        f_c = c02.selectbox("Filtro: Cargo", op_f_carg)
        
        op_f_fun = ["Todos"] + list(view_flat["Função"].unique())
        f_f = c03.selectbox("Filtro: Função Executada", op_f_fun)
        
        # Filtro cumulativo dinamico
        filtered_view = view_flat.copy()
        if f_o != "Todos": filtered_view = filtered_view[filtered_view["Nome do Órgão"] == f_o]
        if f_c != "Todos": filtered_view = filtered_view[filtered_view["Nome do Cargo"] == f_c]
        if f_f != "Todos": filtered_view = filtered_view[filtered_view["Função"] == f_f]
        
        st.dataframe(filtered_view, use_container_width=True)
        
        st.info("💡 As atualizações afetam diretamente as seleções mostradas aqui.")
    except Exception:
        st.warning("Banco de dados insuficiente para montagem da visualização. Por favor cadastre registros na Faixa 1 a 5.")

# ==============================================================================
# ABA 3: RELATÓRIO DO PGR E MÓDULO ODT
# ==============================================================================
with abas[2]:
    st.header("🗄️ Relatorização Consolidadada e Motor PDF")
    
    st.subheader("Equipe Técnica do SESMT")
    df_resp = pd.DataFrame([{"nome": "Nome Exemplo", "matricula": "0000", "funcao": "Cargo", "conselho": "CR Exemplo"}])
    st.write("Edite os dados na tabela abaixo para inclusão automatizada na página 2 do Relatório .odt:")
    edited_sesmt = st.data_editor(df_resp, num_rows="dynamic", key="sesmt_edit", use_container_width=True)
    
    responsaveis_assign = st.multiselect("Selecione quem fará a ASSINATURA final no relatório:", edited_sesmt["nome"].tolist())
    
    st.markdown("---")
    try:
        df_sec = load_tabela("Secretaria")
        all_secretarias = df_sec["Nome do Órgão"].tolist() if not df_sec.empty else []
    except:
        all_secretarias = []
        
    sec_selecionada_relatorio = st.selectbox("Selecione o Entidade a emitir o Relatório PGR PDF:", all_secretarias)

    if st.session_state["usuario_perfil"] == "Admin":
        if st.button("📄 GERAR RELATÓRIO PGR OFICIAL (PDF/LibreOffice)"):
            with st.spinner("Processando Integração Automática ODT-PDF via Secretary engine..."):
                try:
                    sec_dados = df_sec[df_sec["Nome do Órgão"] == sec_selecionada_relatorio].iloc[0]
                    id_ss = sec_dados["Id_Secretaria"]
                    df2 = load_tabela("Secretaria_Lotacao")
                    df3 = load_tabela("Cargo_Funcao")
                    lotes = df2[df2["Id_Secretaria"] == id_ss]["Id_Sec_Lotação"].tolist()
                    total_mf_calc = df3[df3["Id_Sec_Lotação"].isin(lotes)]["TOTAL"].sum()
                    
                    hj = datetime.date.today()
                    tag_data = f"{hj.month}/{hj.year} a {hj.month}/{hj.year + 2}"
                    riscos_faixas = [{"col1": "Exemplo", "col2": "Exemplo", "col3": "Ex", "col4": "Ex", "col5": "Ex", "col6": "Ex"}]

                    # Engine Secretary Data
                    engine = Renderer()
                    parametros = {
                        "NOME_ORGAO": str(sec_dados["Nome do Órgão"]),
                        "DATA_EMISSAO": tag_data,
                        "TOTALMF": str(total_mf_calc),
                        "ENDERECO": str(sec_dados["Endereço"]),
                        "CNPJ": str(sec_dados["CNPJ"]),
                        "CNAE": str(sec_dados["CNAE"]),
                        "DESC_CNAE": str(sec_dados["Descrição CNAE"]),
                        "GRAU_RISCO": str(sec_dados["Grau de Risco"]),
                        "GRUPO_RISCO": str(sec_dados["Grupo de Risco"]),
                        "SIGLA": str(sec_dados["Sigla"]),
                        "equipe_tecnica": edited_sesmt.to_dict("records"),
                        "responsaveis": edited_sesmt[edited_sesmt["nome"].isin(responsaveis_assign)].to_dict("records"),
                        "inventarios": riscos_faixas
                    }

                    # Baixar ODT pelo ID da API
                    request = drive_service.files().get_media(fileId=ODT_TEMPLATE_ID)
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while done is False:
                        status, done = downloader.next_chunk()
                    
                    fh.seek(0)
                    template_path = "/tmp/Documento_base.odt"
                    with open(template_path, "wb") as f:
                        f.write(fh.read())
                        
                    resultado_odt = engine.render(template_path, **parametros)
                    
                    odt_out = "/tmp/relatorio_temp.odt"
                    with open(odt_out, 'wb') as fout:
                        fout.write(resultado_odt)
                        
                    comando = ['soffice', '--headless', '--convert-to', 'pdf', '--outdir', '/tmp', odt_out]
                    subprocess.run(comando, check=True)
                    pdf_path = "/tmp/relatorio_temp.pdf"
                    
                    with open(pdf_path, "rb") as pdf_file:
                        pdf_bytes = pdf_file.read()
                        
                    st.download_button("📥 Download Arquivo Validado (PDF)", data=pdf_bytes, file_name=f"PGR_{sec_selecionada_relatorio}.pdf", mime="application/pdf")
                    
                    try:
                        os.remove(template_path)
                        os.remove(odt_out)
                        os.remove(pdf_path)
                    except:
                        pass
                except Exception as g_erro:
                    st.error(f"Engenharia de automação Falhou na esteira: {str(g_erro)}")
    else:
        st.error("⛔ A emissão do relatório oficial em PDF é restrita ao Administrador.")

def main():
    pass

if __name__ == "__main__":
    try:
        main()
    except Exception as default_erro:
        st.error(f"🚨 Ocorreu um Erro Inesperado na Aplicação: {str(default_erro)}")
        st.code(traceback.format_exc(), language="python")
