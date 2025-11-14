import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from lifelines import KaplanMeierFitter, CoxPHFitter

# ----------------------------
# FUNZIONI DI SUPPORTO
# ----------------------------

def prepara_coorte(df, global_end_str="2025-02-28", landmark_days=365):
    """
    Costruisce la coorte time-to-event con:
    - una riga per paziente
    - MMI NA -> 0
    - follow-up amministrativo fino a global_end
    - landmark a 12 mesi: include tutti gli eventi + censurati con follow-up >= 365 giorni
    """

    # Nomi colonne attesi (adattali se nel tuo file sono diversi)
    col_id = "cf anonimo"
    col_eta = "MinDiEtà Assistito"
    col_sesso = "Sesso Assistito"
    col_min_erog = "MinDiData erogazione"
    col_max_erog = "MaxDiData erogazione"
    col_tratt = "CATEGORIA TERAP"
    col_adh = "MediaDiADH_anno"
    col_ricovero = "DATA RICOVERO"
    col_dim = "DATA DIMISSIONE"
    col_mmi = "MMI"

    # Conversione date
    for c in [col_min_erog, col_max_erog, col_ricovero, col_dim]:
        df[c] = pd.to_datetime(df[c], errors="coerce")

    # Ordina e prendi prima riga per trattamento iniziale
    df_sorted = df.sort_values([col_id, col_min_erog])
    first_rows = df_sorted.groupby(col_id, as_index=False).first()

    # Aggrega per paziente
    agg = df.groupby(col_id).agg(
        index_date=(col_min_erog, "min"),
        max_date=(col_max_erog, "max"),
        aderenza=(col_adh, "mean"),
        data_ricovero=(col_ricovero, "min"),
        MMI=(col_mmi, "max")
    ).reset_index()

    patients = first_rows[[col_id, col_eta, col_sesso, col_tratt]].merge(
        agg, on=col_id, how="left"
    )

    # Rinomina variabili
    patients = patients.rename(columns={
        col_id: "id",
        col_eta: "eta",
        col_sesso: "sesso",
        col_tratt: "trattamento"
    })

    # MMI NA -> 0 (come da tua indicazione)
    patients["MMI"] = patients["MMI"].fillna(0)

    # Fine follow-up globale
    global_end = pd.to_datetime(global_end_str)
    patients["followup_end"] = patients["max_date"].clip(upper=global_end)

    # Tieni solo casi con date valide
    patients = patients[
        patients["index_date"].notna() &
        patients["followup_end"].notna() &
        (patients["followup_end"] >= patients["index_date"])
    ].copy()

    # Definizione evento e tempo
    patients["event"] = np.where(
        (patients["data_ricovero"].notna()) &
        (patients["data_ricovero"] <= patients["followup_end"]),
        1, 0
    )

    patients["time"] = np.where(
        patients["event"] == 1,
        (patients["data_ricovero"] - patients["index_date"]).dt.days,
        (patients["followup_end"] - patients["index_date"]).dt.days
    ).astype(float)

    # Rimuovi tempi non positivi
    patients = patients[patients["time"] > 0].copy()

    # Landmark: includi tutti gli eventi + censurati con time >= landmark_days
    include_mask = (patients["event"] == 1) | (
        (patients["event"] == 0) & (patients["time"] >= landmark_days)
    )
    landmark_df = patients[include_mask].copy()

    # Tronca il follow-up a landmark_days (365)
    landmark_df["time"] = np.where(
        landmark_df["time"] > landmark_days,
        landmark_days,
        landmark_df["time"]
    ).astype(float)

    return landmark_df


def stima_km_landmark(df, landmark_days=365):
    """
    Curve KM per trattamento, follow-up troncato a landmark_days.
    """
    kmf = KaplanMeierFitter()

    fig, ax = plt.subplots()

    for trt, sub in df.groupby("trattamento"):
        kmf.fit(durations=sub["time"], event_observed=sub["event"], label=str(trt))
        kmf.plot_survival_function(ax=ax)

    ax.set_xlabel("Tempo (giorni)")
    ax.set_ylabel("Probabilità di non essere ricoverato")
    ax.set_title(f"Curve di Kaplan–Meier (Landmark {landmark_days} giorni)")
    ax.grid(True)

    return fig


def stima_cox(df):
    """
    Modello di Cox:
    Surv(time, event) ~ trattamento + eta + sesso + aderenza + MMI
    """
    df = df.copy()

    # Categoriali -> dummies
    df["trattamento"] = df["trattamento"].astype("category")
    df["sesso"] = df["sesso"].astype("category")

    # One-hot encoding (reference = prima categoria)
    X = pd.get_dummies(df[["trattamento", "sesso"]], drop_first=True)
    X["eta"] = df["eta"]
    X["aderenza"] = df["aderenza"]
    X["MMI"] = df["MMI"]

    y = df[["time", "event"]]

    cph_df = pd.concat([y, X], axis=1)

    cph = CoxPHFitter()
    cph.fit(cph_df, duration_col="time", event_col="event")

    return cph, cph_df


