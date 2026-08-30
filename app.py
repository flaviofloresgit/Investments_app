#Activar el entorno: .\venv\Scripts\Activate.ps1
#Iniciar la app: streamlit run app.py

import os
import requests
import streamlit as st
import pandas as pd
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta

#Cargar variables del entorno
load_dotenv()

# Buscar clave primero en .env local y luego en Secrets de la nube
def get_secret(key_name):
    if key_name in os.environ and os.environ[key_name]:
        return os.environ[key_name]
    elif hasattr(st, "secrets") and key_name in st.secrets:
        return st.secrets[key_name]
    return None

url = get_secret("SUPABASE_URL")
key = get_secret("SUPABASE_KEY")
banxico_token = get_secret("BANXICO_TOKEN")
app_password = get_secret("APP_PASSWORD")
supabase: Client = create_client(url, key)

# Función para verificar la contraseña
def verificar_password():
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False

    if not st.session_state["autenticado"]:
        st.title("🔒 Acceder al Portafolio")
        input_pass = st.text_input("Ingresa la contraseña para acceder:", type="password")
        if st.button("Iniciar Sesión"):
            if input_pass == app_password:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta. Acceso denegado.")
        return False
    return True

# Detener la ejecución si no está autenticado
if not verificar_password():
    st.stop()

