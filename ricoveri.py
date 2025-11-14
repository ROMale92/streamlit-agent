import streamlit as st
import pandas as pd
import numpy as np
import altair as alt

from statsmodels.duration.hazard_regression import PHReg


# ========== FUNZIONI DI SUPPORTO ==========

def prepara_coorte(df, global_end_str="2025-02-28", landmark_days=365):
    """
    Costruisce la coorte time-to-event con:
    - una riga per paziente
    - MMI NA -> 0
    - follow-up amministrativo fino a global_end
    - landmark: include tutti gli eventi + censurati con follow-up >= landmark_days
    - tronca il follow-up a landmark_days
    """

    # nomi colonne come nel tuo file
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

    # conversione date
    for c in [col_min_erog, col_max_erog, col_ricovero, col_dim]:
        df[c] = pd.to_datetime(df[c], errors="coerce")

    # ordina e prendi prima riga = trattamento iniziale
    df_sorted = df.sort_values([col_id, col_min_erog])
    first_rows = df_sorted.groupby(col_id, as_index=False).first()

    # aggrega per paziente
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

    patients = patients.rename(columns={
        col_id: "id",
        col_eta: "eta",
        col_sesso: "sesso",
        col_tratt: "trattamento"
    })

    # MMI mancante -> 0
    patients["MMI"] = patients["MMI"].fillna(0)

    # fine follow-up globale
    global_end = pd.to_datetime(global_end_str)
    patients["followup_end"] = patients["max_date"].clip(upper=global_end)

    # filtra date impossibili
    patients = patients[
        patients["index_date"].notna() &
        patients["followup_end"].notna() &
        (patients["followup_end"] >= patients["index_date"])
    ].copy()

    # evento + tempo
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

    patients = patients[patients["time"] > 0].copy()

    # LANDMARK: includi tutti gli eventi + censurati con follow-up >= landmark_days
    include_mask = (patients["event"] == 1) | (
        (patients["event"] == 0) & (patients["time"] >= landmark_days)
    )
    landmark_df = patients[include_mask].copy()

    # tronca il follow-up a landmark_days
    landmark_df["time"] = np.where(
        landmark_df["time"] > landmark_days,
        landmark_days,
        landmark_df["time"]
    ).astype(float)

    return landmark_df


def km_curves_df(df, landmark_days=365):
    """
    Calcola le curve KM manualmente e restituisce un DataFrame
    (time, surv, trattamento) da plottare con Altair.
    """

    def km_estimate(times, events):
        # ordina
        order = np.argsort(times)
        t = times[order]
        e = events[order]

        unique_times = np.unique(t[e == 1])
        surv = []
        s = 1.0
        out_times = []

        for ut in unique_times:
            at_risk = np.sum(t >= ut)
            d = np.sum((t == ut) & (e == 1))
            if at_risk > 0:
                s *= (1 - d / at_risk)
            surv.append(s)
            out_times.append(ut)

        return np.array(out_times), np.array(surv)

    all_rows = []
    for trt, sub in df.groupby("trattamento"):
        times = sub["time"].values
        events = sub["event"].values
        t, s = km_estimate(times, events)
        tmp = pd.DataFrame({
            "time": t,
            "surv": s,
            "trattamento": str(trt)
        })
        all_rows.append(tmp)

    km_df = pd.concat(all_rows, ignore_index=True)
    return km_df


def fit_cox(df):
    """
    Modello Cox con statsmodels (PHReg):
    Surv(time, event) ~ trattamento + eta + sesso + aderenza + MMI
    Restituisce: risultato, X, time, event
    """
    df = df.copy()
    df["trattamento"] = df["trattamento"].astype("category")
    df["sesso"] = df["sesso"].astype("category")

    X = pd.get_dummies(df[["trattamento", "sesso"]], drop_first=True)
    X["eta"] = df["eta"]
    X["aderenza"] = df["aderenza"]
    X["MMI"] = df["MMI"]

    time = df["time"].values
    event = df["event"].values

    model = PHReg(time, X, status=event, ties="breslow")
    res = model.fit()

    return res, X, time, event


def baseline_cumulative_hazard_manual(beta, X, time, event):
    """
    Stima H0(t) (baseline cumulative hazard) manualmente
    usando la formula di Breslow.
    """
    time = np.asarray(time)
    event = np.asarray(event)
    X = np.asarray(X)
    beta = np.asarray(beta)

    # tempi di evento unici
    event_mask = (event == 1)
    event_times = np.sort(np.unique(time[event_mask]))

    H0 = []
    t_list = []
    cum_H = 0.0

    exp_Xb = np.exp(X @ beta)

    for tj in event_times:
        # individui a rischio al tempo tj
        at_risk = time >= tj
        denom = exp_Xb[at_risk].sum()

        # numero di eventi a tj
        dj = np.sum((time == tj) & (event == 1))

        if denom > 0:
            dH = dj / denom
        else:
            dH = 0.0

        cum_H += dH
        H0.append(cum_H)
        t_list.append(tj)

    H0 = np.array(H0)
    t_list = np.array(t_list)
    return t_list, H0


