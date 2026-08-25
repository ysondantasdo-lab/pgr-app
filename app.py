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
import uuid
import markupsafe
import jinja2
from google import genai
from pydantic import BaseModel, Field
from typing import List
import unicodedata  # Certifique-se de importar no topo do arquivo

import traceback
import sys
import zipfile
import html
import re
import shutil
import time 
from docxtpl import DocxTemplate



# Estrutura para a Inteligência Artificial do Gemini entregar os dados organizados
class RiscoEstruturado(BaseModel):
    fator_risco: str = Field(description="Ex: Ruído contínuo, Poeira de madeira")
    fonte_geradora: str = Field(description="Ex: Operação de serra circular")
    danos_saude: str = Field(description="Ex: Perda auditiva, irritação respiratória")
    medida_proposta: str = Field(description="Ação sugerida para mitigar o risco")
    tipo_medida: str = Field(description="Deve ser exatamente um: EPC, EPI, Administrativa/Organizacional ou Médica")

class SugestaoPGR(BaseModel):
    riscos: List[RiscoEstruturado]
    
# ==============================================================================


# Configurações de layout (O seu código original continua aqui para baixo)
st.set_page_config(page_title="PGR Dinâmico em Nuvem", layout="wide")


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
    DOCX_TEMPLATE_ID = st.secrets["app_settings"]["DOCX_TEMPLATE_ID"]
    ADMIN_PWD = st.secrets["auth"]["admin_password"]
    USER_PWD = st.secrets["auth"]["user_password"]
except Exception as e:
    st.error("🚨 Erro na configuração. Verifique os Streamlit Secrets nas opções avançadas de deploy.")
    st.stop()

# =========================================================================
# NOVA FUNÇÃO DE LEITURA PROTEGIDA COM RECONEXÃO AUTOMÁTICA E MENSAGEM VISUAL
# =========================================================================
@st.cache_data
def load_tabela(nome):
    tentativas_maximas = 3
    tempo_espera = 4  # segundos entre as tentativas
    
    for tentativa in range(tentativas_maximas):
        try:
            sh = gc.open_by_key(DB_SHEET_ID)
            worksheet = sh.worksheet(nome)
            linhas = worksheet.get_all_values()
            if not linhas or len(linhas) <=1:
                return pd.DataFrame(colums=ESTRUTURA_TABS[nome])
                
            data = worksheet.get_all_records() (retirado - tentativa de consertar erro)
            #if not data:
            #    return pd.DataFrame(columns=ESTRUTURA_TABS[nome])
            return pd.DataFrame(data)
            
        except gspread.exceptions.WorksheetNotFound:
            # Caso a aba realmente não exista, mantém o comportamento original de criá-la
            sh = gc.open_by_key(DB_SHEET_ID)
            ws = sh.add_worksheet(title=nome, rows="1000", cols="20")
            ws.append_row(ESTRUTURA_TABS[nome])
            return pd.DataFrame(columns=ESTRUTURA_TABS[nome])
            
        except Exception as e:
            # Se for a última tentativa e ainda falhar, mostra o erro técnico
            if tentativa == tentativas_maximas - 1:
                st.error(f"Não foi possível reestabelecer a conexão com a base de dados '{nome}'. Detalhes: {e}")
                st.stop()
            
            # Mensagem visual, clara e acolhedora para o usuário leigo
            aviso_placeholder = st.warning(
                f"⏳ **Aviso de Instabilidade:** Detectamos uma oscilação temporária nos servidores do Google Sheets "
                f"ao carregar dados de '{nome}'. Não se preocupe, estamos resolvendo isso para você! "
                f"Ajustando conexão automática (Tentativa {tentativa + 1} de {tentativas_maximas})..."
            )
            
            # Aguarda o tempo estipulado e limpa a sessão do gspread para forçar uma nova rota de rede
            time.sleep(tempo_espera)
            # Limpa o aviso flutuante da tela antes da póxima tentativa
            aviso_placeholder.empty()
        
            #try:
            #    gc.login() # Tenta reautenticar silenciosamente a sessão ativa
            #except:
            #    pass
                

# FIM DA PROTEÇÃO CONTRA INSTABILIDADE DO GOOGLESHEETS

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

if "fk" not in st.session_state:
    st.session_state["fk"] = 0

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

@st.cache_data
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

# TRECHO MODIFICADO E CORRIGIDO DE FORMA DEFINITIVA (MÍNIMA ALTERAÇÃO):
def save_tabela(nome, df):
    if df.empty or len(df.columns) == 0:
        st.sidebar.warning(f"⚠️ Aba '{nome}' ignorada: Verifique se os nomes das colunas na planilha fonte estão corretos.")
        return  # Para aqui, antes de qualquer chamada à API
        
    sh = gc.open_by_key(DB_SHEET_ID)
    worksheet = sh.worksheet(nome)
    
    
    # Converte nulos para texto vazio e transforma tudo estritamente em strings
    df_limpo = df.fillna("").astype(str)
    
    # Une o cabeçalho com as linhas de dados em uma lista nativa limpa
    cabecalho = df_limpo.columns.tolist()
    linhas_dados = df_limpo.values.tolist()
    matriz_final = [cabecalho] + linhas_dados
    
    
    # CORREÇÃO DEFINITIVA: Passa a coordenada inicial 'A1' exigida pelo gspread moderno
    worksheet.update(range_name='A1', values=matriz_final)
    st.cache_data.clear()

    
    
    # 🌟 ADICIONADO APENAS ESTE BLOCO: Se faltar colunas ou dados, avisa o usuário e para aqui
    if df.empty or len(df.columns) == 0:
        st.sidebar.warning(f"⚠️ Aba '{nome}' ignorada: Verifique se os nomes das colunas na planilha fonte estão corretos.")
        return # Para a função aqui para não enviar lixo e evitar o Erro 400
    
    # Todo o seu código atual com as muitas linhas continua intacto aqui para baixo:
    # Garante a eliminação completa de NaNs, nulos e converte tudo estritamente para string de texto comum
    df_limpo = df.copy()
    df_limpo = df_limpo.fillna("").astype(str)
    
    # Monta a estrutura da planilha juntando cabeçalho + linhas em listas puras de Python
    cabecalho = df_limpo.columns.tolist()
    linhas_dados = df_limpo.values.tolist()
    matriz_final = [cabecalho] + linhas_dados
    
    # CORREÇÃO: Força a gravação a partir da célula A1 usando a sintaxe atualizada aceita pela API
    worksheet.update(range_name='A1', values=matriz_final)


    

