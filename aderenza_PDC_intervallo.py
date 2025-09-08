import pandas as pd
import streamlit as st

st.title("📊 Calcolo aderenza a intervalli (tipo Access)")

# Upload file dispensazioni
file = st.file_uploader("Carica il file delle dispensazioni", type=["xlsx", "csv"])
ddd_file = st.file_uploader("Carica il file dizionario DDD standard", type=["xlsx", "csv"])

# Parametro: periodo osservazione
period_days = st.number_input("Periodo di osservazione (giorni)", 
                              min_value=30, max_value=2000, value=365, step=30)

if file and ddd_file:
    # Lettura file
    if file.name.endswith(".xlsx"):
        disp = pd.read_excel(file)
    else:
        disp = pd.read_csv(file, sep=";")

    if ddd_file.name.endswith(".xlsx"):
        ddd_lookup = pd.read_excel(ddd_file)
    else:
        ddd_lookup = pd.read_csv(ddd_file, sep=";")

    # Parsing date
    disp["Data erogazione"] = pd.to_datetime(
        disp["Data erogazione"], dayfirst=True, errors="coerce"
    )
    disp = disp.dropna(subset=["Data erogazione"])

    # Join con DDD standard
    disp = disp.merge(
        ddd_lookup[["Codice AIC", "DDD_standard_giornaliera"]],
        on="Codice AIC",
        how="left"
    )

    # Dedup per stesso giorno/paziente/principio
    disp = (
        disp.groupby(
            ["Cod Fiscale Assistito", "Principio Attivo", "Codice AIC", "Data erogazione"],
            as_index=False
        )
        .agg({"DDD erogate": "sum", "DDD_standard_giornaliera": "first"})
    )

    # Giorni coperti per dispensazione
    disp["giorni_coperti_disp"] = disp["DDD erogate"] / disp["DDD_standard_giornaliera"]

    # Calcolo intervalli refill-to-refill
    out_rows = []
    for (cf, pa), g in disp.sort_values("Data erogazione").groupby(
        ["Cod Fiscale Assistito", "Principio Attivo"]
    ):
        t0 = g["Data erogazione"].min()
        fine = t0 + pd.Timedelta(days=period_days)
        g = g[g["Data erogazione"].between(t0, fine)].copy()
        if g.empty:
            continue
        g = g.sort_values("Data erogazione").reset_index(drop=True)
        for i, row in g.iterrows():
            start_i = row["Data erogazione"]
            if i < len(g) - 1:
                next_date = g.loc[i + 1, "Data erogazione"]
            else:
                next_date = fine
            end_i = min(next_date, fine)
            delta_i = (end_i - start_i).days
            if delta_i <= 0:
                continue
            covered_i = min(row["giorni_coperti_disp"], delta_i)
            adh_i = covered_i / delta_i
            out_rows.append({
                "Cod Fiscale Assistito": cf,
                "Principio Attivo": pa,
                "start": start_i,
                "end": end_i,
                "delta_days": delta_i,
                "ADH_interval": adh_i
            })

    intervals = pd.DataFrame(out_rows)

    adh_patient = (
        intervals.groupby(["Cod Fiscale Assistito", "Principio Attivo"], as_index=False)
        .agg(ADH_anno=("ADH_interval", "mean"))
    )

    st.subheader("📂 Aderenza paziente × principio (media intervalli)")
    st.dataframe(adh_patient)

    # Media per ATC (se presente nel file)
    if "ATC" in disp.columns:
        atc_map = (
            disp.sort_values("Data erogazione")
            .groupby(["Cod Fiscale Assistito", "Principio Attivo"])["ATC"]
            .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
            .reset_index()
        )
        adh_patient = adh_patient.merge(
            atc_map, on=["Cod Fiscale Assistito", "Principio Attivo"], how="left"
        )
        media_per_ATC = adh_patient.groupby("ATC")["ADH_anno"].mean().reset_index()
        st.subheader("📊 Media aderenza per ATC")
        st.dataframe(media_per_ATC)

        st.download_button(
            "⬇️ Scarica risultati per ATC",
            data=media_per_ATC.to_csv(index=False).encode("utf-8"),
            file_name="aderenza_media_per_ATC.csv",
            mime="text/csv"
        )