def curve_aggiustate(cph, cph_df, df, landmark_days=365):
    """
    Curve di sopravvivenza aggiustate per trattamento,
    stimando a valori medi di eta, aderenza, MMI e sesso medio.
    """
    # Trova nomi delle colonne dei dummies
    covariates = cph.params_.index.tolist()

    # Medie delle covariate
    mean_eta = df["eta"].mean()
    mean_adh = df["aderenza"].mean()
    mean_mmi = df["MMI"].mean()

    # sesso_M potrebbe o no esistere a seconda dei dati
    has_sesso_M = any(col.startswith("sesso_") for col in covariates)
    sesso_dummy_cols = [c for c in covariates if c.startswith("sesso_")]
    mean_sesso = {}
    for col in sesso_dummy_cols:
        orig = col.replace("sesso_", "")
        mean_sesso[col] = (df["sesso"] == orig).mean()

    # Lista trattamenti
    trattamenti = sorted(df["trattamento"].unique())

    # Costruisci profili per ciascun trattamento
    fig, ax = plt.subplots()

    timeline = np.linspace(0, landmark_days, 200)

    for trt in trattamenti:
        # Costruisco una singola riga di covariate
        row = {}

        # Trattamento dummies
        for col in covariates:
            if col.startswith("trattamento_"):
                # esempio: trattamento_DPP4i
                trt_name = col.replace("trattamento_", "")
                row[col] = 1.0 if trt == trt_name else 0.0

        # Sesso dummies (a valori medi)
        for col, val in mean_sesso.items():
            row[col] = val

        # Variabili continue
        if "eta" in covariates:
            row["eta"] = mean_eta
        if "aderenza" in covariates:
            row["aderenza"] = mean_adh
        if "MMI" in covariates:
            row["MMI"] = mean_mmi

        row_df = pd.DataFrame([row])

        surv_func = cph.predict_survival_function(row_df, times=timeline)
        ax.plot(timeline, surv_func.iloc[:, 0], label=str(trt))

    ax.set_xlabel("Tempo (giorni)")
    ax.set_ylabel("Probabilità di non essere ricoverato (aggiustata)")
    ax.set_title(f"Curve di sopravvivenza aggiustate (Cox) - Landmark {landmark_days} giorni")
    ax.grid(True)
    ax.legend()

    return fig


# ----------------------------
# APP STREAMLIT
# ----------------------------

def main():
    st.title("Analisi di sopravvivenza real-world - Diabete tipo 2")
    st.write("Kaplan–Meier, Cox, Landmark a 12 mesi, curve aggiustate per trattamento.")

    uploaded_file = st.file_uploader("Carica il file Excel", type=["xlsx", "xls"])

    if uploaded_file is None:
        st.info("Carica il file Excel con le colonne previste (come nel tuo dataset).")
        return

    # Leggi file
    df = pd.read_excel(uploaded_file)

    st.subheader("Anteprima del dataset")
    st.write(df.head())

    # Parametri
    landmark_days = 365
    global_end_str = "2025-02-28"

    # Prepara coorte
    st.subheader("Costruzione coorte e Landmark 12 mesi")
    landmark_df = prepara_coorte(df, global_end_str=global_end_str, landmark_days=landmark_days)

    n_paz = landmark_df["id"].nunique()
    n_eventi = landmark_df["event"].sum()

    st.write(f"**Pazienti inclusi (Landmark 12 mesi)**: {n_paz}")
    st.write(f"**Eventi (primo ricovero)**: {n_eventi}")

    st.write("Distribuzione per trattamento:")
    st.write(landmark_df.groupby("trattamento")["id"].nunique())

    # KM curves
    st.subheader("Curve di Kaplan–Meier (non aggiustate)")
    fig_km = stima_km_landmark(landmark_df, landmark_days=landmark_days)
    st.pyplot(fig_km)

    # Cox model
    st.subheader("Modello di Cox (aggiustato)")
    cph, cph_df = stima_cox(landmark_df)

    st.write("**Risultati del modello di Cox:**")
    st.text(cph.summary().to_string())

    # HR table più leggibile
    hr_table = cph.summary().copy()
    hr_table["HR"] = np.exp(hr_table["coef"])
    hr_table["HR_lower_95"] = np.exp(hr_table["coef"] - 1.96*hr_table["se(coef)"])
    hr_table["HR_upper_95"] = np.exp(hr_table["coef"] + 1.96*hr_table["se(coef)"])
    hr_table_simple = hr_table[["HR", "HR_lower_95", "HR_upper_95", "p"]].round(3)

    st.write("**Hazard ratio (HR) con IC95%:**")
    st.dataframe(hr_table_simple)

    # Adjusted survival curves
    st.subheader("Curve di sopravvivenza aggiustate per trattamento")
    fig_adj = curve_aggiustate(cph, cph_df, landmark_df, landmark_days=landmark_days)
    st.pyplot(fig_adj)

    st.markdown("---")
    st.markdown("App basata su Streamlit + lifelines. Modello: Cox PH, endpoint = primo ricovero, landmark = 12 mesi.")


if __name__ == "__main__":
    main()