def proximo_id(df, col_pk):
    if df.empty: 
        return 1
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
        
        df_sec = load_tabela("Secretarias") 
        df_cargo = load_tabela("Cargo") 
        df_risco = load_tabela("Riscos_Ambientais") 
        
        if is_initial and not df_sec.empty and not df_cargo.empty and len(df_cargo) > 0:
            return True, "Carga inicial já havia sido feita." 
        
        # Criamos o dicionário com chaves {} para identificar o nome de cada aba da planilha fonte
        tabelas_lidas = {} 
        for ws in sh_dados.worksheets(): 
            dados = ws.get_all_records() 
            if dados: 
                tabelas_lidas[ws.title] = pd.DataFrame(dados) 
        
        if not tabelas_lidas: 
            return False, "Planilha DADOSTABELAS parece estar vazia." 
            
        # Processa cada tabela lida do GSheets do Admin usando os nomes exatos fornecidos
        erros_por_aba = []
        for nome_aba, df_excel in tabelas_lidas.items():
            try:
                df_excel.replace("", float("NaN"), inplace=True) 
                df_excel.ffill(inplace=True)
                st.sidebar.write(f"🔄 Processando aba: {nome_aba}")
            
                # --- 1. Sincronizar Probabilidade ---
                if nome_aba == "Probabilidade":
                    df_prob_novo = df_excel[[c for c in ESTRUTURA_TABS["Probabilidade"] if c in df_excel.columns]].copy()
                    save_tabela("Probabilidade", df_prob_novo)
                    continue

                # --- 2. Sincronizar Efeito (Severidade) ---
                if nome_aba == "Efeito":
                    df_efeito_novo = df_excel[[c for c in ESTRUTURA_TABS["Efeito"] if c in df_excel.columns]].copy()
                    save_tabela("Efeito", df_efeito_novo)
                    continue

            
                # --- 3. Sincronizar Tipo de Medida Proposta (Classificação) ---
                if nome_aba == "Tipo_Medida_Proposta":
                    df_tmp_novo = df_excel[[c for c in ESTRUTURA_TABS["Tipo_Medida_Proposta"] if c in df_excel.columns]].copy()
                    save_tabela("Tipo_Medida_Proposta", df_tmp_novo)
                    continue

                # --- 4. Sincronizar Tipo de Exposição ---
                if nome_aba in ("Tipo_Exposicao", "Tipo_Exposição"):
                    df_exp_novo = df_excel[[c for c in ESTRUTURA_TABS["Tipo_Exposicao"] if c in df_excel.columns]].copy()
                    save_tabela("Tipo_Exposicao", df_exp_novo)
                    continue

            
           
                # --- 5. Sincronizar Secretaria --- 
                if nome_aba == "Secretarias": 
                    orgaos = df_excel["Nome do Órgão"].dropna().unique() 
                    df_sec = df_sec[df_sec["Nome do Órgão"].isin(orgaos)] 
                    for index, row in df_excel.drop_duplicates(subset=["Nome do Órgão"]).iterrows(): 
                        nome = row["Nome do Órgão"] 
                        if nome in df_sec["Nome do Órgão"].values: 
                            idx = df_sec[df_sec["Nome do Órgão"] == nome].index 
                            df_sec.loc[idx, ["Sigla", "Endereço", "CNPJ", "CNAE", "Descrição CNAE", "Grau de Risco", "Grupo de Risco"]] = [
                                row.get("Sigla", ""), row.get("Endereço", ""), row.get("CNPJ", ""), row.get("CNAE", ""), row.get("Descrição CNAE", ""), row.get("Grau de Risco", ""), row.get("Grupo de Risco", "")
                            ] 
                        else: 
                            df_sec.loc[len(df_sec)] = [proximo_id(df_sec, "Id_Secretaria"), nome, row.get("Sigla", ""), row.get("Endereço", ""), row.get("CNPJ", ""), row.get("CNAE", ""), row.get("Descrição CNAE", ""), row.get("Grau de Risco", ""), row.get("Grupo de Risco", "")] 
                    save_tabela("Secretaria", df_sec) 
                    continue 
            
                # --- 6. Sincronizar Cargo --- 
                if nome_aba == "Cargo": 
                    col_cargo = "Nome do Cargo" if "Nome do Cargo" in df_excel.columns else ("Cargo" if "Cargo" in df_excel.columns else None) 
                    if col_cargo: 
                        cargos = df_excel[col_cargo].dropna().unique() 
                        df_cargo = df_cargo[df_cargo["Nome do Cargo"].isin(cargos)] 
                        for cargo in cargos: 
                            if cargo not in df_cargo["Nome do Cargo"].values: 
                                df_cargo.loc[len(df_cargo)] = [proximo_id(df_cargo, "Id_Cargo"), cargo] 
                        save_tabela("Cargo", df_cargo) 
                        continue 
            
                # --- 7. Sincronizar Riscos Ambientais --- 
                if nome_aba == "Riscos_Ambientais": 
                    riscos = df_excel["Nome Risco"].dropna().unique() 
                    df_risco = df_risco[df_risco["Nome Risco"].isin(riscos)] 
                    for risco in riscos: 
                        if risco not in df_risco["Nome Risco"].values: 
                            df_risco.loc[len(df_risco)] = [proximo_id(df_risco, "Id_Risco"), risco] 
                    save_tabela("Riscos_Ambientais", df_risco) 
                    continue 
                      
            except Exception as e_aba:
                # Captura o erro completo (inclusive corpo de resposta da API, se houver) por aba específica
                detalhe = str(e_aba)
                resp = getattr(e_aba, "response", None)
            if resp is not None:
                try:
                    detalhe += f" | Resposta da API: {resp.text}"
                except Exception:
                    pass
            erros_por_aba.append(f"{nome_aba}: {detalhe}")
            st.sidebar.error(f"❌ Falha ao processar aba '{nome_aba}': {detalhe}")

        if erros_por_aba:
            return False, "Falhas em uma ou mais abas: " + " || ".join(erros_por_aba)

        return True, "Sincronização de todas as entidades concluída com sucesso."
    except Exception as e:
        erro_detalhado = traceback.format_exc()
        return False, f"Erro ao processar DADOSTABELAS Cloud: {str(e)}"

if st.session_state["usuario_perfil"] == "Admin":
    df_validador = load_tabela("Secretaria")
    if df_validador.empty:
        sincronizar_tabelas_entidades(is_initial=True)

# Inicializa a aba ativa padrão na memória se o programa acabou de abrir
if "aba_ativa_nome" not in st.session_state:
    st.session_state["aba_ativa_nome"] = "Cadastro Interativo"

# Aplica a troca de navegação ANTES do widget radio ser instanciado neste run
if st.session_state.get("forcar_nav_cadastro", False):
    st.session_state["radio_nav_abas"] = "Cadastro Interativo"
    st.session_state["forcar_nav_cadastro"] = False

tabs_gui = ["Cadastro Interativo", "Consulta", "Relatório Completo"]

aba_selecionada = st.radio(
    "Navegação",
    tabs_gui,
    index=tabs_gui.index(st.session_state["aba_ativa_nome"]),
    horizontal=True,
    label_visibility="collapsed",
    key="radio_nav_abas"
)
st.session_state["aba_ativa_nome"] = aba_selecionada
st.markdown("---")

if st.session_state["usuario_perfil"] == "Admin":
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 Sincronizar Tabelas (Puxar da Planilha Fonte)"):
        suc, msg = sincronizar_tabelas_entidades(is_initial=False)
        if suc:
             st.sidebar.success("tabela foi carregada com sucesso")
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
        imediata = "Irrelevante - Não prioritário. Ações dentro do princípio de melhoria contínua. Pode ser necessária avaliação quantitativa do Setor/GHE para confirmação da categoria, a critério do profissional de Higiene Ocupacional."
    elif 4 <= x <= 8:
        nivel = "Moderado"
        classificacao = "Crítica"
        imediata = "De Atenção - Prioridade básica. Iniciar processo de avaliação quantitativa do Setor/GHE para confirmação da categoria e monitoramento periódico."
    elif 9 <= x <= 12:
        nivel = "Alto"
        classificacao = "Não Tolerado"
        imediata = "Crítica - Prioridade preferencial. Adotar medidas de controle para redução da exposição e iniciar processo de avaliação quantitativa do Setor/GHE."
    else: # >= 16
        nivel = "Muito Alto"
        classificacao = "Não Tolerado"
        imediata = "Não tolerável - Prioridade máxima. Adotar medidas imediatas de controle. Quando não, a continuidade da operação só poderá ocorrer com ciência e aprovação do gerente geral da unidade ou instalação. Iniciar processo de avaliação quantitativa do Setor/GHE para verificação do rebaixamento da categoria de risco."
    return x, nivel, classificacao, imediata

