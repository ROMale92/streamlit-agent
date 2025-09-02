
import streamlit as st
import pandas as pd
import io
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Analisi aderenza terapeutica (PDC) basata su DDD – Calcolo a intervalli (media sugli intervalli)") 

# UPLOAD FILE DISPENSAZIONI
file_disp = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"], key="disp")
# UPLOAD FILE DDD
file_ddd = st.file_uploader("📁 Carica file Excel con tabella DDD (ATC, DDD_standard)", type=["xlsx"], key="ddd")

if file_disp and file_ddd:
    df = pd.read_excel(file_disp)
    tab_ddd = pd.read_excel(file_ddd)
    st.success("✅ File caricati!")

    # FORM INPUT
    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            atc_col = st.selectbox("Colonna categoria terapeutica (es. ATC)", df.columns)
            ddd_col = st.selectbox("Colonna DDD dispensate", df.columns)
            atc_ddd_col = st.selectbox("Colonna ATC nella tabella DDD", tab_ddd.columns)
            ddd_std_col = st.selectbox("Colonna DDD_standard nella tabella DDD", tab_ddd.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            cutoff_naive = st.date_input("📅 Data indice (per selezionare naïve)")
            periodo = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=1825, value=365, step=30)
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        # PARSING DATE
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

        # MERGE con tabella DDD
        df = df.merge(tab_ddd[[atc_ddd_col, ddd_std_col]], left_on=atc_col, right_on=atc_ddd_col, how="left")
        df = df.rename(columns={ddd_std_col: "DDD_standard"})
        if df["DDD_standard"].isna().any():
            st.warning("⚠️ Attenzione: alcuni ATC non hanno corrispondenza nella tabella DDD.")

        # IDENTIFICA NAÏVE
        first_disp = df.groupby(id_col)[date_col].min().reset_index().rename(columns={date_col: "prima_data"})
        naive_ids = first_disp[first_disp["prima_data"] >= pd.to_datetime(cutoff_naive)][id_col]
        df = df[df[id_col].isin(naive_ids)]
        df = df.merge(first_disp, on=id_col, how="left")

        # Funzione per calcolo PDC a intervalli (media sugli intervalli)
        def calcola_pdc_paziente(s):
            s = s.sort_values(date_col)
            start = s[date_col].min()
            end = start + pd.Timedelta(days=int(periodo))

            ev = s[s[date_col] < end][[date_col, ddd_col, "DDD_standard"]].copy()
            ev["giorni_coperti"] = ev[ddd_col] / ev["DDD_standard"]
            ev = pd.concat([ev, pd.DataFrame([{date_col: end, "giorni_coperti": 0}])], ignore_index=True).sort_values(date_col)

            prev_date = start
            stock = 0.0
            numeratore = 0.0
            denominatore = 0.0

            for _, row in ev.iterrows():
                date = row[date_col]
                interval_len = (date - prev_date).days
                if interval_len > 0:
                    used = min(stock, interval_len)
                    pdc_int = used / interval_len
                    numeratore += pdc_int * interval_len
                    denominatore += interval_len
                    stock -= used
                stock += row["giorni_coperti"]
                prev_date = date

            pdc = numeratore / denominatore if denominatore > 0 else 0
            return pd.Series({
                "PDC": min(pdc, 1.0),
                "Durata": int(periodo),
                "ATC_principale": s[atc_col].mode().iloc[0] if not s[atc_col].mode().empty else None
            })

        # Applica calcolo a ogni paziente
        aderenza = df.groupby(id_col).apply(calcola_pdc_paziente).reset_index()
        aderenza["Aderente"] = aderenza["PDC"] >= 0.8

        # RIEPILOGO PER ATC
        riepilogo = aderenza.groupby("ATC_principale").agg({
            id_col: "count",
            "Aderente": "sum",
            "PDC": ["mean", "std", "min", "max"]
        })
        riepilogo.columns = ["N_pazienti", "N_aderenti", "PDC_medio", "PDC_std", "PDC_min", "PDC_max"]
        riepilogo = riepilogo.reset_index()
        riepilogo["%_aderenti"] = (riepilogo["N_aderenti"] / riepilogo["N_pazienti"] * 100).round(1)

        # MOSTRA RISULTATI
        st.subheader("📊 PDC per paziente")
        st.dataframe(aderenza)

        st.subheader("📊 Riepilogo per ATC")
        st.dataframe(riepilogo)

        # GRAFICO BOX PLOT PER ATC
        st.subheader("📈 Distribuzione PDC per ATC")
        fig = px.box(aderenza, x="ATC_principale", y="PDC", points="all",
                     title="Distribuzione PDC per categoria terapeutica",
                     labels={"PDC": "PDC"})
        st.plotly_chart(fig, use_container_width=True)

        # DOWNLOAD RISULTATI
        st.subheader("📥 Scarica risultati")
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            aderenza.to_excel(writer, index=False, sheet_name="PDC pazienti")
            riepilogo.to_excel(writer, index=False, sheet_name="Riepilogo ATC")
        st.download_button(
            label="💾 Scarica risultati (Excel)",
            data=buffer.getvalue(),
            file_name="risultati_aderenza.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
