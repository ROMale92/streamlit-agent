import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import io

st.set_page_config(layout="wide")
st.title("Analisi prescrittiva farmaci – PDC con DDD")

file = st.file_uploader("① Carica file Excel con dispensazioni singole", type=["xlsx"])
ddd_file = st.file_uploader("② Carica file con DDD (es. atc_diabe_con_ddd.xlsx)", type=["xlsx"])

if file and ddd_file:
    df = pd.read_excel(file)
    ddd_df = pd.read_excel(ddd_file)
    st.success("File caricati correttamente!")
    st.dataframe(df.head())

    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna ID paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica (es. ATC)", df.columns)
            atc_col = st.selectbox("Colonna ATC per mappatura DDD", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            ddd_disp_col = st.selectbox("Colonna DDD dispensate", df.columns)
            cutoff_date = st.date_input("Data indice (naïve)")
            followup_date = st.date_input("Data cut-off follow-up")
        ex_col = st.selectbox("Colonna sesso", df.columns)
        age_col = st.selectbox("Colonna età", df.columns)
        submit = st.form_submit_button("Esegui analisi")

    if submit:
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        df[ddd_disp_col] = pd.to_numeric(df[ddd_disp_col], errors='coerce')
        df = df.dropna(subset=[date_col, ddd_disp_col])

        # DDD mappata
        ddd_map = dict(zip(ddd_df['ATC'], ddd_df['DDD_standard']))
        df["DDD_standard"] = df[atc_col].map(ddd_map)
        df["Giorni_coperti"] = df[ddd_disp_col] / df["DDD_standard"]

        # Filtra naïve
        first_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = first_disp[first_disp[date_col] >= pd.to_datetime(cutoff_date)][id_col]
        df = df[df[id_col].isin(naive_ids)].copy()

        # Linee terapeutiche
        df = df.sort_values([id_col, date_col])
        df["Linea"] = df.groupby(id_col)[cat_col].transform(lambda x: x.ne(x.shift()).cumsum()).astype(str)
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"] + ")"

        # Esito
        last_disp = df.groupby(id_col)[date_col].max().reset_index()
        last_disp["Esito"] = last_disp[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime(followup_date) else "Perso al follow-up"
        )
        df = df.merge(last_disp[[id_col, "Esito"]], on=id_col, how="left")

        # Sankey
        st.subheader("Flussi terapeutici (Sankey)")
        sankey_data = []
        max_line = df["Linea"].astype(int).max()
        for i in range(1, max_line):
            step = df[df["Linea"].isin([str(i), str(i + 1)])]
            piv = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
            if not piv.empty:
                sankey_data.append(piv.groupby([str(i), str(i + 1)]).size().reset_index(name="Count"))
        if sankey_data:
            sankey_df = pd.concat(sankey_data)
            labels = pd.concat([sankey_df.iloc[:, 0], sankey_df.iloc[:, 1]]).unique().tolist()
            sankey_df["source"] = sankey_df[sankey_df.columns[0]].apply(labels.index)
            sankey_df["target"] = sankey_df[sankey_df.columns[1]].apply(labels.index)
            fig_sankey = go.Figure(go.Sankey(
                node=dict(label=labels, pad=15, thickness=20),
                link=dict(source=sankey_df["source"], target=sankey_df["target"], value=sankey_df["Count"])
            ))
            st.plotly_chart(fig_sankey, use_container_width=True)

        # Selezione linee
        st.subheader("Analisi per linea terapeutica")
        sel_linee = st.multiselect("Seleziona linee da analizzare", sorted(df["Linea"].unique()), default=["1"])
        df_linee = df[df["Linea"].isin(sel_linee)]

        # Tabella 1
        st.subheader("Tabella 1 – Caratteristiche pazienti")
        tab1 = df_linee.groupby(cat_col).agg(
            N_pazienti=(id_col, "nunique"),
            Perc_maschi=(ex_col, lambda x: round((x == 'M').mean() * 100, 2)),
            Età_mediana=(age_col, "median"),
            Età_minima=(age_col, "min"),
            Età_massima=(age_col, "max")
        ).reset_index()
        st.dataframe(tab1)

        # Persistenza
        persist = df_linee.groupby(id_col).agg(
            Prima_disp=(date_col, "min"),
            Ultima_disp=(date_col, "max")
        ).reset_index()
        persist["Durata_trattamento"] = (persist["Ultima_disp"] - persist["Prima_disp"]).dt.days + 1

        # Aderenza (PDC)
        aderenza = df_linee.groupby(id_col).agg(
            DDD_totali=("Giorni_coperti", "sum"),
            Prima_disp=(date_col, "min"),
            Ultima_disp=(date_col, "max")
        ).reset_index()
        aderenza["Durata"] = (aderenza["Ultima_disp"] - aderenza["Prima_disp"]).dt.days + 1
        aderenza["PDC"] = aderenza["DDD_totali"] / aderenza["Durata"]
        aderenza["Aderente"] = aderenza["PDC"] >= 0.8
        aderenza = aderenza.merge(df_linee[[id_col, cat_col]].drop_duplicates(), on=id_col, how="left")

        # Tabella aderenza
        st.subheader("Aderenza (PDC da DDD)")
        tab_adh = aderenza.groupby(cat_col).agg(
            media_PDC=("PDC", "mean"),
            sd_PDC=("PDC", "std"),
            N_pazienti=(id_col, "count"),
            N_aderenti=("Aderente", "sum")
        ).reset_index()
        tab_adh["%_aderenti"] = round(tab_adh["N_aderenti"] / tab_adh["N_pazienti"] * 100, 2)
        st.dataframe(tab_adh)

        # Boxplot PDC
        fig_pdc = px.box(aderenza, x=cat_col, y="PDC", points="all", title="Distribuzione PDC per categoria")
        st.plotly_chart(fig_pdc, use_container_width=True)

        # Download Excel
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Dati grezzi")
            df_linee.to_excel(writer, index=False, sheet_name="Linee selezionate")
            tab1.to_excel(writer, index=False, sheet_name="Tabella1")
            persist.to_excel(writer, index=False, sheet_name="Persistenza")
            aderenza.to_excel(writer, index=False, sheet_name="PDC")
            tab_adh.to_excel(writer, index=False, sheet_name="Sintesi Aderenza")
        st.download_button("Scarica risultati in Excel", data=excel_buffer.getvalue(), file_name="analisi_aderenza_ddd.xlsx")
