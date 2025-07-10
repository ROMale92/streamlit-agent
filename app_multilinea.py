import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io

st.set_page_config(layout="wide")
st.title("Analisi prescrittiva farmaci – PDC con DDD WHO/AIFA")

# ─────────────────────────────────────────────────────────────
# 1) UPLOAD FILE DISPENSAZIONI
# ─────────────────────────────────────────────────────────────
file = st.file_uploader("① Carica file Excel con dispensazioni singole", type=["xlsx"])

# 2) UPLOAD (FACOLTATIVO) FILE DIZIONARIO DDD
ddd_csv = st.file_uploader(
    "② (Opzionale) Carica dizionario ATC → DDD standard (.csv con colonne ATC,DDD_standard)",
    type=["csv"]
)

# ─────────────────────────────────────────────────────────────
# 3) CARICA DATAFRAME DISPENSAZIONI
# ─────────────────────────────────────────────────────────────
if file:
    df = pd.read_excel(file)
    st.success("File dispensazioni caricato!")
    st.dataframe(df.head())

    # ————————————————————————————————————————————————————
    # 3a) Dizionario DDD
    # ————————————————————————————————————————————————————
    if ddd_csv:
        ddd_df = pd.read_csv(ddd_csv)
        diz_ddds = dict(zip(ddd_df["ATC"].astype(str).str.upper(),
                            ddd_df["DDD_standard"].astype(float)))
        st.info(f"Dizionario DDD caricato: {len(diz_ddds):,} ATC")
    else:
        st.warning("Nessun CSV caricato: uso dizionario DDD minimale di esempio.")
        diz_ddds = {
            "N05BA01": 10,  # Diazepam
            "C09AA05": 10,  # Enalapril
            "A10BA02": 2,   # Metformina
            "R03AC02": 0.4  # Salbutamolo
        }

    # ─────────────────────────────────────────────────────────
    # 4) FORM DI SETUP
    # ─────────────────────────────────────────────────────────
    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col    = st.selectbox("Colonna ID paziente", df.columns)
            cat_col   = st.selectbox("Colonna categoria terapeutica", df.columns)
            col_atc   = st.selectbox("Colonna codice ATC/farmaco", df.columns)
        with col2:
            date_col  = st.selectbox("Colonna data dispensazione", df.columns)
            col_ddds  = st.selectbox("Colonna DDD dispensate", df.columns)

            cutoff_date   = st.date_input("Data indice (pazienti naïve)")
            followup_date = st.date_input("Data cut-off follow-up",
                                          value=pd.to_datetime("2024-09-30"))

        ex_col  = st.selectbox("Colonna sesso", df.columns)
        age_col = st.selectbox("Colonna età", df.columns)

        submit = st.form_submit_button("Esegui analisi")

    # ─────────────────────────────────────────────────────────
    # 5) ELABORAZIONE
    # ─────────────────────────────────────────────────────────
    if submit:
        # Date & numeri
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df[col_ddds] = pd.to_numeric(df[col_ddds], errors="coerce")

        # Filtra record validi
        df = df.dropna(subset=[date_col, col_ddds])

        # Pazienti naïve
        first_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids  = first_disp[first_disp[date_col] >= pd.to_datetime(cutoff_date)][id_col]
        df = df[df[id_col].isin(naive_ids)].copy()

        # Linee terapeutiche
        df = df.sort_values([id_col, date_col])
        df["Linea"] = (df.groupby(id_col)[cat_col]
                         .transform(lambda x: x.ne(x.shift()).cumsum())
                       ).astype(str)
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"] + ")"

        # Esito follow-up
        last_dates = df.groupby(id_col)[date_col].max().reset_index()
        last_dates["Esito"] = last_dates[date_col].apply(
            lambda x: "In trattamento"
            if x >= pd.to_datetime(followup_date) else "Perso al follow-up"
        )
        df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

        # ─────────────────────────────────────────────
        # 5a) PDC con DDD
        # ─────────────────────────────────────────────
        df["ATC_std"]       = df[col_atc].astype(str).str.upper()
        df["DDD_standard"]  = df["ATC_std"].map(diz_ddds)
        df["Giorni_coperti"] = df[col_ddds] / df["DDD_standard"]

        tratt = (df.groupby(id_col)
                   .agg(Inizio=(date_col, "min"),
                        Fine=(date_col, "max"),
                        Giorni_coperti_tot=("Giorni_coperti", "sum"))
                   .reset_index())
        tratt["Durata_osserv"] = (tratt["Fine"] - tratt["Inizio"]).dt.days + 1
        tratt["PDC"] = tratt["Giorni_coperti_tot"] / tratt["Durata_osserv"]
        tratt["Aderente_≥0.8"] = tratt["PDC"] >= 0.8

        # Aggiungi categoria terapeutica
        tratt = tratt.merge(df[[id_col, cat_col]].drop_duplicates(),
                            on=id_col, how="left")

        # ─────────────────────────────────────────────
        # 6) OUTPUT
        # ─────────────────────────────────────────────
        st.subheader("Sintesi aderenza (PDC basata su DDD)")
        tab_adh = (tratt.groupby(cat_col)
                         .agg(mean_PDC=("PDC", "mean"),
                              sd_PDC=("PDC", "std"),
                              N_pazienti=(id_col, "count"),
                              N_aderenti=("Aderente_≥0.8", "sum"))
                         .reset_index())
        tab_adh["% aderenti"] = (tab_adh["N_aderenti"] /
                                 tab_adh["N_pazienti"] * 100).round(2)
        st.dataframe(tab_adh)

        st.subheader("Distribuzione PDC per categoria")
        fig_pdc = px.box(tratt, x=cat_col, y="PDC", points="all",
                         title="Boxplot PDC con DDD")
        st.plotly_chart(fig_pdc, use_container_width=True)

        # ─────────────────────────────────────────────
        # Sankey semplificato linee (opzionale)
        # ─────────────────────────────────────────────
        st.subheader("Flussi terapeutici (Sankey rapido)")
        sankey_data = []
        max_line = df["Linea"].astype(int).max()
        for i in range(1, max_line):
            step = df[df["Linea"].isin([str(i), str(i+1)])]
            piv  = (step.pivot_table(index=id_col, columns="Linea",
                                     values="Terapia", aggfunc="first")
                          .dropna())
            if not piv.empty:
                sankey_data.append(
                    piv.groupby([str(i), str(i+1)]).size()
                       .reset_index(name="Count")
                )

        if sankey_data:
            sankey_df = pd.concat(sankey_data)
            labels = pd.concat([sankey_df.iloc[:, 0],
                                sankey_df.iloc[:, 1]]).unique().tolist()
            sankey_df["source"] = sankey_df[sankey_df.columns[0]].apply(labels.index)
            sankey_df["target"] = sankey_df[sankey_df.columns[1]].apply(labels.index)
            fig = go.Figure(go.Sankey(
                node=dict(label=labels, pad=15, thickness=20),
                link=dict(source=sankey_df["source"],
                          target=sankey_df["target"],
                          value=sankey_df["Count"])
            ))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Non abbastanza dati per un Sankey.")

        # ─────────────────────────────────────────────
        # Download Excel
        # ─────────────────────────────────────────────
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Dati grezzi")
            tratt.to_excel(writer, index=False, sheet_name="PDC_DDD")
            tab_adh.to_excel(writer, index=False, sheet_name="Sintesi")
        st.download_button("Scarica risultati Excel",
                           excel_buffer.getvalue(),
                           file_name="analisi_pdc_ddd.xlsx")