if aba_selecionada == "Cadastro Interativo":
    # --- ATIVAÇÃO DA MENSAGEM FLUTUANTE E RESET DE EDICÃO ---
    if st.session_state.get("cadastro_salvo_sucesso", False):
        st.toast("✅ Cadastro Geral do PGR salvo com sucesso na Nuvem!", icon="💾")
        st.session_state["cadastro_salvo_sucesso"] = False
        if "id_funcao_em_alteracao_db" in st.session_state:
            st.session_state["id_funcao_em_alteracao_db"] = None

    #... conteúdo que estava em abas[0]
    st.header(" Formulário de Mapeamento do PGR (5 Faixas)")



    
    # ... conteúdo que estava em abas[0] 
    st.header("📝 Formulário de Mapeamento do PGR (5 Faixas)") 
 
    if "lista_riscos" not in st.session_state: 
        st.session_state["lista_riscos"] = []
        st.session_state["id_funcao_em_alteracao_db"] = None
        st.session_state["indice_em_edicao"] = None
        st.success("Dados encadeados salvos com sucesso no Google Drive.")
        
    if "N" not in st.session_state:
        st.session_state["N"] = 0
    if "indice_em_edicao" not in st.session_state: 
        st.session_state["indice_em_edicao"] = None 
   
    #--- ENGENHARIA DE PREENCHIMENTO AUTOMÁTICO DO CABEÇALHO ---
    id_alvo_db = st.session_state.get("id_funcao_em_alteracao_db", None)

    # Se for um cadastro novo (ou pós-salvamento), preserva a estrutura e limpa a função
    if id_alvo_db is None:
        padrao_sec_idx = st.session_state.get("ultimo_sec_idx", 0)
        padrao_cargo_idx = 0
        padrao_lotacao = st.session_state.get("ultimo_setor_digitado", "")
        padrao_desc_fisica = st.session_state.get("ultima_desc_fisica_digitada", "")
        padrao_funcao_text = ""
        padrao_qtd_m = 0
        padrao_qtd_f = 0
        padrao_desc_atv = ""
    else:
        padrao_sec_idx = 0
        padrao_cargo_idx = 0
        padrao_lotacao = ""
        padrao_desc_fisica = ""
        padrao_funcao_text = ""
        padrao_qtd_m = 0
        padrao_qtd_f = 0
        padrao_desc_atv = ""


    df_sec_load = load_tabela("Secretaria") 
    df_cargo_load = load_tabela("Cargo") 
    op_sec = df_sec_load["Nome do Órgão"].tolist() if not df_sec_load.empty else [] 
    op_cargo = df_cargo_load["Nome do Cargo"].tolist() if not df_cargo_load.empty else [] 

    # Se viermos de uma edição da Consulta, extraímos os dados históricos fixos do banco
    if id_alvo_db is not None:
        df_cf_atual = load_tabela("Cargo_Funcao")
        df_sl_atual = load_tabela("Secretaria_Lotacao")
        df_sec_atual = load_tabela("Secretaria")
        df_cargo_atual = load_tabela("Cargo")

        linha_cf = df_cf_atual[df_cf_atual["Id_Cargo_Func"] == id_alvo_db]
        if not linha_cf.empty:
            linha_cf = linha_cf.iloc[0]
            linha_sl = df_sl_atual[df_sl_atual["Id_Sec_Lotação"] == linha_cf["Id_Sec_Lotação"]].iloc[0]
            linha_sec = df_sec_atual[df_sec_atual["Id_Secretaria"] == linha_sl["Id_Secretaria"]].iloc[0]
            linha_cargo = df_cargo_atual[df_cargo_atual["Id_Cargo"] == linha_cf["Id_Cargo"]].iloc[0]

            nome_sec_banco = str(linha_sec.get("Nome do Órgão", ""))
            nome_cargo_banco = str(linha_cargo.get("Nome do Cargo", ""))
            if nome_sec_banco in op_sec: padrao_sec_idx = op_sec.index(nome_sec_banco)
            if nome_cargo_banco in op_cargo: padrao_cargo_idx = op_cargo.index(nome_cargo_banco)

            padrao_lotacao = str(linha_sl.get("Lotação", ""))
            padrao_desc_fisica = str(linha_sl.get("Descrição Física", ""))
            padrao_funcao_text = str(linha_cf.get("Função", ""))
            padrao_qtd_m = int(linha_cf.get("Quantidade M", 0)) if pd.notna(linha_cf.get("Quantidade M")) else 0
            padrao_qtd_f = int(linha_cf.get("Quantidade F", 0)) if pd.notna(linha_cf.get("Quantidade F")) else 0
            padrao_desc_atv = str(linha_cf.get("Descrição Atividade", ""))

    # --- EXIBIÇÃO RENDERIZADA DOS CAMPOS DA FAIXA 1 ---
    st.markdown("### FAIXA 1: Dados Iniciais do Órgão/Secretaria") 
    
    c1, c2 = st.columns(2) 
    # Vinculados dinamicamente aos índices padrões calculados
    sec_selecionada = c1.selectbox("Órgão / Secretaria", op_sec, index=padrao_sec_idx)
    lotacao = c2.text_input("Lotação (Setor/Departamento)", value=padrao_lotacao)
    desc_fisica = st.text_input("Descrição Física do Ambiente", value=padrao_desc_fisica)

    # --- SALVA O ESTADO ATUAL PARA REUTILIZAÇÃO ---
    if op_sec and sec_selecionada in op_sec:
        st.session_state["ultimo_sec_idx"] = op_sec.index(sec_selecionada)
    st.session_state["ultimo_setor_digitado"] = lotacao
    st.session_state["ultima_desc_fisica_digitada"] = desc_fisica
 
 
    c3, c4 = st.columns(2) 
    cargo_selecionado = c3.selectbox("Cargo", op_cargo, index=padrao_cargo_idx) 
    funcao_text = c4.text_input("Função Exercida", value=padrao_funcao_text) 
 
    c5, c6 = st.columns(2) 
    qtd_m = c5.number_input("Quantidade Masc. (M)", min_value=0, value=padrao_qtd_m, step=1) 
    qtd_f = c6.number_input("Quantidade Fem. (F)", min_value=0, value=padrao_qtd_f, step=1) 
    st.info(f"**Total de Pessoas:** {qtd_m + qtd_f}") 
    desc_atv = st.text_area("Descrição Geral da Atividade (Função)", value=padrao_desc_atv)

    if "ia_sugestoes" not in st.session_state:
        st.session_state["ia_sugestoes"] = []

    if st.button("🪄 Sugerir Riscos com IA (Gemini)", use_container_width=True):
        if not desc_atv or not cargo_selecionado or not funcao_text:
            st.error("Por favor, preencha o Cargo, a Função Exercida e a Descrição da Atividade para a IA analisar.")
        else:
            with st.spinner("O Gemini está analisando o ambiente de trabalho..."):
                tentativas_maximas = 3
                tempo_espera = 3  # segundos entre as tentativas
                sucesso_ia = False

                for tentativa in range(tentativas_maximas):               
                    try:
                        client = genai.Client(api_key=st.secrets["auth"]["GEMINI_API_KEY"])
                        prompt = f"Atue como um Engenheiro de Segurança do Trabalho Sênior. Analise o cargo '{cargo_selecionado}' exercendo a função de '{funcao_text}' que realiza a atividade: '{desc_atv}'. Gere uma lista de riscos ambientais previsíveis seguindo as diretrizes da NR-01."
                        response = client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=prompt,
                            config={
                                "response_mime_type": "application/json",
                                "response_schema": SugestaoPGR
                            }
                        )
                        # Se chegou aqui com dados válidos, salva e encerra o loop de tentativas
                        st.session_state["ia_sugestoes"] = response.parsed.riscos
                        st.success("Sugestões geradas com sucesso!")
                        sucesso_ia = True
                        break
                    except Exception as ai_err:
                        if tentativa == tentativas_maximas - 1:
                            st.error(
                                "⚠️ **Instabilidade no Gemini. A reconexão será automática.** Se o erro persistir, melhore o cargo, a função ou a descrição das atividades."
                            )
                            break
                    
                        aviso_ia_placeholder = st.warning(
                            f"⏳ **Aviso de Oscilação:** O servidor de Inteligência Artificial demorou para responder. "
                            f"Estamos ajustando o canal de comunicação para tentar novamente de forma automática... "
                            f"(Tentativa {tentativa + 1} de {tentativas_maximas})"
                        )
                    
                        time.sleep(tempo_espera)
                        aviso_ia_placeholder.empty()
     
    if st.session_state["ia_sugestoes"]:
        for idx_ia, item_ia in enumerate(st.session_state["ia_sugestoes"]):
            with st.expander(f"💡 Sugestão {idx_ia + 1}: {item_ia.fator_risco}"):
                st.write(f"**Fonte:** {item_ia.fonte_geradora} | **Danos:** {item_ia.danos_saude}")
                st.write(f"**Proposta:** {item_ia.medida_proposta}")
                
                # TRECHO MODIFICADO:
                if st.button("Usar estes dados no formulário abaixo", key=f"btn_ia_{idx_ia}"):
                    fk_atual = st.session_state.get("fk", 0)
    
                    st.session_state[f"risco_{fk_atual}"] = item_ia.fator_risco # <--- ADICIONADO AQUI
                    st.session_state[f"fator_{fk_atual}"] = item_ia.fator_risco
                    st.session_state[f"fonte_{fk_atual}"] = item_ia.fonte_geradora
                    st.session_state[f"danos_{fk_atual}"] = item_ia.danos_saude
                    st.session_state[f"mp_{fk_atual}"] = item_ia.medida_proposta
    
                    st.rerun()

      

    # ------------------ RISCOS JÁ ADICIONADOS (COM EDIÇÃO E EXCLUSÃO) ------------------
    if len(st.session_state["lista_riscos"]) > 0:
        st.markdown("### 📋 Riscos Adicionados para Esta Função")
        
        # Cabeçalho visual das colunas
        cab1, cab2, cab3, cab4 = st.columns([2, 4, 3, 2])
        cab1.markdown("**Risco**")
        cab2.markdown("**Fator / Fonte**")
        cab3.markdown("**Medida Proposta**")
        cab4.markdown("**Ações**")
        st.markdown("---")

        # Varre a lista de riscos invertida para mostrar o mais recente primeiro (opcional)
        # Usamos o enumerate para saber o índice exato de cada risco na lista do session_state
        for idx, r in enumerate(st.session_state["lista_riscos"]):
            # Se o risco foi marcado como excluído em uma lógica futura, pulamos (opcional)
            col_r1, col_r2, col_r3, col_r4 = st.columns([2, 4, 3, 2])
            
            col_r1.write(r.get("risco", "N/A"))
            col_r2.write(f"**Fator:** {r.get('fator', '')}\n\n**Fonte:** {r.get('fonte', '')}")
            col_r3.write(r.get("medida_proposta", "N/A"))
            
            # Botões de ação para este risco específico
            btn_col1, btn_col2 = col_r4.columns(2)
            
            # 1. BOTÃO EDITAR
            if btn_col1.button("✏️", key=f"edit_risk_{idx}", help="Editar este risco"):
                n_atual = st.session_state.get("fk", 0)
                st.session_state[f"risco_{n_atual}"] = r.get("risco", "")
                st.session_state[f"fator_{n_atual}"] = r.get("fator", "")
                st.session_state[f"fonte_{n_atual}"] = r.get("fonte", "")
                st.session_state[f"aval_{n_atual}"] = r.get("aval", "")
                st.session_state[f"danos_{n_atual}"] = r.get("danos", "")
                st.session_state[f"expo_{n_atual}"] = r.get("expo", "")
                st.session_state[f"me_{n_atual}"] = r.get("medida_existente", "")
                st.session_state[f"epi_{n_atual}"] = r.get("epi", "")
                st.session_state[f"epc_{n_atual}"] = r.get("epc", "")
                st.session_state[f"pa_{n_atual}"] = r.get("prob_atual", "")
                st.session_state[f"ea_{n_atual}"] = r.get("efeito_atual", "")
                st.session_state[f"mp_{n_atual}"] = r.get("medida_proposta", "")
                st.session_state[f"tmp_{n_atual}"] = r.get("tmp_sel", "")
                st.session_state[f"pp_{n_atual}"] = r.get("prob_prop", "")
                st.session_state[f"ep_{n_atual}"] = r.get("efeito_prop", "")
                st.session_state[f"resp_{n_atual}"] = r.get("resp_acao", "")
                st.session_state[f"porc_{n_atual}"] = r.get("porc_exec", 0)
                st.session_state[f"status_{n_atual}"] = r.get("status_acao", "Não Iniciado")

                from datetime import datetime as dt
                def _parse_data(s):
                    try:
                        return dt.strptime(s, "%d/%m/%Y").date()
                    except Exception:
                        return None
                st.session_state[f"dti_{n_atual}"] = _parse_data(r.get("dt_ini", ""))
                st.session_state[f"dtf_{n_atual}"] = _parse_data(r.get("dt_fim", ""))
                st.session_state[f"dte_{n_atual}"] = _parse_data(r.get("dt_exec", ""))

                   
                # Guarda no session_state qual índice estamos editando para sabermos se vamos atualizar ou criar um novo
                st.session_state["indice_em_edicao"] = idx
                st.success("Dados carregados no formulário abaixo para alteração!")
                st.rerun()
                
            # 2. BOTÃO EXCLUIR
            if btn_col2.button("🗑️", key=f"del_risk_{idx}", help="Excluir este risco"):
                # Remove o risco da lista usando o índice dele
                st.session_state["lista_riscos"].pop(idx)
                st.warning("Risco removido da lista temporária.")
                st.rerun()
        st.markdown("---")
        

    # ------------------ ENTRADA DO NOVO RISCO ------------------
    st.markdown("---")
    st.markdown("#### ADICIONAR NOVO RISCO À FUNÇÃO")
    fk = st.session_state.get("fk",0)

    # TRECHO MODIFICADO:
    st.markdown("##### FAIXA 2: Identificação do Risco")
    # Buscamos um valor pré-existente (da IA ou edição), se não houver inicia vazio ""
    valor_padrao_risco = st.session_state.get(f"risco_{fk}", "")
    risco_selecionado = st.text_input("Risco Ambiental (Tipo de Risco)", value=valor_padrao_risco, key=f"risco_{fk}")

    
    c7, c8 = st.columns(2)
    fator_risco = c7.text_input("Fator de Risco", key=f"fator_{fk}")
    fonte_geradora = c8.text_input("Fonte Geradora", key=f"fonte_{fk}")
    aval_quant = c7.text_input("Avaliação Quantitativa", key=f"aval_{fk}")
    danos = c8.text_input("Danos Possíveis à Saúde", key=f"danos_{fk}")
    
    df_exp = load_tabela("Tipo_Exposicao")
    op_exp = df_exp["Nome Exposição"].tolist() if not df_exp.empty else []
    expo_sel = st.selectbox("Tipo de Exposição", op_exp, key=f"expo_{fk}")
    
    st.markdown("##### FAIXA 3: Avaliação de Risco Atual (Com medidas existentes)")
    med_exist = st.text_area("Descreva a Medida Existente", key=f"me_{fk}")
    c9, c10 = st.columns(2)
    epi_eficaz = c9.selectbox("EPI Eficaz?", ["Sim", "Não"], key=f"epi_{fk}")
    epc_eficaz = c10.selectbox("EPC Eficaz?", ["Sim", "Não"], key=f"epc_{fk}")
    
    df_prob = load_tabela("Probabilidade")
    df_efeito = load_tabela("Efeito")
    
    c11, c12 = st.columns(2)
    op_prb = [f"{row['Peso Probabilidade']} - {row['Nome Probabilidade']}" for _, row in df_prob.iterrows()]
    prob_atual_sel = c11.selectbox("Probabilidade Atual", op_prb, key=f"pa_{fk}")
    peso_p_atual = int(str(prob_atual_sel).split(" - ")[0])
    
    op_ef = [f"{row['Peso Efeito']} - {row['Nome Efeito']}" for _, row in df_efeito.iterrows()]
    efeito_atual_sel = c12.selectbox("Efeito (Severidade) Atual", op_ef, key=f"ea_{fk}")
    peso_e_atual = int(str(efeito_atual_sel).split(" - ")[0])
    
    val_x_atual, niv_atual, class_atual, imediata_atual = calcula_matriz(peso_p_atual, peso_e_atual)
    st.warning(f"**Cálculo Automático Matriz Multiplicação Probabilidade x Efeito:** Valor {val_x_atual} -> Nível Calculado '{niv_atual}' / Classificação Calculada '{class_atual}'")
    
    st.markdown("##### FAIXA 4: Plano de Ação (Medidas Propostas)")
    med_prop = st.text_area("Descreva as Medidas Propostas", key=f"mp_{fk}")
    df_tm_prop = load_tabela("Tipo_Medida_Proposta")
    op_tmp = df_tm_prop["Nome Tipo Medida Proposta"].tolist() if not df_tm_prop.empty else []
    tmp_sel = st.selectbox("Tipo de Medida Proposta", op_tmp, key=f"tmp_{fk}")
    
    c13, c14 = st.columns(2)
    prob_prop_sel = c13.selectbox("Probabilidade Esperada (Após Medida Proposta)", op_prb, key=f"pp_{fk}")
    efeito_prop_sel = c14.selectbox("Efeito Esperado (Após Medida Proposta)", op_ef, key=f"ep_{fk}")
    peso_p_prop = int(str(prob_prop_sel).split(" - ")[0])
    peso_e_prop = int(str(efeito_prop_sel).split(" - ")[0])
    
    val_x_prop, niv_prop, class_prop, imediata_prop = calcula_matriz(peso_p_prop, peso_e_prop)
    st.success(f"**Cálculo Automático Matriz Multiplicação Probabilidade x Efeito Após Medida Proposta:** Valor {val_x_prop} -> Nível Calculado '{niv_prop}' / Classificação Calculada '{class_prop}'")
    
    st.markdown("##### FAIXA 5: Acompanhamento de Execução")
    
    # Substituição cirúrgica: sai st.info e entra st.text_area expansível
   
    # Este comando força o navegador a desenhar o campo desativado com letras escuras e nítidas
    st.html("""
        <style>
        textarea:disabled,       
        .stTextArea label { 
            color: black !important; 
            -webkit-text-fill-color: black !important; 
            cursor: default !important; 
        }
    </style>
    """)

    st.text_area(
        label="👉 Imediata (Preenchimento Automático):",
        value=imediata_atual,
        height=100,          # Altura inicial confortável
        disabled=True,      # CORREÇÃO: Bloqueia a digitação mas mantém o visual 100% nítido
        key=f"imediata_show_{fk}_{val_x_atual}"   # <-- key agora inclui val_x_prop
    )
    
    c15, c16 = st.columns(2)
    resp_acao = c15.text_input("Responsável Técnico pela Ação", key=f"resp_{fk}")
    porc_exec = c16.number_input("Concluído (%)", min_value=0, max_value=100, value=0, step=5, key=f"porc_{fk}")
    c17, c18, c19 = st.columns(3)
    dt_ini = c17.date_input("Data Inicial", value=None, format="DD/MM/YYYY", key=f"dti_{fk}")
    dt_fim = c18.date_input("Data Limite (Final)", value=None, format="DD/MM/YYYY", key=f"dtf_{fk}")
    dt_exec = c19.date_input("Data de Execução", value=None, format="DD/MM/YYYY", key=f"dte_{fk}")

    # ADICIONE APENAS ESTAS DUAS LINHAS AQUI E APAGUE QUALQUER REPETIÇÃO ABAIXO DELAS:
    status_opcoes = ["Não Iniciado", "Em Andamento", "Concluído", "Atrasado"]
    status_sel = st.selectbox("Status", status_opcoes, key=f"status_{fk}")



    
    # BOTÃO PARA ADICIONAR RISCO
    # Detecta se há uma edição ativa para mudar o nome do botão dinamicamente
    idx_edicao = st.session_state.get("indice_em_edicao", None)
    texto_botao = "💾 Atualizar Risco Editado" if idx_edicao is not None else "➕ Adicionar Este Risco"
    
    
    
    if st.button(texto_botao, use_container_width=True):
        novo_risco = {
            "risco": risco_selecionado,
            "fator": fator_risco,
            "fonte": fonte_geradora,
            "aval": aval_quant,
            "danos": danos,
            "expo": expo_sel,
            "medida_existente": med_exist,
            "epi": epi_eficaz,
            "epc": epc_eficaz,
            "prob_atual": prob_atual_sel,
            "efeito_atual": efeito_atual_sel,
            "val_x_atual": val_x_atual,
            "class_atual": class_atual,
            "medida_proposta": med_prop,
            "tmp_sel": tmp_sel,
            "prob_prop": prob_prop_sel,
            "efeito_prop": efeito_prop_sel,
            "val_x_prop": val_x_prop,
            "class_prop": class_prop,
            "imediata": imediata_prop,
            "resp_acao": resp_acao,
            "porc_exec": porc_exec,
            "dt_ini": dt_ini.strftime("%d/%m/%Y") if dt_ini else "",
            "dt_fim": dt_fim.strftime("%d/%m/%Y") if dt_fim else "",
            "dt_exec": dt_exec.strftime("%d/%m/%Y") if dt_exec else "",
            "status_acao": status_sel
        }
          
        
                        
        # TRECHO MODIFICADO COM ALERTA DE SALVAMENTO OBRIGATÓRIO:
        if idx_edicao is not None:
            # 📝 MODO EDIÇÃO: Substitui na mesma posição da lista antiga
            st.session_state["lista_riscos"][idx_edicao] = novo_risco
            st.session_state["indice_em_edicao"] = None  # Reseta o estado para livre
            
            # Mensagem combinando o sucesso com a instrução do próximo passo obrigatório
            st.success("Risco alterado com sucesso na listagem temporária!")
            st.warning("⚠️ **Atenção:** Suas alterações ainda NÃO foram salvas definitivamente. Para gravar no banco de dados, você DEVE clicar no botão verde abaixo: 'Salvar Cadastro Geral na Nuvem (Função + Riscos)' antes de sair.")
        else:
            # ➕ MODO NOVO: Insere no final da lista normalmente
            st.session_state["lista_riscos"].append(novo_risco)
            st.session_state["fk"] += 1
            
            st.success("Risco inserido com sucesso na listagem temporária!")
            st.info("💡 **Próximo Passo Obrigatório:** Para gravar de forma definitiva este e os demais riscos no banco de dados, lembre-se de clicar no botão 'Salvar Cadastro Geral na Nuvem (Função + Riscos)' ao final da página.")
        
        # REMOVIDO st.rerun() daqui para a mensagem fixar na tela e o usuário conseguir ler o aviso antes do refresh!


    st.markdown("---")

    # SALVAMENTO EM BANCO
    if st.button("💾 Salvar Cadastro Geral na Nuvem (Função + Riscos)"):
        campos_faltantes = []
        if not sec_selecionada:
            campos_faltantes.append("Nome do Órgão")
        if not cargo_selecionado:
            campos_faltantes.append("Nome do Cargo")
        if not funcao_text or not funcao_text.strip():
            campos_faltantes.append("Função")

        if campos_faltantes:
            st.error(f"⚠️ Não é possível salvar. Preencha o(s) campo(s) obrigatório(s): {', '.join(campos_faltantes)}")
        elif len(st.session_state["lista_riscos"]) == 0:
            st.error("Adicione pelo menos um risco antes de salvar!")
        else:
            try:
                id_sec = df_sec_load[df_sec_load["Nome do Órgão"] == sec_selecionada].iloc[0]["Id_Secretaria"]
                id_cargo = df_cargo_load[df_cargo_load["Nome do Cargo"] == cargo_selecionado].iloc[0]["Id_Cargo"]

                # --- FASE 1: SNAPSHOT DO ESTADO ORIGINAL (antes de qualquer alteração em memória) ---
                df_sl = load_tabela("Secretaria_Lotacao")
                df_sl_original = df_sl.copy()
                id_sl = proximo_id(df_sl, "Id_Sec_Lotação")

                df_cf = load_tabela("Cargo_Funcao")
                df_cf_original = df_cf.copy()
                id_cf = proximo_id(df_cf, "Id_Cargo_Func")

                df_lr = load_tabela("Lotacao_Risco")
                df_lr_original = df_lr.copy()
                df_me = load_tabela("Risco_Medida_Existente")
                df_me_original = df_me.copy()
                df_mp = load_tabela("Risco_Medida_Proposta")
                df_mp_original = df_mp.copy()
            except Exception as ex:
                st.error(f"Erro ao preparar dados: {ex}")

                # --- FASE 2: VALIDAÇÃO TOTAL EM MEMÓRIA (nenhum lookup falho grava nada) ---
               
                # Criamos mapas na memória RAM antes de entrar no laço. A busca fica instantânea!
                mapa_riscos = dict(zip(df_risco_load["Nome Risco"], df_risco_load["Id_Risco"]))
                mapa_expo = dict(zip(df_exp["Nome Exposição"], df_exp["Id_Exposição"]))
                mapa_prob = dict(zip(df_prob["Peso Probabilidade"], df_prob["Id_Probabilidade"]))
                mapa_efeito = dict(zip(df_efeito["Peso Efeito"], df_efeito["Id_Efeito"]))

                linhas_lr, linhas_me, linhas_mp = [], [], []
                for ri in st.session_state["lista_riscos"]:
                    # CORREÇÃO: Busca direta nos mapas de memória RAM (Velocidade máxima)
                    id_risco = mapa_riscos.get(ri["risco"])
                    id_expo = mapa_expo.get(ri["expo"])
                    
                    # Quebra os textos para isolar os pesos numéricos
                    p_atual_peso = int(str(ri["prob_atual"]).split(" - ")[0])
                    e_atual_peso = int(str(ri["efeito_atual"]).split(" - ")[0])
                    p_prop_peso = int(str(ri["prob_prop"]).split(" - ")[0])
                    e_prop_peso = int(str(ri["efeito_prop"]).split(" - ")[0])
                    
                    # CORREÇÃO: Busca os IDs das probabilidades e efeitos direto nos mapas
                    id_prob_at = mapa_prob.get(p_atual_peso)
                    id_ef_at = mapa_efeito.get(e_atual_peso)
                    id_prob_pr = mapa_prob.get(p_prop_peso)
                    id_ef_pr = mapa_efeito.get(e_prop_peso)

                    # O restante do laço mantém a estrutura original de montagem das listas
                    id_lr = proximo_id(df_lr, "Id_Lotação_Risco") + len(linhas_lr)
                    linhas_lr.append([id_lr, id_sl, id_cf, id_risco, ri["fator"], ri["fonte"], ri["aval"], ri["danos"], id_expo])

                    id_me = proximo_id(df_me, "Id_Risco_Med_Existente") + len(linhas_me)
                    linhas_me.append([id_me, id_lr, ri["medida_existente"], ri["epi"], ri["epc"], id_prob_at, id_ef_at, ri["val_x_atual"], ri["class_atual"]])

                    id_mp = proximo_id(df_mp, "Id_Risco_Med_Proposta") + len(linhas_mp)
                    linhas_mp.append([id_mp, id_me, ri["medida_proposta"], id_prob_pr, id_ef_pr, ri["val_x_prop"], ri["class_prop"], ri["imediata"], ri["resp_acao"], ri["dt_ini"], ri["dt_fim"], ri["status_acao"], ri["porc_exec"], ri["dt_exec"]])

                # --- FASE 3: GRAVAÇÃO NA NUVEM, COM ROLLBACK PELO SNAPSHOT ORIGINAL ---

                tabelas_gravadas = []
                try:
                    df_sl.loc[len(df_sl)] = [id_sl, id_sec, lotacao, desc_fisica]
                    save_tabela("Secretaria_Lotacao", df_sl)
                    tabelas_gravadas.append(("Secretaria_Lotacao", df_sl_original))

                    df_cf.loc[len(df_cf)] = [id_cf, id_sl, id_cargo, funcao_text, desc_atv, qtd_m, qtd_f, qtd_m + qtd_f]
                    save_tabela("Cargo_Funcao", df_cf)
                    tabelas_gravadas.append(("Cargo_Funcao", df_cf_original))

                    for linha in linhas_lr:
                        df_lr.loc[len(df_lr)] = linha
                    save_tabela("Lotacao_Risco", df_lr)
                    tabelas_gravadas.append(("Lotacao_Risco", df_lr_original))

                    for linha in linhas_me:
                        df_me.loc[len(df_me)] = linha
                    save_tabela("Risco_Medida_Existente", df_me)
                    tabelas_gravadas.append(("Risco_Medida_Existente", df_me_original))

                    for linha in linhas_mp:
                        df_mp.loc[len(df_mp)] = linha
                    save_tabela("Risco_Medida_Proposta", df_mp)
                                        
                    # TRECHO MODIFICADO COM MENSAGEM FIXA DE SALVAMENTO:
                    # --- PREPARA O AMBIENTE PARA O PRÓXIMO LANÇAMENTO ---
                    st.success("✅ Tudo pronto! O Cadastro Geral (Função e todos os Riscos Mapeados) foi gravado com total sucesso no banco de dados na Nuvem!")
                    
                    st.session_state["lista_riscos"] = []
                    st.session_state["cadastro_salvo_sucesso"] = True  # Mantém o gatilho ativo para segurança
                    
                    # Aguarda 3 segundos para que o usuário consiga ler a mensagem fixa de sucesso antes de limpar a tela
                    import time
                    time.sleep(3)
                    
                    st.rerun()



                except Exception as erro_gravacao:
                    for nome_tabela, df_estado_anterior in reversed(tabelas_gravadas):
                        try:
                            save_tabela(nome_tabela, df_estado_anterior)
                        except Exception:
                            pass
                    st.error(f"Falha ao salvar — alterações revertidas para manter consistência. Detalhe: {erro_gravacao}")

            except Exception as ex:
                st.error(f"Erro ao preparar dados: {ex}")


