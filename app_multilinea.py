
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io

st.set_page_config(layout="wide")
st.title("Analisi prescrittiva farmaci - da dispensazioni singole")

file = st.file_uploader("Carica file Excel con dispensazioni singole", type=["xlsx"])
if file:
    df = pd.read_excel(file)
    st.success("File caricato correttamente!")
    st.dataframe(df.head())

    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            cutoff_date = st.date_input("Data indice (cut-off naïve)")
            followup_cutoff = st.date_input("Data cut-off follow-up", value=pd.to_datetime("2024-09-30"))

        submit = st.form_submit_button("Procedi")

    if submit:
        # Selezione manuale colonne per Tabella 1
st.markdown("### Tabella 1 - Caratteristiche pazienti")

with st.expander("Seleziona colonne per Tabella 1"):
    col1, col2 = st.columns(2)
    with col1:
        sex_col = st.selectbox("Colonna per il sesso", options=df.columns, index=0)
    with col2:
        age_col = st.selectbox("Colonna per l'età", options=df.columns, index=1)

if sex_col in df.columns and age_col in df.columns:
    table1 = df.groupby("Terapia").agg(
        Numero_pazienti=(id_col, "nunique"),
        Percentuale_maschi=(sex_col, lambda x: round((x == "M").mean() * 100, 2)),
        Età_mediana=(age_col, "median"),
        Età_minima=(age_col, "min"),
        Età_massima=(age_col, "max")
    ).reset_index()

    st.dataframe(table1)
else:
    st.info("Aggiungi le colonne 'Sesso' e 'Età' per visualizzare la Tabella 1.")

        df[date_col] = pd.to_datetime(df[date_col], errors='coerce', dayfirst=True)
        df = df.dropna(subset=[date_col])
        first_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = first_disp[first_disp[date_col] >= pd.to_datetime(cutoff_date)][id_col]
        df = df[df[id_col].isin(naive_ids)]

        df = df.sort_values([id_col, date_col])
        df["Linea"] = (
            df.groupby(id_col)[cat_col]
              .transform(lambda x: x.ne(x.shift()).cumsum())
        )
        df["Linea"] = df["Linea"].astype(str)
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"] + ")"

        last_dates = df.groupby(id_col)[date_col].max().reset_index()
        last_dates["Esito"] = last_dates[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime(followup_cutoff) else "Perso al follow-up"
        )
        df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

        # Sankey su tutte le linee
        st.subheader("Flussi terapeutici (Sankey)")
        sankey_data = []
        max_line = df["Linea"].astype(int).max()
        for i in range(1, max_line):
            step = df[df["Linea"].isin([str(i), str(i + 1)])]
            step_pivot = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
            step_df = step_pivot.groupby([str(i), str(i + 1)]).size().reset_index(name="Count")
            sankey_data.append(step_df)

        last_step = df.groupby(id_col).agg({"Linea": "max", "Terapia": "last", "Esito": "last"}).reset_index()
        last_df = last_step.groupby(["Terapia", "Esito"]).size().reset_index(name="Count")
        sankey_data.append(last_df.rename(columns={"Terapia": str(max_line + 1), "Esito": str(max_line + 2)}))

        sankey_df = pd.concat(sankey_data)
        all_labels = pd.concat([sankey_df.iloc[:, 0], sankey_df.iloc[:, 1]]).unique().tolist()
        sankey_df["source"] = sankey_df[sankey_df.columns[0]].apply(lambda x: all_labels.index(x))
        sankey_df["target"] = sankey_df[sankey_df.columns[1]].apply(lambda x: all_labels.index(x))

        fig = go.Figure(go.Sankey(
            node=dict(label=all_labels, pad=15, thickness=20),
            link=dict(source=sankey_df["source"], target=sankey_df["target"], value=sankey_df["Count"])
        ))
        st.plotly_chart(fig, use_container_width=True)

        # Selezione linee da analizzare
        st.subheader("Analisi dettagliata per linea")
        linee_dispo = sorted(df["Linea"].unique())
        linee_sel = st.multiselect("Seleziona le linee da analizzare", linee_dispo, default=["1"])
        df_linee = df[df["Linea"].isin(linee_sel)]

        # Tabella 1
        st.subheader("Tabella 1 - Caratteristiche pazienti")
        if "Sesso" in df.columns and "Età" in df.columns:
            tab1 = df_linee.groupby(cat_col).agg(
                Numero_pazienti=(id_col, "nunique"),
                Percentuale_maschi=("Sesso", lambda x: round((x == 'M').mean()*100, 2)),
                Età_mediana=("Età", "median"),
                Età_minima=("Età", "min"),
                Età_massima=("Età", "max")
            ).reset_index()
            totale = tab1["Numero_pazienti"].sum()
            tab1["Percentuale_pazienti"] = round(tab1["Numero_pazienti"] / totale * 100, 2)
            st.dataframe(tab1)
        else:
            st.warning("Aggiungi le colonne 'Sesso' e 'Età' per visualizzare la Tabella 1.")

        # Persistenza
        persist = df_linee.groupby([id_col]).agg(
            Prima_disp=(date_col, "min"),
            Ultima_disp=(date_col, "max")
        ).reset_index()
        persist["Durata_trattamento"] = (persist["Ultima_disp"] - persist["Prima_disp"]).dt.days

        # Aderenza (PDC)
        pdc = df_linee.copy()
        pdc["Mese"] = pdc[date_col].dt.to_period("M")
        pdc_stats = (
            pdc.groupby(id_col)["Mese"]
            .nunique()
            .reset_index(name="Mesi_coperti")
        )
        pdc_stats["Durata_mesi"] = persist["Durata_trattamento"].apply(lambda x: max(1, x // 30 + 1))
        pdc_stats["PDC"] = pdc_stats["Mesi_coperti"] / pdc_stats["Durata_mesi"]
        pdc_stats["Aderente_>=0.8"] = pdc_stats["PDC"] >= 0.8

        pdc_merged = pdc_stats.merge(df_linee[[id_col, cat_col]].drop_duplicates(), on=id_col)
        tab_adh = pdc_merged.groupby(cat_col).agg(
            mean=("PDC", "mean"),
            sd=("PDC", "std"),
            N_totali=(id_col, "count"),
            N_aderenti=("Aderente_>=0.8", "sum")
        ).reset_index()
        tab_adh["%_aderenti"] = round(tab_adh["N_aderenti"] / tab_adh["N_totali"] * 100, 2)
        st.subheader("Aderenza PDC")
        st.dataframe(tab_adh)

        # Esporta tutto in Excel
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Tutti i dati")
            df_linee.to_excel(writer, index=False, sheet_name="Linee selezionate")
            persist.to_excel(writer, index=False, sheet_name="Persistenza")
            if 'tab1' in locals():
                tab1.to_excel(writer, index=False, sheet_name="Tabella1")
            tab_adh.to_excel(writer, index=False, sheet_name="Aderenza")
        st.download_button("Scarica risultati in Excel", excel_buffer.getvalue(), file_name="risultati_analisi.xlsx")