#Funciones auxiliares de consulta a Banxico
@st.cache_data(ttl=86400)
def obtener_tc_dof(fecha_str):
    """
    Obtiene el TC publicado en el DOF para 'fecha_str' (YYYY-MM-DD),
    consultando el valor FIX (Serie SF43718) del último día hábil anterior.
    """
    if not banxico_token:
        st.error("⚠️ Error: No se encontró la variable BANXICO_TOKEN en el archivo .env o en secrets.")
        return 20.00

    try:
        headers = {"Bmx-Token": banxico_token}
        fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")
        
        # Consultar rango de 7 días hacia atrás hasta el día anterior
        fecha_inicio = (fecha_dt - timedelta(days=7)).strftime("%Y-%m-%d")
        fecha_fin = (fecha_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Serie oficial diaria FIX: SF43718
        url_bmx = f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/{fecha_inicio}/{fecha_fin}"
        
        response = requests.get(url_bmx, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            datos = data['bmx']['series'][0]['datos']
            
            # Filtrar días inhábiles o festivos que contienen 'N/N'
            validos = [d for d in datos if d.get('dato') != 'N/N']
            if validos:
                # Tomar el último valor válido (FIX del día hábil anterior = DOF del día consultado)
                return float(validos[-1]['dato'])
        else:
            st.warning(f"⚠️ Error HTTP Banxico ({response.status_code}) para fecha {fecha_str}")
                
        return 20.00
    except Exception as e:
        st.error(f"⚠️ Error en consulta a Banxico ({fecha_str}): {e}")
        return 20.00

@st.cache_data(ttl=86400)
def obtener_tabla_inpc():
    """Carga los registros de INPC desde Supabase hacia la caché."""
    try:
        res = supabase.table("inpc").select("*").execute()
        if res.data:
            tabla = {}
            for item in res.data:
                clave = f"{item['anio']}-{str(item['mes']).zfill(2)}"
                tabla[clave] = float(item['valor'])
            return tabla
    except Exception as e:
        st.error(f"Error al cargar INPC desde la base de datos: {e}")
    return {}

def obtener_inpc(anio, mes):
    """Busca el INPC en la caché cargada desde Supabase."""
    tabla = obtener_tabla_inpc()
    clave = f"{str(anio)}-{str(mes).zfill(2)}"
    
    if clave in tabla:
        return tabla[clave]
    elif tabla:
        return tabla[max(tabla.keys())]
    
    return 1.0

def calcular_factor_inpc_sat(fecha_compra, fecha_venta):
    """Calcula el Factor de Actualización según Art. 129 LISR."""
    dt_venta_anterior = pd.to_datetime(fecha_venta) - pd.DateOffset(months=1)
    inpc_venta = obtener_inpc(dt_venta_anterior.year, dt_venta_anterior.month)

    dt_compra = pd.to_datetime(fecha_compra)
    inpc_compra = obtener_inpc(dt_compra.year, dt_compra.month)

    if inpc_compra > 0:
        factor = inpc_venta / inpc_compra
    else:
        factor = 1.0

    return max(1.0, round(factor, 5))

def mostrar_tabla_con_filtros(df: pd.DataFrame, key_prefix: str):
    """
    Agrega controles de filtro (Año, Broker, Ticker) y muestra el DataFrame resultante. Aplicable a pestañas Compras y Ventas.
    """
    if df.empty:
        st.info("No hay registros disponibles.")
        return df

    # Crear copia para no alterar el DataFrame original
    df_temp = df.copy()
    
    # 1. Preparar columna de Año asegurando formato de fecha
    if "fecha" in df_temp.columns:
        df_temp["Fecha_dt"] = pd.to_datetime(df_temp["fecha"])
        df_temp["Año"] = df_temp["Fecha_dt"].dt.year
    else:
        df_temp["Año"] = "Sin Fecha"

    # Obtener opciones únicas
    anos_opt = sorted(df_temp["Año"].unique(), reverse=True)
    brokers_opt = sorted(df_temp["broker"].unique()) if "broker" in df_temp.columns else []
    tickers_opt = sorted(df_temp["ticker"].unique()) if "ticker" in df_temp.columns else []

    # 2. Renderizar controles de filtro en 3 columnas
    col_ano, col_broker, col_ticker = st.columns(3)

    with col_ano:
        sel_anos = st.multiselect(
            "📅 Año",
            options=anos_opt,
            default=anos_opt,
            key=f"{key_prefix}_multiselect_ano"
        )

    with col_broker:
        sel_brokers = st.multiselect(
            "🏦 Broker",
            options=brokers_opt,
            default=brokers_opt,
            key=f"{key_prefix}_multiselect_broker"
        )

    with col_ticker:
        sel_tickers = st.multiselect(
            "🏷️ Ticker",
            options=tickers_opt,
            default=tickers_opt,
            key=f"{key_prefix}_multiselect_ticker"
        )

    # 3. Aplicar lógica de filtrado
    mask = (
        df_temp["Año"].isin(sel_anos) &
        (df_temp["broker"].isin(sel_brokers) if brokers_opt else True) &
        (df_temp["ticker"].isin(sel_tickers) if tickers_opt else True)
    )
    
    df_filtrado = df_temp[mask].drop(columns=["Fecha_dt", "Año"], errors="ignore")

    # 4. Mostrar resumen rápido y tabla
    st.caption(f"Mostrando {len(df_filtrado)} de {len(df)} registros.")
    st.dataframe(df_filtrado, width="stretch", hide_index=True, column_config={"cantidad": st.column_config.NumberColumn(format="%.9f")})

    return df_filtrado

# Configurar la página
st.set_page_config(page_title="Mi Portafolio Fiscal", layout="wide")
st.title("📊 Sistema de Gestión de Acciones & Portafolio Fiscal")

# Pestañas de navegación
tab_compras, tab_ventas, tab_dividendos, tab_reporte, tab_inpc = st.tabs(["🛒 Compras", "🏷️ Ventas", "💰 Dividendos", "📈 Reporte Fiscal (FIFO)", "📜 Índices INPC (DOF)"])

# Lista de Brokers disponibles
BROKERS = ["Banco Plata", "ARQ", "Interactive Brokers", "Fintual"]

# ==========================================
# PESTAÑA 1: REGISTRO DE COMPRAS
# ==========================================
with tab_compras:
    with st.expander("➕ Registrar Nueva Compra", expanded=False):   
        with st.form("form_compras", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                broker_c = st.selectbox("Broker", BROKERS, key="b_compra")
                fecha_c = st.date_input("Fecha de Compra", key="f_compra")
                ticker_c = st.text_input("Ticker (ej. AAPL)", key="t_compra").upper()
            with col2:
                cantidad_c = st.number_input("Cantidad de Acciones", min_value=0.0, format="%.9f", step=0.000000001, key="c_compra")
                precio_c = st.number_input("Precio por Acción (USD)", min_value=0.0, format="%.5f", step=0.00001, key="p_compra")
            with col3:
                comision_c = st.number_input("Comisión (USD)", min_value=0.0, value=0.0, format="%.5f", step=0.00001, key="com_compra")
        
            submit_compra = st.form_submit_button("Guardar Compra")

    if submit_compra:
        if ticker_c and cantidad_c > 0 and precio_c > 0:
            datos = {
                "broker": broker_c,
                "fecha": str(fecha_c),
                "ticker": ticker_c,
                "cantidad": cantidad_c,
                "precio_usd": precio_c,
                "comision_usd": comision_c
            }
            supabase.table("compras").insert(datos).execute()
            st.success(f"¡Compra de {cantidad_c} {ticker_c} en {broker_c} guardada!")
            st.rerun()
        else:
            st.error("Por favor completa el Ticker, Cantidad y Precio.")

    # Mostrar historial de compras
    st.divider()
    st.subheader("Historial de Compras Registradas")
    res_compras = supabase.table("compras").select("*").order("fecha", desc=True).execute()
    if res_compras.data:
        cols = ["fecha", "broker", "ticker", "cantidad", "precio_usd", "comision_usd"]
        df_c = pd.DataFrame(res_compras.data)[cols]
        mostrar_tabla_con_filtros(df_c, key_prefix="compras")
    else:
        st.info("No hay compras registradas en la base de datos.")

# ==========================================
# PESTAÑA 2: REGISTRO DE VENTAS
# ==========================================
with tab_ventas:
    with st.expander("➕ Registrar Nueva Venta", expanded=False):
        with st.form("form_ventas", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                broker_v = st.selectbox("Broker", BROKERS, key="b_venta")
                fecha_v = st.date_input("Fecha de Venta", key="f_venta")
                ticker_v = st.text_input("Ticker (ej. AAPL)", key="t_venta").upper()
            with col2:
                cantidad_v = st.number_input("Cantidad Vendida", min_value=0.0, format="%.9f", step=0.000000001, key="c_venta")
                precio_v = st.number_input("Precio de Venta (USD)", min_value=0.0, format="%.5f", step=0.00001, key="p_venta")
            with col3:
                comision_v = st.number_input("Comisión (USD)", min_value=0.0, value=0.0, format="%.5f", step=0.00001, key="com_venta")
        
            submit_venta = st.form_submit_button("Guardar Venta")

    if submit_venta:
        if ticker_v and cantidad_v > 0 and precio_v > 0:
            datos = {
                "broker": broker_v,
                "fecha": str(fecha_v),
                "ticker": ticker_v,
                "cantidad": cantidad_v,
                "precio_usd": precio_v,
                "comision_usd": comision_v
            }
            supabase.table("ventas").insert(datos).execute()
            st.success(f"¡Venta de {cantidad_v} {ticker_v} en {broker_v} guardada!")
            st.rerun()
        else:
            st.error("Por favor completa los campos obligatorios.")

    st.divider()
    st.subheader("Historial de Ventas Registradas")
    res_ventas = supabase.table("ventas").select("*").order("fecha", desc=True).execute()
    if res_ventas.data:
        cols = ["fecha", "broker", "ticker", "cantidad", "precio_usd", "comision_usd"]
        df_v = pd.DataFrame(res_ventas.data)[cols]
        mostrar_tabla_con_filtros(df_v, key_prefix="ventas")
    else:
        st.info("No hay ventas registradas en la base de datos.")

# ==========================================
# PESTAÑA 3: DIVIDENDOS
# ==========================================
with tab_dividendos:
    st.header("💰 Gestión y Reporte Fiscal de Dividendos")
    
    # --- 1. FORMULARIO DE REGISTRO ---
    with st.expander("➕ Registrar Nuevo Dividendo", expanded=False):
        with st.form("form_dividendo", clear_on_submit=True):
            col_d1, col_d2, col_d3 = st.columns(3)
            with col_d1:
                f_div_fecha = st.date_input("Fecha de Pago", key="div_fecha")
                f_div_broker = st.selectbox("Broker", BROKERS, key="div_broker")
            with col_d2:
                f_div_ticker = st.text_input("Ticker", key="div_ticker").upper()
                f_div_monto_bruto = st.number_input("Monto Bruto (USD)", min_value=0.0, format="%.4f", key="div_bruto")
            with col_d3:
                f_div_retencion = st.number_input("Retención IRS (USD)", min_value=0.0, format="%.4f", key="div_retencion")
            
            btn_guardar_div = st.form_submit_button("Guardar Dividendo")
            if btn_guardar_div:
                if f_div_ticker and f_div_monto_bruto > 0:
                    payload = {
                        "fecha": str(f_div_fecha),
                        "ticker": f_div_ticker,
                        "broker": f_div_broker,
                        "monto_bruto_usd": f_div_monto_bruto,
                        "retencion_irs_usd": f_div_retencion
                    }
                    supabase.table("dividendos").insert(payload).execute()
                    st.success("✅ Dividendo registrado correctamente.")
                    st.rerun()
                else:
                    st.error("Por favor ingresa un Ticker y un Monto Bruto válido.")

    st.divider()

    # --- 2. CONSULTA Y FILTROS ---
    st.subheader("📊 Balance Fiscal para la Declaración Anual (SAT)")
    
    q_divs = supabase.table("dividendos").select("*").order("fecha", desc=False).execute()
    
    if q_divs.data:
        df_divs = pd.DataFrame(q_divs.data)
        df_divs["anio"] = df_divs["fecha"].str[:4]
        
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            brokers_disponibles = ["Todos"] + list(df_divs["broker"].unique())
            filtro_broker_div = st.selectbox("Filtrar por Broker", brokers_disponibles, key="f_div_broker")
        with col_f2:
            anios_disponibles = ["Todos"] + sorted(list(df_divs["anio"].unique()), reverse=True)
            filtro_anio_div = st.selectbox("Ejercicio Fiscal (Año)", anios_disponibles, key="f_div_anio")
        
        # Aplicación de Filtros
        df_filtrado = df_divs.copy()
        if filtro_broker_div != "Todos":
            df_filtrado = df_filtrado[df_filtrado["broker"] == filtro_broker_div]
        if filtro_anio_div != "Todos":
            df_filtrado = df_filtrado[df_filtrado["anio"] == filtro_anio_div]

        if not df_filtrado.empty:
            # --- 3. CÁLCULO DE VALORES FISCALES CON TC DOF ---
            filas_calculadas = []
            for _, row in df_filtrado.iterrows():
                fecha_pago = row["fecha"]
                monto_bruto_usd = float(row["monto_bruto_usd"])
                ret_irs_usd = float(row.get("retencion_irs_usd", 0.0))
                
                # Obtener Tipo de Cambio DOF oficial
                tc_dof = obtener_tc_dof(fecha_pago)
                
                # Conversiones a MXN
                monto_bruto_mxn = monto_bruto_usd * tc_dof
                ret_irs_mxn = ret_irs_usd * tc_dof
                monto_neto_mxn = monto_bruto_mxn - ret_irs_mxn
                isr_10_mxn = monto_bruto_mxn * 0.10
                
                filas_calculadas.append({
                    "id": row["id"],
                    "Fecha": fecha_pago,
                    "Ticker": row["ticker"],
                    "Broker": row["broker"],
                    "Monto Bruto (USD)": monto_bruto_usd,
                    "Retención IRS (USD)": ret_irs_usd,
                    "TC DOF": tc_dof,
                    "Ingreso Bruto (MXN)": monto_bruto_mxn,
                    "ISR Retenido Extranjero (MXN)": ret_irs_mxn,
                    "Ingreso Neto (MXN)": monto_neto_mxn,
                    "ISR 10% México (MXN)": isr_10_mxn
                })
            
            df_res_div = pd.DataFrame(filas_calculadas)
            
            # --- 4. MÉTRICAS DE RESUMEN (VALORES DIRECTOS PARA EL SAT) ---
            total_bruto_mxn = df_res_div["Ingreso Bruto (MXN)"].sum()
            total_irs_mxn = df_res_div["ISR Retenido Extranjero (MXN)"].sum()
            total_neto_mxn = df_res_div["Ingreso Neto (MXN)"].sum()
            total_isr10_mxn = df_res_div["ISR 10% México (MXN)"].sum()
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Ingreso Bruto Acumulable", f"${total_bruto_mxn:,.2f} MXN")
            m2.metric("Impuesto Retenido EE.UU. (Acreditable)", f"${total_irs_mxn:,.2f} MXN")
            m3.metric("Ingreso Neto Percibido", f"${total_neto_mxn:,.2f} MXN")
            m4.metric("ISR 10% México (Estimado)", f"${total_isr10_mxn:,.2f} MXN")
            
            st.divider()
            
            # --- 5. TABLA DESGLOSADA ---
            st.dataframe(
                df_res_div.drop(columns=["id"]),
                width="stretch",
                column_config={
                    "Monto Bruto (USD)": st.column_config.NumberColumn(format="$%.4f"),
                    "Retención IRS (USD)": st.column_config.NumberColumn(format="$%.4f"),
                    "TC DOF": st.column_config.NumberColumn(format="%.4f"),
                    "Ingreso Bruto (MXN)": st.column_config.NumberColumn(format="$%.2f"),
                    "ISR Retenido Extranjero (MXN)": st.column_config.NumberColumn(format="$%.2f"),
                    "Ingreso Neto (MXN)": st.column_config.NumberColumn(format="$%.2f"),
                    "ISR 10% México (MXN)": st.column_config.NumberColumn(format="$%.2f"),
                }
            )
        else:
            st.info("No hay dividendos registrados para los filtros seleccionados.")
    else:
        st.info("Aún no tienes registros de dividendos guardados.")

# ==========================================
# PESTAÑA 4: REPORTE FISCAL Y CÁLCULO FIFO
# ==========================================
with tab_reporte:
    st.subheader("📊 Resumen Fiscal y Algoritmo PEPS (FIFO)")
    
    # Filtros de Broker y Año de Venta
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        broker_filtro = st.selectbox("Filtrar por Broker", ["Todos"] + BROKERS, key="f_reporte_broker")
    with col_f2:
        anio_actual = datetime.now().year
        opciones_anios = ["Todos"] + [str(y) for y in range(anio_actual, anio_actual - 5, -1)]
        anio_filtro = st.selectbox("Ejercicio Fiscal (Año de Venta)", opciones_anios, key="f_reporte_anio")
    
    if st.button("Calcular Impuestos (Ejecutar FIFO)"):
        # 1. Obtener Compras y Ventas de Supabase (Todas las históricas para no romper el inventario FIFO)
        q_compras = supabase.table("compras").select("*").order("fecha", desc=False)
        q_ventas = supabase.table("ventas").select("*").order("fecha", desc=False)
        
        if broker_filtro != "Todos":
            q_compras = q_compras.eq("broker", broker_filtro)
            q_ventas = q_ventas.eq("broker", broker_filtro)
            
        res_c = q_compras.execute()
        res_v = q_ventas.execute()
        
        if not res_c.data or not res_v.data:
            st.warning("Necesitas tener al menos una compra y una venta registrada para ejecutar el cálculo.")
        else:
            df_compras = pd.DataFrame(res_c.data)
            df_ventas = pd.DataFrame(res_v.data)
            
            # Convertir columnas numéricas
            for col in ["cantidad", "precio_usd", "comision_usd"]:
                df_compras[col] = pd.to_numeric(df_compras[col])
                df_ventas[col] = pd.to_numeric(df_ventas[col])
            
            resultados_fifo = []
            tickers = df_ventas["ticker"].unique()
            
            for t in tickers:
                compras_t = df_compras[df_compras["ticker"] == t].copy().to_dict('records')
                ventas_t = df_ventas[df_ventas["ticker"] == t].copy().to_dict('records')
                
                lotes = []
                for c in compras_t:
                    cant_c = float(c["cantidad"])
                    lotes.append({
                        "id": c["id"],
                        "fecha_compra": c["fecha"],
                        "broker": c["broker"],
                        "cantidad_inicial": cant_c,
                        "cantidad_disponible": cant_c,
                        "precio_compra_usd": float(c["precio_usd"]),
                        "comision_compra_usd": float(c.get("comision_usd", 0.0))
                    })
                
                for v in ventas_t:
                    cant_por_vender = float(v["cantidad"])
                    cant_vta_total = float(v["cantidad"])
                    precio_vta = float(v["precio_usd"])
                    comision_vta_total = float(v.get("comision_usd", 0.0))
                    fecha_vta = v["fecha"]
                    broker_vta = v["broker"]
                    
                    while cant_por_vender > 0 and len(lotes) > 0:
                        lote_actual = lotes[0]
                        cant_matcheada = min(cant_por_vender, lote_actual["cantidad_disponible"])
                        
                        # Prorrateo proporcional de comisiones por acción matcheada
                        comision_compra_prop = (lote_actual["comision_compra_usd"] / lote_actual["cantidad_inicial"]) * cant_matcheada
                        comision_venta_prop = (comision_vta_total / cant_vta_total) * cant_matcheada
                        
                        # Costo e Ingreso netos en USD (incluyendo comisiones)
                        costo_compra_usd = (cant_matcheada * lote_actual["precio_compra_usd"]) + comision_compra_prop
                        ingreso_venta_usd = (cant_matcheada * precio_vta) - comision_venta_prop
                        
                        # Conversión a MXN con Tipo de Cambio DOF
                        tc_compra = obtener_tc_dof(lote_actual["fecha_compra"])
                        tc_venta = obtener_tc_dof(fecha_vta)
                        
                        costo_compra_mxn_orig = costo_compra_usd * tc_compra
                        ingreso_venta_mxn = ingreso_venta_usd * tc_venta
                        
                        # Factor de actualización según Art. 129 LISR
                        factor_act = calcular_factor_inpc_sat(lote_actual["fecha_compra"], fecha_vta)
                        
                        costo_compra_mxn_ajustado = costo_compra_mxn_orig * factor_act
                        ganancia_mxn = ingreso_venta_mxn - costo_compra_mxn_ajustado
                        isr_10 = max(0.0, ganancia_mxn * 0.10)
                        
                        resultados_fifo.append({
                            "Ticker": t,
                            "Broker": broker_vta,
                            "Fecha Compra": lote_actual["fecha_compra"],
                            "Fecha Venta": fecha_vta,
                            "Cantidad": cant_matcheada,
                            "TC DOF Compra": tc_compra,
                            "TC DOF Venta": tc_venta,
                            "Costo Compra (MXN)": costo_compra_mxn_orig,
                            "Factor INPC": factor_act,
                            "Costo Ajustado (MXN)": costo_compra_mxn_ajustado,
                            "Ingreso Venta (MXN)": ingreso_venta_mxn,
                            "Ganancia/Pérdida (MXN)": ganancia_mxn,
                            "ISR 10% (MXN)": isr_10
                        })
                        
                        cant_por_vender = round(cant_por_vender - cant_matcheada, 9)
                        lote_actual["cantidad_disponible"] = round(lote_actual["cantidad_disponible"] - cant_matcheada, 9)
                        
                        if lote_actual["cantidad_disponible"] <= 0:
                            lotes.pop(0)
                            
                    if round(cant_por_vender, 9) > 0:
                        st.error(f"⚠️ Error: Intentas vender acciones de {t} sin registro de compra previo.")
            
            # 3. MOSTRAR Y FILTRAR RESULTADOS
            if resultados_fifo:
                df_res = pd.DataFrame(resultados_fifo)
                
                # FILTRO POR EJERCICIO FISCAL (AÑO DE VENTA)
                if anio_filtro != "Todos":
                    df_res = df_res[df_res["Fecha Venta"].str.startswith(str(anio_filtro))]
                
                if df_res.empty:
                    st.info(f"No hay ventas registradas para el Ejercicio Fiscal {anio_filtro}.")
                else:
                    st.success(f"¡Cálculo PEPS realizado con éxito para el Ejercicio Fiscal {anio_filtro}!")
                    
                    tot_ingreso = df_res["Ingreso Venta (MXN)"].sum()
                    tot_costo = df_res["Costo Ajustado (MXN)"].sum()
                    tot_ganancia = df_res["Ganancia/Pérdida (MXN)"].sum()
                    tot_isr = max(0.0, tot_ganancia * 0.10)
                    
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Ingresos Totales", f"${tot_ingreso:,.2f} MXN")
                    c2.metric("Costo Ajustado INPC", f"${tot_costo:,.2f} MXN")
                    c3.metric("Ganancia/Pérdida Neta", f"${tot_ganancia:,.2f} MXN")
                    c4.metric("ISR Estimado (10%)", f"${tot_isr:,.2f} MXN")
                    st.divider()
                    st.subheader(f"Desglose Fiscal de Ventas ({anio_filtro})")
                    st.dataframe(df_res, width="stretch",
                                 column_config={
                                     "Cantidad": st.column_config.NumberColumn(format="%.9f"),
                                     "Factor INPC": st.column_config.NumberColumn(format="%.5f"),
                                     "TC DOF Compra": st.column_config.NumberColumn(format="%.4f"),
                                     "TC DOF Venta": st.column_config.NumberColumn(format="%.4f"),
                                     "Costo Compra (MXN)": st.column_config.NumberColumn(format="$%.4f"),
                                     "Costo Ajustado (MXN)": st.column_config.NumberColumn(format="$%.4f"),
                                     "Ingreso Venta (MXN)": st.column_config.NumberColumn(format="$%.4f"),
                                     "Ganancia/Pérdida (MXN)": st.column_config.NumberColumn(format="$%.4f"),
                                     "ISR 10% (MXN)": st.column_config.NumberColumn(format="$%.4f"),
                                 }
                                )

# ==========================================
# PESTAÑA 5: Índices INPC (DOF)
# ==========================================
with tab_inpc:
    st.subheader("Índices INPC (DOF)")
    with st.expander("➕ Registrar Nuevo Valor INPC", expanded=False):
        with st.form("form_inpc", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            anio_actual = datetime.now().year
            with col1:
                anio_i = st.number_input("Año", min_value=2020, max_value=None, value=anio_actual, step=1)
            with col2:
                mes_i = st.number_input("Mes", min_value=1, max_value=12, value=1, step=1)
            with col3:
                valor_i = st.number_input("Valor INPC (DOF)", min_value=0.0, format="%.3f")
        
            btn_inpc = st.form_submit_button("Guardar / Actualizar INPC")
        
            if btn_inpc:
                if valor_i > 0:
                    datos_inpc = {
                        "anio": int(anio_i),
                        "mes": int(mes_i),
                        "valor": round(valor_i, 3)
                    }
                    # Guardar o actualizar en Supabase
                    supabase.table("inpc").upsert(datos_inpc).execute()
                
                    # Limpiar caché local para reflejar el nuevo valor en los reportes al instante
                    st.cache_data.clear()
                    st.success(f"INPC de {anio_i}-{str(mes_i).zfill(2)} ({valor_i}) guardado correctamente.")
                    st.rerun()
                else:
                    st.error("Por favor ingresa un valor de INPC válido.")

    st.divider()
    st.subheader("Historial de INPC Registrados")
    res_inpc = supabase.table("inpc").select("*").order("anio", desc=True).order("mes", desc=True).execute()
    if res_inpc.data:
        cols = ["anio", "mes", "valor"]
        df_inpc = pd.DataFrame(res_inpc.data)[cols]
        df_inpc = df_inpc.rename(columns={
            "anio": "Año",
            "mes": "Mes",
            "valor": "Valor INPC"
        })
        st.dataframe(df_inpc, width="stretch", hide_index=True)
    else:
        st.info("No hay registros de INPC en la base de datos.")