# ==============================================================================
# FUNÇÃO AUXILIAR COM CACHE: Evita reprocessar os Merges a cada clique na tela
# ==============================================================================
@st.cache_data(ttl=60)  # Guarda os cruzamentos prontos por 60 segundos na memória RAM
def gerar_view_consolidada():
    df1 = load_tabela("Secretaria").rename(columns={"Id_Secretaria": "id_sec"})
    df2 = load_tabela("Secretaria_Lotacao").rename(columns={"Id_Sec_Lotação": "id_sl", "Id_Secretaria": "id_sec"})
    df3 = load_tabela("Cargo_Funcao").rename(columns={"Id_Cargo_Func": "id_cf", "Id_Sec_Lotação": "id_sl", "Id_Cargo": "id_c"})
    df4 = load_tabela("Cargo").rename(columns={"Id_Cargo": "id_c"})
    df_lr = load_tabela("Lotacao_Risco").rename(columns={"Id_Lotação_Risco": "id_lr", "Id_Cargo_Func": "id_cf", "Id_Risco": "id_risco"})
    df_risco = load_tabela("Riscos_Ambientais").rename(columns={"Id_Risco": "id_risco"})

    # 🌟 CORREÇÃO: Carrega a tabela de Medidas Propostas que estava faltando aqui dentro
    df_mp = load_tabela("Risco_Medida_Proposta")

    # 🌟 CORREÇÃO ESSENCIAL: Traduz as colunas de Id da planilha para o padrão minúsculo exigido pelos merges abaixo
    df_mp = df_mp.rename(columns={
        "Id_Probabilidade": "id_prob_proposta",
        "Id_Efeito": "id_efeito_proposta"
    })

    # Medida Existente -> tudo com sufixo "_atual"
    df_me = load_tabela("Risco_Medida_Existente").rename(columns={
        "Id_Risco_Med_Existente": "Id_Risco_Med_Existente", # Mantém o nome igual ao Sheets
        "Id_Lotação_Risco": "id_lr",
        "Id_Probabilidade": "id_prob_atual",
        "Id_Efeito": "id_efeito_atual",
        "Nível": "nivel_atual",
        "Classificação": "classificacao_atual",
    })
        

    # Tabelas de apoio, duplicadas com nomes diferentes para o lado atual e proposto
    df_prob_raw = load_tabela("Probabilidade")
    df_prob_atual = df_prob_raw.rename(columns={
        "Id_Probabilidade": "id_prob_atual",
        "Nome Probabilidade": "nome_prob_atual",
        "Peso Probabilidade": "peso_prob_atual",
    })[["id_prob_atual", "nome_prob_atual", "peso_prob_atual"]]
    df_prob_proposta = df_prob_raw.rename(columns={
        "Id_Probabilidade": "id_prob_proposta",
        "Nome Probabilidade": "nome_prob_proposta",
        "Peso Probabilidade": "peso_prob_proposta",
    })[["id_prob_proposta", "nome_prob_proposta", "peso_prob_proposta"]]

    df_efeito_raw = load_tabela("Efeito")
    df_efeito_atual = df_efeito_raw.rename(columns={
        "Id_Efeito": "id_efeito_atual",
        "Nome Efeito": "nome_efeito_atual",
        "Peso Efeito": "peso_efeito_atual",
    })[["id_efeito_atual", "nome_efeito_atual", "peso_efeito_atual"]]
    df_efeito_proposta = df_efeito_raw.rename(columns={
        "Id_Efeito": "id_efeito_proposta",
        "Nome Efeito": "nome_efeito_proposta",
        "Peso Efeito": "peso_efeito_proposta",
    })[["id_efeito_proposta", "nome_efeito_proposta", "peso_efeito_proposta"]]
       
    m_sec_sl = pd.merge(df1, df2, on="id_sec", how="left")
    m_sl_cf = pd.merge(m_sec_sl, df3, on="id_sl", how="left")
    m_cf_carg = pd.merge(m_sl_cf, df4, on="id_c", how="left")
    m_c_lr = pd.merge(m_cf_carg, df_lr, on="id_cf", how="left")
    m_lr_ri = pd.merge(m_c_lr, df_risco, on="id_risco", how="left")
    m_ri_me = pd.merge(m_lr_ri, df_me, on="id_lr", how="left")
    m_me_mp = pd.merge(m_ri_me, df_mp, on="Id_Risco_Med_Existente", how="left")

    m_final = pd.merge(m_me_mp, df_prob_atual, on="id_prob_atual", how="left")
    m_final = pd.merge(m_final, df_prob_proposta, on="id_prob_proposta", how="left")
    m_final = pd.merge(m_final, df_efeito_atual, on="id_efeito_atual", how="left")
    m_final = pd.merge(m_final, df_efeito_proposta, on="id_efeito_proposta", how="left")

    return m_final
    