def adjusted_curves_df(res, X, time, event, df, landmark_days=365):
    """
    Calcola curve di sopravvivenza aggiustate per trattamento:
    S(t|x*) = exp( -H0(t) * exp(x* beta) )
    dove x* ha:
      - trattamento fissato
      - eta, aderenza, MMI = medie
      - sesso = proporzioni medie
    Restituisce DataFrame (time, surv, trattamento)
    """
    beta = res.params.values
    t0, H0 = baseline_cumulative_hazard_manual(beta, X, time, event)

    cov_names = res.params.index.tolist()

    # medie covariate
    mean_eta = df["eta"].mean()
    mean_adh = df["aderenza"].mean()
    mean_mmi = df["MMI"].mean()

    sesso_cols = [c for c in cov_names if c.startswith("sesso_")]
    sesso_means = {}
    for col in sesso_cols:
        livello = col.replace("sesso_", "")
        sesso_means[col] = (df["sesso"] == livello).mean()

    # quali trattamenti?
    trattamenti = sorted(df["trattamento"].unique())

    all_rows = []

    for trt in trattamenti:
        # costruiamo vettore covariate x*
        x_star = pd.Series(0.0, index=cov_names)

        # dummies trattamento
        for col in cov_names:
            if col.startswith("trattamento_"):
                livello = col.replace("trattamento_", "")
                x_star[col] = 1.0 if trt == livello else 0.0

        # sesso a valori medi
        for col, val in sesso_means.items():
            x_star[col] = val

        # continue
        if "eta" in cov_names:
            x_star["eta"] = mean_eta
        if "aderenza" in cov_names:
            x_star["aderenza"] = mean_adh
        if "MMI" in cov_names:
            x_star["MMI"] = mean_mmi

        xb = float(np.dot(x_star.values, beta))
        exp_xb = np.exp(xb)

        surv = np.exp(-H0 * exp_xb)

        tmp = pd.DataFrame({
            "time": t0,
            "surv": surv,
            "trattamento": str(trt)
        })
        all_rows.append(tmp)

    adj_df = pd.concat(all_rows, ignore_index=True)
    # taglio a landmark_days se serve
    adj_df = adj_df[adj_df["time"] <= landmark_days].copy()
    return adj_df


# ========== STREAMLIT APP ==========

def main():
    st.title("Analisi di sopravvivenza - Ricoveri T2D (Landmark 12 mesi)")
    st.write("Coorte real-world, primo ricovero, confronto tra categorie terapeutiche.")

    uploaded_file = st.file_uploader("Carica il file Excel", type=["xlsx", "xls"])

    if uploaded_file is None:
        st.info("Carica il file .xlsx del tuo dataset.")
        return

    # leggi file
    df = pd.read_excel(uploaded_file)

    st.subheader("Anteprima dati")
    st.write(df.head())

    # parametri
    landmark_days = 365
    global_end_str = "2025-02-28"

    st.subheader("Costruzione coorte + Landmark 12 mesi")
    landmark_df = prepara_coorte(df, global_end_str=global_end_str, landmark_days=landmark_days)

    n_paz = landmark_df["id"].nunique()
    n_eventi = int(landmark_df["event"].sum())

    st.write(f"**Pazienti inclusi (Landmark 12 mesi)**: {n_paz}")
    st.write(f"**Eventi (primo ricovero)**: {n_eventi}")

    st.write("Numero di pazienti per trattamento:")
    st.write(landmark_df.groupby("trattamento")["id"].nunique())

    # ---------- KM NON AGGIUSTATE ----------
    st.subheader("Curve di Kaplan–Meier (non aggiustate)")

    km_df = km_curves_df(landmark_df, landmark_days=landmark_days)

    km_chart = (
        alt.Chart(km_df)
        .mark_line()
        .encode(
            x=alt.X("time", title="Tempo (giorni)"),
            y=alt.Y("surv", title="Probabilità di non essere ricoverato"),
            color=alt.Color("trattamento", title="Trattamento")
        )
        .properties(
            width=700,
            height=400,
            title=f"Kaplan–Meier - Landmark {landmark_days} giorni"
        )
    )

    st.altair_chart(km_chart, use_container_width=True)

    # ---------- COX ----------
    st.subheader("Modello di Cox (aggiustato)")

    res, X, time, event = fit_cox(landmark_df)

    st.text(res.summary().as_text())

    # Tabella HR
    coef = res.params
    se = res.bse
    hr = np.exp(coef)
    hr_low = np.exp(coef - 1.96 * se)
    hr_high = np.exp(coef + 1.96 * se)
    pvals = res.pvalues

    hr_table = pd.DataFrame({
        "covariata": coef.index,
        "HR": hr,
        "HR_lower_95": hr_low,
        "HR_upper_95": hr_high,
        "p_value": pvals
    }).round(3)

    st.write("**Hazard ratio (HR) con IC95%:**")
    st.dataframe(hr_table)

    # ---------- CURVE AGGIUSTATE ----------
    st.subheader("Curve di sopravvivenza aggiustate (Cox)")

    adj_df = adjusted_curves_df(res, X, time, event, landmark_df, landmark_days=landmark_days)

    adj_chart = (
        alt.Chart(adj_df)
        .mark_line()
        .encode(
            x=alt.X("time", title="Tempo (giorni)"),
            y=alt.Y("surv", title="Probabilità di non essere ricoverato (aggiustata)"),
            color=alt.Color("trattamento", title="Trattamento")
        )
        .properties(
            width=700,
            height=400,
            title=f"Curve di sopravvivenza aggiustate (Landmark {landmark_days} giorni)"
        )
    )

    st.altair_chart(adj_chart, use_container_width=True)

    st.markdown("---")
    st.markdown(
        "Modello: Cox PH (statsmodels), endpoint = primo ricovero, "
        "landmark = 12 mesi, aggiustato per età, sesso, aderenza, MMI."
    )


if __name__ == "__main__":
    main()
