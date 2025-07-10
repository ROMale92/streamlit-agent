    if submit:
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce', dayfirst=True)
        df = df.dropna(subset=[date_col])

        # Selezione colonne per sesso ed età
        st.markdown("### Tabella 1 - Caratteristiche pazienti")
        with st.expander("Seleziona colonne per Tabella 1"):
            col1, col2 = st.columns(2)
            with col1:
                sex_col = st.selectbox("Colonna per il sesso", options=df.columns)
            with col2:
                age_col = st.selectbox("Colonna per l'età", options=df.columns)

        # Filtro pazienti naïve
        first_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = first_disp[first_disp[date_col] >= pd.to_datetime(cutoff_date)][id_col]
        df = df[df[id_col].isin(naive_ids)]

        # Linee di trattamento
        df = df.sort_values([id_col, date_col])
        df["Linea"] = df.groupby(id_col)[cat_col].transform(lambda x: x.ne(x.shift()).cumsum())
        df["Linea"] = df["Linea"].astype(str)
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"] + ")"

        # Esito
        last_dates = df.groupby(id_col)[date_col].max().reset_index()
        last_dates["Esito"] = last_dates[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime(followup_cutoff)