# ==============================================================================
# ABA 2: CONSULTA DE DADOS + FILTROS CUMULATIVOS
# ==============================================================================
if aba_selecionada == "Consulta":
    st.header("🔍 Painel de Filtros Avançados")
    
    # Recarrega ou consome o cache de forma instantânea
    try:
        # CORREÇÃO: Puxa a tabela unificada direto da memória RAM sem travar a tela
        view_flat = gerar_view_consolidada()
        
        # Criamos as 3 colunas horizontais para os filtros ficarem lado a lado
        c01, c02, c03 = st.columns(3)

        # --- FILTRO 1: ÓRGÃO / SECRETARIA (Fica dentro da coluna c01) ---
        op_f_orgao = ["Selecione..."] + list(view_flat["Nome do Órgão"].dropna().unique())
        f_o = c01.selectbox("Filtro 1: Órgão (Secretaria)", op_f_orgao, key="filtro_c_sec")
        
        # Inicializamos as variáveis para evitar erros de leitura nos passos seguintes
        f_c = "Selecione..."
        f_f = "Selecione..."
        
        # Só exibe o Filtro 2 se o usuário escolher uma Secretaria válida
        if f_o != "Selecione...":
            view_filtrada_sec = view_flat[view_flat["Nome do Órgão"] == f_o]
            
            # --- FILTRO 2: CARGO (Fica dentro da coluna c02) ---
            op_f_carg = ["Selecione..."] + list(view_filtrada_sec["Nome do Cargo"].dropna().unique())
            f_c = c02.selectbox("Filtro 2: Cargo", op_f_carg, key="filtro_c_cargo")
            
            # Só exibe o Filtro 3 se o usuário escolher um Cargo válido
            if f_c != "Selecione...":
                view_filtrada_cargo = view_filtrada_sec[view_filtrada_sec["Nome do Cargo"] == f_c]
                
                # --- FILTRO 3: FUNÇÃO EXECUTADA (Fica dentro da coluna c03) ---
                op_f_fun = ["Selecione..."] + [
                    f"{int(row['id_cf'])} - {row['Função']}" 
                    for _, row in view_filtrada_cargo.drop_duplicates(subset=['id_cf']).iterrows() 
                    if pd.notna(row['Função'])
                ]
                f_f = c03.selectbox("Filtro 3: Função Executada", op_f_fun, key="filtro_c_funcao")
        
        # --- APLICAÇÃO DOS FILTROS NA PLANILHA ---

        # --- APLICAÇÃO DOS FILTROS NA PLANILHA ---
        filtered_view = view_flat.copy()
        
        if f_o != "Selecione...": 
            filtered_view = filtered_view[filtered_view["Nome do Órgão"] == f_o]
        if f_c != "Selecione...": 
            filtered_view = filtered_view[filtered_view["Nome do Cargo"] == f_c]
            
        if f_f != "Selecione...":
            # Extrai o ID numérico da função escolhida no selectbox
            id_cf_selecionado = int(f_f.split(" - ")[0])
            filtered_view = filtered_view[filtered_view["id_cf"] == id_cf_selecionado]
            
            st.success(f"✅ Prontuário da Função ID {id_cf_selecionado} localizado com sucesso!")
            
            # 1. Captura com segurança a primeira linha da função para montar o cabeçalho (Faixa 1)
            linha_base = filtered_view[filtered_view["id_cf"] == id_cf_selecionado].iloc[0]
            
            st.markdown("### 📋 Informações Gerais da Função (Faixa 1)")
            
            # Layout espelhado idêntico ao Cadastro Interativo utilizando CSS para clareza visual
            st.html("<style>textarea:disabled, input:disabled { color: black !important; -webkit-text-fill-color: black !important; }</style>")
            
            c_v1, c_v2 = st.columns(2)
            c_v1.text_input("Órgão / Secretaria", value=str(linha_base.get("Nome do Órgão", "")), disabled=True, key="c_v_sec")
            c_v2.text_input("Lotação (Setor/Departamento)", value=str(linha_base.get("Lotação", "")), disabled=True, key="c_v_lot")
            st.text_input("Descrição Física do Ambiente", value=str(linha_base.get("Descrição Física", "")), disabled=True, key="c_v_desc")
            
            c_v3, c_v4 = st.columns(2)
            c_v3.text_input("Cargo Referência", value=str(linha_base.get("Nome do Cargo", "")), disabled=True, key="c_v_cargo")
            c_v4.text_input("Função Praticada", value=str(linha_base.get("Função", "")), disabled=True, key="c_v_fun")
            
            c_v5, c_v6 = st.columns(2)
            c_v5.text_input("Quantidade Masc. (M)", value=str(linha_base.get("Quantidade M", "0")), disabled=True, key="c_v_qm")
            c_v6.text_input("Quantidade Fem. (F)", value=str(linha_base.get("Quantidade F", "0")), disabled=True, key="c_v_qf")
            
            st.text_area("Descrição Geral da Atividade (Função)", value=str(linha_base.get("Descrição Atividade", "")), disabled=True, key="c_v_atv")
            
            # --- APRESENTAÇÃO COMPACTA DE RISCOS ---
            st.markdown("### ⚡ Riscos Ocupacionais Mapeados (Faixas 2, 3, 4 e 5)")
            
            # Extrai apenas as colunas amigáveis de riscos sem redundâncias de IDs numéricos
            colunas_pgr = ["Nome Risco", "Fator de Risco", "Fonte Geradora", "Medida Existente", "Medida Proposta", "Status"]
            colunas_validas = [c for c in colunas_pgr if c in filtered_view.columns]
            df_riscos_bloco = filtered_view[filtered_view["id_cf"] == id_cf_selecionado][colunas_validas].drop_duplicates()
            
            st.dataframe(df_riscos_bloco, use_container_width=True)
            
            # --- SISTEMA DE GESTÃO DIRETIVA (BOTÕES) ---
            st.markdown("---")
            c_g1, c_g2 = st.columns(2)
            
            # Ação 1: Despachar dados brutos para edição na ABA 1
            if c_g1.button("✏️ Editar Registro no Cadastro", type="primary", use_container_width=True, key="btn_c_editar"):
                st.info("Transferindo registros históricos para a memória ativa...")
                # Captura todas as ocorrências de riscos mapeados para reinjetar na lista temporária da Aba 1
                linhas_funcao_reais = filtered_view[filtered_view["id_cf"] == id_cf_selecionado]
                
                lista_reconstruida = []
                for _, r_linha in linhas_funcao_reais.iterrows():
                    lista_reconstruida.append({
                        "risco": r_linha.get("Nome Risco", ""),
                        "fator": r_linha.get("Fator de Risco", ""),
                        "fonte": r_linha.get("Fonte Geradora", ""),
                        "aval": r_linha.get("Avaliação Quantitativa", ""),
                        "danos": r_linha.get("Danos à Saúde", ""),
                        "expo": r_linha.get("Nome Exposição", ""),
                        "medida_existente": r_linha.get("Medida Existente", ""),
                        "epi": r_linha.get("EPI EFICAZ", ""),
                        "epc": r_linha.get("EPC EFICAZ", ""),
                        "prob_atual": f"{r_linha.get('Peso Probabilidade', '1')} - {r_linha.get('Nome Probabilidade', '')}",
                        "efeito_atual": f"{r_linha.get('Peso Efeito', '1')} - {r_linha.get('Nome Efeito', '')}",
                        "val_x_atual": r_linha.get("Nível", 1),
                        "class_atual": r_linha.get("Classificação", ""),
                        "medida_proposta": r_linha.get("Medida Proposta", ""),
                        "tmp_sel": r_linha.get("Nome Tipo Medida Proposta", ""),
                        "prob_prop": f"{r_linha.get('Peso Probabilidade', '1')} - {r_linha.get('Nome Probabilidade', '')}", # aproximado por segurança
                        "efeito_prop": f"{r_linha.get('Peso Efeito', '1')} - {r_linha.get('Nome Efeito', '')}",
                        "val_x_prop": r_linha.get("Nível", 1),
                        "class_prop": r_linha.get("Classificação", ""),
                        "imediata": r_linha.get("Imediata", ""),
                        "resp_acao": r_linha.get("Responsável", ""),
                        "porc_exec": int(r_linha.get("Porcentagem", 0)) if pd.notna(r_linha.get("Porcentagem")) else 0,
                        "dt_ini": r_linha.get("Data Início", ""),
                        "dt_fim": r_linha.get("Data Final", ""),
                        "dt_exec": r_linha.get("Data Execução", ""),
                        "status_acao": r_linha.get("Status", "Não Iniciado")
                    })
                
                # Alimenta o estado da Aba 1 para "acordar" preenchida
                st.session_state["lista_riscos"] = lista_reconstruida
                st.session_state["id_funcao_em_alteracao_db"] = id_cf_selecionado
                
                # Altera o nome da aba ativa diretamente no Python (Navegação imediata)
                st.session_state["aba_ativa_nome"] = "Cadastro Interativo"
                st.session_state["forcar_nav_cadastro"] = True  # sinaliza a troca para o próximo run
                
                st.success("Registros sincronizados na memória ativa! Redirecionando...")
                st.rerun()

            

                
                
            # Ação 2: Ativar modal de segurança para expurgo de dados
            if c_g2.button("🗑️ Excluir Função do Banco de Dados", type="secondary", use_container_width=True, key="btn_c_excluir"):
                st.session_state["confirmar_exclusao_id_cf"] = id_cf_selecionado
                st.rerun()

            # Caixa de verificação física para evitar deleção acidental por cliques errados
            if st.session_state.get("confirmar_exclusao_id_cf", None) == id_cf_selecionado:
                st.error(f"⚠️ **CONFIRMAÇÃO CRÍTICA:** Deseja expurgar a Função ID {id_cf_selecionado} e TODOS os riscos acoplados permanentemente do Google Drive?")
                c_ex1, c_ex2 = st.columns(2)
                
                if c_ex1.button("Sim, Excluir Definitivamente", type="primary", use_container_width=True, key="btn_c_confirma_sim"):
                    # Carrega as tabelas cruas diretamente do Google Drive para expurgar as referências cruzadas
                    df_cf_cru = load_tabela("Cargo_Funcao")
                    df_lr_cru = load_tabela("Lotacao_Risco")
                    df_me_cru = load_tabela("Risco_Medida_Existente")
                    df_mp_cru = load_tabela("Risco_Medida_Proposta")
                    
                    # 1. Localiza os IDs secundários (chaves estrangeiras) que pertencem a essa função exclusiva
                    ids_lr_alvo = df_lr_cru[df_lr_cru["Id_Cargo_Func"] == id_cf_selecionado]["Id_Lotação_Risco"].tolist()
                    ids_me_alvo = df_me_cru[df_me_cru["Id_Lotação_Risco"].isin(ids_lr_alvo)]["Id_Risco_Med_Existente"].tolist()
                    
                    # 2. Executa a filtragem reversa (Mantém apenas o que NÃO pertence à função deletada)
                    df_mp_novo = df_mp_cru[~df_mp_cru["Id_Risco_Med_Existente"].isin(ids_me_alvo)]
                    df_me_novo = df_me_cru[~df_me_cru["Id_Risco_Med_Existente"].isin(ids_me_alvo)]
                    df_lr_novo = df_lr_cru[~df_lr_cru["Id_Lotação_Risco"].isin(ids_lr_alvo)]
                    df_cf_novo = df_cf_cru[df_cf_cru["Id_Cargo_Func"] != id_cf_selecionado]
                    
                    # 3. Salva em cascata as tabelas limpas de volta para a nuvem
                    save_tabela("Risco_Medida_Proposta", df_mp_novo)
                    save_tabela("Risco_Medida_Existente", df_me_novo)
                    save_tabela("Lotacao_Risco", df_lr_novo)
                    save_tabela("Cargo_Funcao", df_cf_novo)
                    
                    # Reseta os gatilhos e atualiza a aplicação
                    st.session_state["confirmar_exclusao_id_cf"] = None
                    st.success("🚀 Registro removido com sucesso e tabelas limpas na Nuvem!")
                    st.rerun()
                    
                if c_ex2.button("Cancelar Operação", use_container_width=True, key="btn_c_confirma_nao"):
                    st.session_state["confirmar_exclusao_id_cf"] = None
                    st.rerun()

        else: 
            # Se nenhuma função específica foi selecionada ainda, mostra a tabela filtrada até o momento
            st.dataframe(filtered_view, use_container_width=True) 
            st.info("💡 Filtre até o nível de 'Função Executada' para abrir as opções de Edição e Exclusão.")
            
    except Exception as e:
        st.warning(f"Banco de dados insuficiente para montagem da visualização. Detalhe: {e}")


# ==============================================================================
# ABA 3: RELATÓRIO DO PGR E MÓDULO DOCX
# ==============================================================================

if aba_selecionada == "Relatório Completo":
    ...  # conteúdo que estava em abas[2]
    st.header("🗄️ Relatorização Consolidadada e Motor PDF")
    
    st.subheader("Equipe Técnica do SESMT")
    df_resp = pd.DataFrame([{"nome": "Nome Exemplo", "matricula": "0000", "funcao": "Cargo", "conselho": "Registro"}])
    st.write("Edite os dados na tabela abaixo para inclusão automatizada na página 2 do Relatório .docx:")
    edited_sesmt = st.data_editor(df_resp, num_rows="dynamic", key="sesmt_edit_v2", use_container_width=True)
    
    responsaveis_assign = st.multiselect("Selecione quem fará a ASSINATURA final no relatório:", edited_sesmt["nome"].tolist())

    resps_dict = edited_sesmt[edited_sesmt["nome"].isin(responsaveis_assign)].to_dict(orient="records")
    linhas_assinatura = [resps_dict[i:i + 2] for i in range(0, len(resps_dict), 2)]
    
    st.markdown("---")
    try:
        df_sec = load_tabela("Secretaria")
        all_secretarias = df_sec["Nome do Órgão"].tolist() if not df_sec.empty else []
    except:
        all_secretarias = []
        
    sec_selecionada_relatorio = st.selectbox("Selecione o Entidade a emitir o Relatório PGR PDF:", all_secretarias)

    if st.session_state["usuario_perfil"] == "Admin":
        if st.button("📄 GERAR RELATÓRIO PGR OFICIAL (PDF/LibreOffice)"):
            with st.spinner("Processando Integração Automática DOCX-PDF via motor DocxTemplate..."):
                
                    sec_dados = df_sec[df_sec["Nome do Órgão"] == sec_selecionada_relatorio].iloc[0]
                    id_ss = sec_dados["Id_Secretaria"]
                    df_rel_lotacao = load_tabela("Secretaria_Lotacao")
                    df_rel_cargo_funcao = load_tabela("Cargo_Funcao")
                    lotes = df_rel_lotacao[df_rel_lotacao["Id_Secretaria"] == id_ss]["Id_Sec_Lotação"].tolist()
                    total_mf_calc = df_rel_cargo_funcao[df_rel_cargo_funcao["Id_Sec_Lotação"].isin(lotes)]["TOTAL"].sum()
                    
                    hj = datetime.date.today()
                    tag_data = f"{hj.month}/{hj.year} a {hj.month}/{hj.year + 2}"
                    
                    # Monta uma tabela por Função (todos os campos do Formulário de 5 Faixas)
                    df_view_rel = gerar_view_consolidada()
                    df_view_rel = df_view_rel[df_view_rel["id_sec"] == id_ss]

                    riscos_faixas = []
                    for id_cf_rel in df_view_rel["id_cf"].dropna().unique():
                        bloco_funcao = df_view_rel[df_view_rel["id_cf"] == id_cf_rel]
                        cab = bloco_funcao.iloc[0]

                        lista_riscos_funcao = []
                        for _, rl in bloco_funcao.iterrows():
                            if pd.isna(rl.get("Nome Risco")):
                                continue
                            lista_riscos_funcao.append({
                                "risco": str(rl.get("Nome Risco", "")),
                                "fator": str(rl.get("Fator de Risco", "")),
                                "fonte": str(rl.get("Fonte Geradora", "")),
                                "aval": str(rl.get("Avaliação Quantitativa", "")),
                                "danos": str(rl.get("Danos à Saúde", "")),
                                "expo": str(rl.get("Nome Exposição", "")),
                                "medida_existente": str(rl.get("Medida Existente", "")),
                                "epi": str(rl.get("EPI EFICAZ", "")),
                                "epc": str(rl.get("EPC EFICAZ", "")),

                                # --- Avaliação ATUAL (antes da medida) ---
                                "probabilidade_atual": f"{rl.get('peso_prob_atual', '')} - {rl.get('nome_prob_atual', '')}",
                                "efeito_atual": f"{rl.get('peso_efeito_atual', '')} - {rl.get('nome_efeito_atual', '')}",
                                "nivel_atual": str(rl.get("nivel_atual", "")),
                                "classificacao_atual": str(rl.get("classificacao_atual", "")),

                                # --- Plano de ação ---
                                "medida_proposta": str(rl.get("Medida Proposta", "")),          # texto livre digitado pelo usuário
                                "tipo_medida_proposta": str(rl.get("Nome Tipo Medida Proposta", "")),  # EPC/EPI/Administrativa/Médica

                                # --- Avaliação PROPOSTA (esperada após a medida) ---
                                "probabilidade_proposta": f"{rl.get('peso_prob_proposta', '')} - {rl.get('nome_prob_proposta', '')}",
                                "efeito_proposta": f"{rl.get('peso_efeito_proposta', '')} - {rl.get('nome_efeito_proposta', '')}",
                                "nivel_proposta": str(rl.get("nivel_proposta", "")),
                                "classificacao_proposta": str(rl.get("classificacao_proposta", "")),

                                "imediata": str(rl.get("Imediata", "")),
                                "responsavel": str(rl.get("Responsável", "")),
                                "data_inicio": str(rl.get("Data Início", "")),
                                "data_final": str(rl.get("Data Final", "")),
                                "data_execucao": str(rl.get("Data Execução", "")),
                                "status": str(rl.get("Status", "")),
                                "porcentagem": str(rl.get("Porcentagem", "")),
                            })
                            
                        riscos_faixas.append({
                            "orgao": str(cab.get("Nome do Órgão", "")),
                            "lotacao": str(cab.get("Lotação", "")),
                            "descricao_fisica": str(cab.get("Descrição Física", "")),
                            "cargo": str(cab.get("Nome do Cargo", "")),
                            "funcao": str(cab.get("Função", "")),
                            "qtd_m": str(cab.get("Quantidade M", "")),
                            "qtd_f": str(cab.get("Quantidade F", "")),
                            "total": str(cab.get("TOTAL", "")),
                            "descricao_atividade": str(cab.get("Descrição Atividade", "")),
                            "riscos": lista_riscos_funcao
                        })

                                        
                    # Filtro recursivo para normalizar strings de formulários e tabelas dinâmicas
                    def _limpar(d):
                        if isinstance(d, dict): return {k: _limpar(v) for k, v in d.items()}
                        if isinstance(d, list): return [_limpar(i) for i in d]
                        if isinstance(d, str): return __import__('unicodedata').normalize('NFC', d)
                        return d

                    parametros = _limpar({
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
                    })
               
                    
                    # --------------------------------------------------------------------------
                    # MÓDULO DE EMISSÃO: MOTOR AUTOMÁTICO WORD (.DOCX) PARA PDF
                    # --------------------------------------------------------------------------

                    # Criação de IDs únicos para evitar conflitos de múltiplos usuários
                    id_unico = uuid.uuid4().hex
                    template_path = f"/tmp/base_{id_unico}.docx"
                    docx_out = f"/tmp/relatorio_{id_unico}.docx"
                    pdf_path = f"/tmp/relatorio_{id_unico}.pdf"

                    # Baixar DOCX pelo ID da API
                    request = drive_service.files().get_media(fileId=DOCX_TEMPLATE_ID)

                    # === Configurações de cabeçalho para evitar cache ===
                    request.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                    request.headers['Pragma'] = 'no-cache'
                    request.headers['Expires'] = '0'
                
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while done is False:
                        status, done = downloader.next_chunk()

                    # Só grava o arquivo depois que o download estiver 100% completo
                    with open(template_path, "wb") as f:
                        f.write(fh.getvalue())



                    # --- ENTRADA DO NOVO MOTOR DOCXTPL COM TRATAMENTO DE ERROS ALINHADO ---
                    try:
                        # 1. Abre o arquivo Word, processa o dicionário 'parametros' e salva o resultado
                        doc = DocxTemplate(template_path)
                        
                        parametros['linhas_assinatura'] = linhas_assinatura

                        doc.render(parametros)
                        doc.save(docx_out)

                        # 2. Executa a conversão do Word (.docx) para PDF via LibreOffice Headless
                        comando = ['soffice', '--headless', '--convert-to', 'pdf', '--outdir', '/tmp', docx_out]
                        subprocess.run(comando, check=True)

                        # 3. Lê o arquivo PDF gerado para disponibilizar ao usuário
                        with open(pdf_path, "rb") as pdf_file:
                            pdf_bytes = pdf_file.read()

                        st.success("✅ Relatório PGR Oficial processado com sucesso!")
                        st.download_button(
                            label="📥 Download Arquivo Validado (PDF)", 
                            data=pdf_bytes, 
                            file_name=f"PGR_{sec_selecionada_relatorio}.pdf", 
                            mime="application/pdf"
                        )

                    except Exception as docx_err:
                        # Se houver um erro de digitação de tag no Word, o docxtpl avisa aqui de forma limpa
                        st.error("⚠️ **Erro de Processamento no Documento:** Não foi possível aplicar os dados ao modelo Word.")
                        st.markdown(f"Detalhes do erro técnico: `{str(docx_err)}`")
                        st.info("Dica: Verifique se todas as tags '{{' e '{%' estão fechadas corretamente dentro do arquivo do Word.")

                    finally:
                        # Limpeza imediata de todos os arquivos temporários criados nesta execução
                        for arquivo in [template_path, docx_out, pdf_path]:
                            if os.path.exists(arquivo):
                                os.remove(arquivo)

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



